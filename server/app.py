"""The Stand's own thin server (F3, F5, F9, F10 wiring).

Deliberately NOT `adk web` / `adk api_server`: those give a generic dev
console with no dial control and no rubric sidebar, and their websocket wire
format isn't a contract worth reverse-engineering for a one-room product UI.
Per adk-docs (Part 1: FastAPI Application Example), driving `Runner.run_live()`
directly from our own FastAPI WebSocket endpoint is the documented, idiomatic
pattern for a custom Bidi-streaming client — same upstream/downstream task
shape as the docs' demo, just with our own JSON message contract instead of
raw `Event` dumps, so the browser only has to understand messages the UI
actually needs (audio, transcript, score, dial, debrief).

Message contract, browser -> server (JSON text frames on /ws/{session_id}):
  {"type": "start", "case_id": "martinez_v_nordbay", "pressure_level": 1, "owner_token": "<uuid, required for uploaded cases>"}
  {"type": "audio", "data": "<base64 pcm16 @16kHz>"}
  {"type": "dial", "level": 1|2|3}
  {"type": "refocus", "focus": "Section 3, methodology"}  -- F19 2b: mid-session focus shift
  {"type": "end_session"}

Message contract, server -> browser:
  {"type": "audio", "data": "<base64 pcm16 @24kHz>"}
  {"type": "transcript", "role": "examiner"|"witness", "text": "...", "partial": bool,
   "replace": bool}
      -- replace=false: "text" is the next delta, append it to the current turn.
         replace=true:  "text" is the FULL corrected text of the current turn,
                        swap the turn's node instead of appending. The Live API
                        re-sends a whole turn with earlier words revised once it
                        has heard the full utterance; a delta cannot express an
                        edit in the middle, which is why appending doubled the
                        transcript on screen (BUG 2).
  {"type": "interrupted"}  -- forwards the Live API's real LlmResponse.interrupted
                              signal (genuine barge-in, not a UI guess)
  {"type": "score", "events": [{"criterion","dxx","triggered","violation","note","score_delta"}]}
  {"type": "focus", "focus": "..."}  -- confirms a "refocus" message was applied
  {"type": "debrief", "amta_score": int, "headline": "...", "moments": [...], "practice_focus": "...", "cost": {...}}
  {"type": "error", "message": "..."}

P1 privacy: an uploaded case (F16) is only ever visible to the browser that
uploaded it. The frontend mints an anonymous `owner_token` once (localStorage)
and resends it on every /api/cases list (X-Owner-Token header), case briefing
(X-Owner-Token header), and WS "start" message. Curated case_files/ cases have
no owner and stay visible to everyone.
"""

import asyncio
import base64
import difflib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from rubric_scorer.debrief import DebriefAgent
from rubric_scorer.scorer import RubricScorer
from server.cost_tracker import CostTracker
from server.firestore_store import SessionStore, UploadedCaseStore
from witness_agent.agent import (
    DISCLAIMER,
    MalformedCaseError,
    ReverseNotAvailableError,
    UnknownCaseError,
    _validate_case,
    build_agent_from_case,
    case_language_code,
    load_case,
    make_agent_for_case,
    reverse_opening_direction,
)
from witness_agent.case_generator import (
    GenerationFailedError,
    UnreadableDocumentError,
    UnsupportedModeError,
    UploadTooLargeError,
    assert_document_has_substance,
    build_case_dict,
    generate_case_content,
)

APP_NAME = "the_stand"
STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("the_stand.app")

# BUG 2 (round 3) diagnostic: dump every raw transcription chunk the Live API
# emits, with the accumulated state around it, so the reconciliation logic can
# be fixed against real chunk shapes instead of a hypothesis. Off by default —
# only the session's own transcript chunks are ever logged here, never the
# uploaded document's content (that stays forbidden by the leitplanken).
TRANSCRIPT_DIAG = os.environ.get("THE_STAND_TRANSCRIPT_DIAG") == "1"


def _diag_chunk(
    session_id: str, role: str, accumulated: str, chunk: str, delta: str, replace: bool
) -> None:
    if not TRANSCRIPT_DIAG:
        return
    logger.warning(
        "TDIAG %s role=%s replace=%s acc_len=%d chunk=%r acc=%r delta=%r",
        session_id,
        role,
        replace,
        len(accumulated),
        chunk,
        accumulated,
        delta,
    )

# F16 mode templates: the curated escalation ladder + rubric an uploaded
# case borrows from — never regenerated per-upload, only the persona's
# document-specific goals/affidavit/summary are generated (see
# witness_agent/case_generator.py). Keys are the two F16-allowed modes.
UPLOAD_MODE_TEMPLATE_CASE_ID = {"defense": "dissertation_defense", "sales": "sales_discovery_call"}

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

session_service = InMemorySessionService()
firestore_store = SessionStore()
uploaded_case_store = UploadedCaseStore()
# In-process cache so an uploaded case is immediately playable even before
# (or if) its Firestore write lands — Firestore persistence is best-effort,
# the in-memory cache is this server instance's source of truth for its
# own lifetime. Cloud Run's single-instance-per-revision default for this
# service keeps this safe for the demo; a multi-instance deploy would need
# Firestore as the read path too (list_cases() already does this on startup
# fallback below).
_uploaded_cases_cache: dict[str, dict] = {}


def _normalize_for_compare(text: str) -> str:
    """Lowercased, punctuation-free, single-spaced form used only to decide
    whether two strings are the same utterance. Never sent anywhere."""
    kept = [ch.lower() if (ch.isalnum() or ch.isspace()) else " " for ch in text]
    return " ".join("".join(kept).split())


# A restatement is a re-send of the WHOLE turn, so it is comparable in length
# to what's accumulated; a genuine incremental delta is a few words against a
# long accumulation. 0.6 is the length floor, 0.82 the similarity floor —
# both measured against real captured chunks (see docs/DIRECTORS_NOTES.md):
# the observed restatement scored 0.9976 similarity at 1.005x length, while
# real deltas score far below both.
_RESTATEMENT_MIN_LEN_RATIO = 0.6
_RESTATEMENT_MIN_SIMILARITY = 0.82
# Below this many normalized characters, only an exact duplicate may count as
# a restatement. A real turn-end restatement is a whole turn (the captured one
# was 612 characters); on a 3-character accumulation the ratio and similarity
# guards are both trivially satisfied, so "the" + " then" was being treated as
# a correction and silently dropped the word "the". Replacing is destructive
# and appending is not, so the tie is broken toward appending.
_RESTATEMENT_MIN_TURN_CHARS = 60


def _is_restatement(accumulated: str, chunk: str) -> bool:
    """True when `chunk` is a corrected re-send of the whole accumulated turn
    rather than the next piece of it."""
    na, nc = _normalize_for_compare(accumulated), _normalize_for_compare(chunk)
    if not na or not nc:
        return False
    if nc == na:
        # The same utterance modulo punctuation/spacing. Safe to treat as a
        # restatement at any length: replacing text with itself loses nothing.
        return True
    if len(na) < _RESTATEMENT_MIN_TURN_CHARS:
        return False
    if nc.startswith(na):
        # A cumulative resend that only differs from `accumulated` in
        # formatting. Note startswith, not `in`: a restatement re-sends the
        # turn from its beginning, whereas `na in nc` also matched a mid-string
        # coincidence ("no" inside " I said no"), which is a delta, not a
        # correction.
        return True
    if len(nc) < _RESTATEMENT_MIN_LEN_RATIO * len(na):
        # Far too short to be a re-send of the whole turn — this is a delta.
        return False
    return (
        difflib.SequenceMatcher(None, na, nc).ratio() >= _RESTATEMENT_MIN_SIMILARITY
    )


def _reconcile_transcript_chunk(accumulated: str, chunk: str) -> tuple[str, str, bool]:
    """Merges a new input_transcription/output_transcription chunk into the
    turn's running text.

    Returns (new_accumulated, text_to_send, replace). When `replace` is True
    the client must REPLACE the current turn's text with `text_to_send`
    instead of appending it (see the transcript message contract at the top
    of this file).

    BUG 2, third attempt — this time driven by captured chunks rather than by
    reasoning about the API. A real Live session (`THE_STAND_TRANSCRIPT_DIAG=1`,
    reverse mode, sales_discovery_call) showed the actual shape that was
    doubling the screen: at turn end the API re-sends the ENTIRE turn as one
    chunk, lightly revised now that it has heard the whole utterance. The
    captured pair was 612 chars accumulated vs. a 615-char final chunk whose
    only difference was one inserted word ("...this quarter, I wanted..." →
    "...this quarter, so I wanted..."). That chunk is not contained in the
    accumulation and does not start with it, so both of the previous rounds'
    checks necessarily missed it and it landed in the `accumulated + chunk`
    branch — printing the whole turn twice. Exactly what the screenshots show
    ("comes around 500k" / "comes in around 500k").

    Hence the four cases below: an exact/substring repeat (emit nothing), a
    clean cumulative resend (emit only the new suffix), a *revised*
    restatement of the same turn (replace the turn, emit the full corrected
    text), and a genuine incremental delta (append)."""
    if not chunk:
        return accumulated, "", False
    if not accumulated:
        # First chunk of a turn. This MUST be a replace, not an append: the
        # server clears `pending_examiner_text`/`current_witness_text` at
        # turn_complete, but the browser has no turn_complete signal, so an
        # append here glued every new question onto the previous ones and the
        # "YOU —" line grew into a run-on for the whole session
        # ("...where were you on the night of the 14th?Can anyone corroborate
        # that?"). That is the second half of BUG 2, and the one the user's
        # Rheinwerk screenshot actually shows ("...wo Sie mir dasHaben sie" —
        # note the missing space, a concatenation seam, not a repetition).
        # Sending replace here makes "server cleared the turn" and "client
        # cleared the turn" the same event instead of two that can drift.
        return chunk, chunk, True
    if chunk in accumulated:
        # Exact repeats, suffix repeats, strict-prefix-shrink resends, and
        # middle-substring resends — all already on screen, emit nothing.
        return accumulated, "", False
    if chunk.startswith(accumulated):
        return chunk, chunk[len(accumulated):], False
    if _is_restatement(accumulated, chunk):
        # The API corrected earlier words. Appending would double the turn;
        # a delta can't express an edit in the middle. Send the whole turn
        # and let the client swap the node.
        return chunk, chunk, True
    return accumulated + chunk, chunk, False


async def _resolve_case(case_id: str) -> dict:
    """Loads a case dict by id from either the static case_files/ (F2) or
    the uploaded-case store/cache (F16). Raises UnknownCaseError if neither
    has it — same failure contract as the static-only load_case()."""
    try:
        return load_case(case_id)
    except UnknownCaseError:
        pass
    if case_id in _uploaded_cases_cache:
        return _uploaded_cases_cache[case_id]
    case = await uploaded_case_store.get_case(case_id)
    if case is None:
        raise UnknownCaseError(f"no case file or uploaded case for case_id '{case_id}'")
    _uploaded_cases_cache[case_id] = case
    return case


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


def _case_summary(case_id: str, case: dict) -> dict:
    language = case.get("language")
    reverse = case.get("reverse")
    return {
        "case_id": case_id,
        "case_name": case["case_name"],
        "display_name": case.get("display_name", case["case_name"]),
        "case_number": case.get("case_number"),
        "case_type": case.get("case_type", "Civil"),
        "display_order": case.get("display_order", 99),
        "card_summary": (case.get("card_summary") or case["summary"]).strip(),
        "summary": case["summary"].strip(),
        "witness_name": case["witness"]["name"],
        "witness_role": case["witness"]["role"],
        "witness_short_role": case["witness"].get("short_role", case["witness"]["role"]),
        "witness_disposition": case["witness"].get("disposition"),
        "language": {"code": language["code"], "name": language["name"]} if language else None,
        "session_verb": case.get("session_verb"),
        "uploaded": bool(case.get("uploaded")),
        # F19 2c: which upload mode generated this case (defense/sales) — the
        # only thing the "Change focus" re-attach flow needs client-side to
        # resubmit to the same template. Owner-scoped like everything else
        # about an uploaded case; never present on curated cases.
        "upload_mode": case.get("upload_mode"),
        # F17
        "user_role": case.get("user_role"),
        # F19
        "focus": case.get("focus"),
        "focus_note": case.get("focus_note"),
        # F18
        "reverse_available": bool(reverse),
        "reverse_short_role": reverse.get("short_role") if reverse else None,
    }


def _case_briefing(case_id: str, case: dict, role: str) -> dict:
    """F17 Case-Briefing-Panel content. Read-only, strictly from the case
    file. Deliberately never includes `witness.goals` / `reverse.goals` —
    those are the hidden playbook (spielleiter-Wissen); leaking them kills
    the sparring value. Forward briefing shows the witness's/committee's
    profile and the user's own role; a `role=reverse` briefing instead shows
    the reverse persona's profile and the user's role in that mode."""
    if role == "reverse":
        reverse = case.get("reverse")
        if not reverse:
            raise ReverseNotAvailableError(f"case '{case_id}' has no reverse mode")
        return {
            "case_id": case_id,
            "case_name": case["case_name"],
            "summary": case["summary"].strip(),
            "user_role": reverse["user_role"],
            "counterpart_role": reverse["role"],
            "counterpart_short_role": reverse["short_role"],
            "counterpart_disposition": reverse.get("disposition"),
            "affidavit": reverse.get("affidavit", ""),
            "focus": case.get("focus"),
            "focus_note": case.get("focus_note"),
            "reverse": True,
        }
    witness = case["witness"]
    return {
        "case_id": case_id,
        "case_name": case["case_name"],
        "summary": case["summary"].strip(),
        "user_role": case.get("user_role"),
        "counterpart_role": witness["role"],
        "counterpart_short_role": witness.get("short_role", witness["role"]),
        "counterpart_disposition": witness.get("disposition"),
        "affidavit": witness["affidavit"],
        "focus": case.get("focus"),
        "focus_note": case.get("focus_note"),
        "reverse": False,
    }


# Uploaded cases accumulate in Firestore across every rehearsal/test upload
# for the life of the project and are never auto-expired (F16's own
# acceptance criterion is that they survive a restart). Left unbounded, the
# case-selection grid grows without limit and the curated demo cases scroll
# out of view — this cap keeps only the most recently uploaded cases visible
# in the grid (older ones stay in Firestore, just not rendered), so a long
# rehearsal history can't push the Cold Open beat off-screen again.
MAX_UPLOADED_CASES_SHOWN = 6


@app.get("/api/cases")
async def list_cases(x_owner_token: str = Header(None)):
    from witness_agent.agent import CASE_FILES

    out = [_case_summary(case_id, load_case(case_id)) for case_id in CASE_FILES]

    # F16: uploaded cases are read from Firestore on every list so a case
    # generated by another server instance (or before a restart) still shows
    # up — the acceptance criterion is "Fallakte überlebt Neustart". Firestore
    # is best-effort (see firestore_store.py): if it's unavailable or the
    # write for a case just made in THIS process hasn't landed yet, fall back
    # to the in-process cache so an upload never silently vanishes from its
    # own server's case grid.
    #
    # P1 privacy fix: an uploaded case only ever appears for the client that
    # owns it — its `owner_token` (see upload_case) must match the caller's
    # `X-Owner-Token` header exactly. A case with no owner_token (only
    # possible from before this fix) is never shown to anyone rather than
    # falling back to the old "visible to every visitor" behavior.
    now = datetime.now(timezone.utc)
    uploaded: list[tuple[datetime, dict]] = []
    seen_ids = set()
    for case in await uploaded_case_store.list_cases():
        case_id = case["case_id"]
        seen_ids.add(case_id)
        _uploaded_cases_cache[case_id] = case
        if not x_owner_token or case.get("owner_token") != x_owner_token:
            continue
        uploaded.append((case.get("created_at") or now, _case_summary(case_id, case)))
    for case_id, case in _uploaded_cases_cache.items():
        if case_id not in seen_ids:
            if not x_owner_token or case.get("owner_token") != x_owner_token:
                continue
            # Cache-only entries were created within this process's lifetime
            # (i.e. very recently) and Firestore hasn't caught up yet — treat
            # as newest so they never disappear behind the cap.
            uploaded.append((now, _case_summary(case_id, case)))

    uploaded.sort(key=lambda t: t[0], reverse=True)
    out.extend(summary for _, summary in uploaded[:MAX_UPLOADED_CASES_SHOWN])

    out.sort(key=lambda c: c["display_order"])
    return {"cases": out, "disclaimer": DISCLAIMER, "upload_modes": list(UPLOAD_MODE_TEMPLATE_CASE_ID)}


@app.post("/api/cases/upload")
async def upload_case(
    mode: str = Form(...),
    file: UploadFile = File(...),
    focus: str = Form(None),
    owner_token: str = Form(None),
    case_id: str = Form(None),
):
    """F16 Bring Your Own Case. Only "defense" and "sales" modes — the legal
    cross-exam mode stays fiction-only (brief Section 8, Rule 1.6/D-28).

    F19: optional `focus` form field — where the user wants to be grilled
    (e.g. "Chapter 4, methodology"). Passed through to generation so the
    goals anchor on that section; cite-or-GAP if the document doesn't
    support it (see `content.focus_note`, carried onto the case).

    P1: `owner_token` is required — a per-browser anonymous id the frontend
    mints once (localStorage) and resends on every upload/list/session-start.
    It becomes the case's visibility key (see list_cases/websocket_endpoint)
    and is never echoed back in `_case_summary`.

    F19 2c: an optional `case_id` re-attaches a document to an *existing*
    uploaded case (re-generating its cited attack lines with a new focus)
    instead of minting a new one — the "Change focus" flow. Requires the
    caller's `owner_token` to match the existing case's owner."""
    if mode not in UPLOAD_MODE_TEMPLATE_CASE_ID:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(UPLOAD_MODE_TEMPLATE_CASE_ID)}")
    owner_token = (owner_token or "").strip()
    if not owner_token:
        raise HTTPException(status_code=400, detail="owner_token is required")

    target_case_id = None
    if case_id:
        try:
            existing = await _resolve_case(case_id)
        except UnknownCaseError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not existing.get("uploaded") or existing.get("owner_token") != owner_token:
            raise HTTPException(status_code=403, detail="not the owner of this case")
        target_case_id = case_id

    file_bytes = await file.read()
    mime_type = file.content_type or "application/pdf"
    # Never log file_bytes or any generated text (leitplanken: "kein Logging
    # des Volltexts") — filename/size/mode/focus only.
    logger.info(
        "upload_case: mode=%s filename=%s bytes=%d focus_set=%s refocus=%s",
        mode, file.filename, len(file_bytes), bool((focus or "").strip()), bool(target_case_id),
    )

    # BUG 1: prove there is something to build a case FROM before the model
    # ever sees the document. An empty/whitespace-only/image-only upload used
    # to sail past the size ceiling straight into generation, and the model
    # invented an entire case — committee, methodology attack lines and all —
    # out of zero bytes, then presented it under a citation rubric. Refuse
    # loudly at the upload card instead; never fall back to a template.
    try:
        assert_document_has_substance(file_bytes, mime_type, file.filename or "")
    except UnreadableDocumentError as exc:
        logger.info("upload_case rejected: unreadable/too-thin document (%s)", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        content = await generate_case_content(mode, file_bytes, mime_type, focus=focus)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GenerationFailedError as exc:
        raise HTTPException(status_code=502, detail=f"couldn't read your case: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Anything the generation call itself throws — an upstream 4xx/5xx
        # from the model API, a transport error, a schema surprise. Without
        # this, such an error propagated raw and the Bring-Your-Own-Case card
        # showed a bare 500 with no explanation, on the demo's second headline
        # feature. The document's content is never logged (leitplanken), only
        # the error type.
        logger.warning("upload_case generation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Couldn't build a case from this document — the generator "
                   "couldn't process it. Try a different file.",
        ) from exc

    template = load_case(UPLOAD_MODE_TEMPLATE_CASE_ID[mode])
    case_id_out = target_case_id or f"uploaded_{uuid.uuid4().hex[:10]}"
    case = build_case_dict(mode, content, template, case_id_out, focus=focus)
    _validate_case(case, case_id_out)  # same schema gate every static case file passes
    case["owner_token"] = owner_token
    case["upload_mode"] = mode

    _uploaded_cases_cache[case_id_out] = case
    await uploaded_case_store.save_case(case_id_out, case)

    return {"case": _case_summary(case_id_out, case)}


@app.delete("/api/cases/{case_id}")
async def delete_uploaded_case(case_id: str, x_owner_token: str = Header(None)):
    """FEATURE 7: let an uploader remove their own uploaded case.

    Until now the only way to get rid of an upload was `server/admin_prune_case`
    from a shell — which is exactly how the 2026-08-30 privacy P1 happened
    (someone else's document sat visible in the grid because nobody could
    remove it from the product). This is the same delete, done properly:

    - Ownership is enforced SERVER-side, not by hiding a button. A curated
      case can never be deleted; an uploaded case can only be deleted by a
      caller presenting its `owner_token`.
    - A wrong/absent token gets the same 404 as an unknown id, so the endpoint
      cannot be used to probe which uploaded case ids exist.
    - Reuses `UploadedCaseStore.delete_case()` — the one deletion path, shared
      with the admin CLI — and additionally drops the in-process cache entry,
      which the CLI cannot reach (a Cloud Run instance would otherwise keep
      serving the deleted case for the rest of its life).
    """
    try:
        case = await _resolve_case(case_id)
    except UnknownCaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    not_found = HTTPException(
        status_code=404, detail=f"no case file or uploaded case for case_id '{case_id}'"
    )
    if not case.get("uploaded"):
        # Curated cases are part of the product, not user data.
        raise HTTPException(status_code=403, detail="this case can't be deleted")
    if not x_owner_token or case.get("owner_token") != x_owner_token:
        raise not_found

    _uploaded_cases_cache.pop(case_id, None)
    await uploaded_case_store.delete_case(case_id)
    logger.info("delete_uploaded_case: %s removed by its owner", case_id)
    return {"deleted": case_id}


@app.get("/api/cases/{case_id}/briefing")
async def case_briefing(case_id: str, role: str = "examiner", x_owner_token: str = Header(None)):
    """F17 Case-Briefing-Panel: read-only case context (parties, the user's
    own role, counterpart profile, affidavit summary, focus). Never includes
    hidden goals/strategy — see `_case_briefing` docstring. Available before
    a session starts (case-selection card) and during it (header toggle).

    P1: an uploaded case's briefing is only readable by its owner (same
    `X-Owner-Token` check as list_cases/websocket_endpoint) — same 404 as an
    unknown case_id, so a guessed id can't be distinguished from a
    nonexistent one."""
    try:
        case = await _resolve_case(case_id)
    except UnknownCaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if case.get("uploaded") and (not x_owner_token or case.get("owner_token") != x_owner_token):
        raise HTTPException(status_code=404, detail=f"no case file or uploaded case for case_id '{case_id}'")
    try:
        return _case_briefing(case_id, case, role)
    except ReverseNotAvailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()

    # Wait for the "start" control message before touching ADK — it carries
    # the case selection (F2) and the dial's starting level (F10).
    try:
        first_raw = await websocket.receive_text()
        first = json.loads(first_raw)
    except (WebSocketDisconnect, json.JSONDecodeError):
        return
    if first.get("type") != "start":
        await websocket.send_text(
            json.dumps({"type": "error", "message": "expected a 'start' message first"})
        )
        return

    case_id = first.get("case_id", "martinez_v_nordbay")
    try:
        pressure_level = int(first.get("pressure_level", 1))
    except (TypeError, ValueError):
        pressure_level = 1
    pressure_level = min(max(pressure_level, 1), 3)
    user_id = "operator"
    # F18: "role": "reverse" swaps in the case's reverse persona instead of
    # the witness. Anything other than the literal "reverse" stays forward
    # mode (the default, same as before this field existed).
    reverse = first.get("role") == "reverse"
    # P1: same owner check as list_cases/case_briefing, carried over the
    # "start" message since a WS handshake has no header/query param a
    # browser fetch would use. A guessed uploaded case_id without the
    # matching token gets the same "unknown case" error a made-up id would
    # — no distinct "found but not yours" signal.
    owner_token = first.get("owner_token")

    try:
        case = await _resolve_case(case_id)
        if case.get("uploaded") and (not owner_token or case.get("owner_token") != owner_token):
            raise UnknownCaseError(f"no case file or uploaded case for case_id '{case_id}'")
        case, agent, stage_direction_for_level = build_agent_from_case(case, reverse=reverse)
        scorer = RubricScorer(case, reverse=reverse)
    except (UnknownCaseError, MalformedCaseError, ReverseNotAvailableError) as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        return
    debrief_agent = DebriefAgent()

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={"pressure_level": pressure_level},
    )
    await firestore_store.start_session(session_id, case_id, pressure_level)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    # F12: a case can declare a witness language (BCP-47 code); wiring it
    # into SpeechConfig makes the Live model's audio actually come back in
    # that language instead of relying on the prompt alone. Omitted for the
    # English-default cases (speech_config=None keeps default behavior).
    language_code = case_language_code(case)
    speech_config = (
        types.SpeechConfig(language_code=language_code) if language_code else None
    )

    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=speech_config,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    live_request_queue = LiveRequestQueue()

    # F18 conversational initiative: Live agents are reactive by default, but
    # reverse mode needs the AI to open the call (it now plays the active
    # seller/candidate). One-time `send_content` trigger queued before
    # run_live starts consuming — the exact mechanism the `dial` handler
    # below already uses mid-session, just fired once at the start instead
    # of on an operator action. `RunConfig.proactivity` is documented
    # native-audio-only and unsupported on the pinned LIVE_MODEL, so this
    # stays the only initiative mechanism (see witness_agent/agent.py
    # reverse_opening_direction docstring).
    if reverse:
        opening_direction = reverse_opening_direction(case)
        if opening_direction:
            live_request_queue.send_content(
                types.Content(role="user", parts=[types.Part(text=opening_direction)])
            )

    # Transcript bookkeeping for the RubricScorer (per-exchange) and the
    # DebriefAgent (whole session) — the scorer needs one examiner question
    # paired with the witness answer that follows it.
    transcript_lines: list[str] = []
    scored_events: list[dict] = []
    pending_examiner_text = ""
    current_witness_text = ""
    cost_tracker = CostTracker()
    # F19 2b: starts as the case's upload-time focus (if any) and can be
    # shifted live via a "refocus" message — carried into the RubricScorer's
    # context and reflected back in the debrief so both stay honest about
    # what the operator actually asked to be pressed on this session.
    active_focus = case.get("focus")

    async def score_and_emit(examiner_q: str, witness_a: str) -> None:
        if not examiner_q.strip() or not witness_a.strip():
            return
        try:
            result = await scorer.score_exchange(examiner_q, witness_a)
        except Exception as exc:  # scorer failure must never break the live session
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"scorer error: {exc}"})
            )
            return
        cost_tracker.add_scorer(result.usage_metadata)
        # FEATURE 2 "Whisper mode": checked before the events-empty early
        # return below — most exchanges trigger zero rubric events but can
        # still carry a whisper suggestion, and the whisper must not be
        # dropped just because there was nothing to score this turn.
        if result.whisper:
            await websocket.send_text(json.dumps({"type": "whisper", "text": result.whisper}))
        if not result.events:
            return
        events_payload = [
            {
                "criterion": e.criterion,
                "dxx": e.dxx,
                "triggered": e.triggered,
                "violation": e.violation,
                "note": e.note,
                "score_delta": e.score_delta,
            }
            for e in result.events
        ]
        scored_events.extend(events_payload)
        await websocket.send_text(json.dumps({"type": "score", "events": events_payload}))
        await firestore_store.append_score_events(session_id, events_payload)

    async def upstream_task() -> None:
        nonlocal pending_examiner_text, active_focus
        audio_chunk_count = 0
        audio_byte_count = 0
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "audio":
                        data = msg.get("data")
                        if not data:
                            continue
                        audio_bytes = base64.b64decode(data)
                        live_request_queue.send_realtime(
                            types.Blob(mime_type="audio/pcm;rate=16000", data=audio_bytes)
                        )
                        audio_chunk_count += 1
                        audio_byte_count += len(audio_bytes)
                        if audio_chunk_count == 1 or audio_chunk_count % 50 == 0:
                            print(
                                f"[{session_id}] upstream audio: {audio_chunk_count} chunks, "
                                f"{audio_byte_count} bytes total (last chunk {len(audio_bytes)} bytes)"
                            )
                    elif mtype == "dial":
                        try:
                            level = int(msg.get("level", 1))
                        except (TypeError, ValueError):
                            level = 1
                        direction = stage_direction_for_level(level)
                        live_request_queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=direction)])
                        )
                    elif mtype == "refocus":
                        # F19 2b: in-session "shift the pressure" — same
                        # send_content mechanism as the dial above, no new
                        # codepath into the live loop and never a generation
                        # call. Ehrlichkeitsgrenze: the stage direction is
                        # deliberately generic (no citation claim) — if the
                        # new focus falls outside the attack lines cited at
                        # upload time, the witness just presses on the topic
                        # in character, and the UI never claims a document
                        # source for it.
                        raw_focus = msg.get("focus")
                        new_focus = raw_focus.strip() if isinstance(raw_focus, str) else ""
                        if new_focus:
                            active_focus = new_focus
                            scorer.set_focus(new_focus)
                            # Adversarial-review hardening: neutralize `]`/`"`
                            # before embedding in the bracket-wrapped stage
                            # direction so a focus value can't break out of
                            # the `[STAGE DIRECTION: ...]` wrapper the model
                            # is told to treat as meta-notation, not dialogue.
                            safe_focus = new_focus.replace("]", ")").replace('"', "'")
                            direction = (
                                f"[STAGE DIRECTION: starting with your very next answer, "
                                f"aggressively steer toward \"{safe_focus}\" — bring it up "
                                f"yourself and press on it for the rest of this session]"
                            )
                            live_request_queue.send_content(
                                types.Content(role="user", parts=[types.Part(text=direction)])
                            )
                            print(f"[{session_id}] refocus applied: \"{new_focus}\"")
                            await websocket.send_text(
                                json.dumps({"type": "focus", "focus": new_focus})
                            )
                    elif mtype == "whisper":
                        # FEATURE 2: toggles the RubricScorer's whisper
                        # addendum on/off for subsequent exchanges — no new
                        # live codepath, no second model call.
                        scorer.set_whisper(bool(msg.get("enabled")))
                    elif mtype == "end_session":
                        break
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    # A malformed frame (missing field, bad base64, non-numeric
                    # level) must never kill the live session — report it and
                    # keep listening for the next frame instead.
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": f"bad message: {exc}"})
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            live_request_queue.close()

    async def downstream_task() -> None:
        nonlocal current_witness_text, pending_examiner_text
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            if event.usage_metadata:
                cost_tracker.add_witness(event.usage_metadata)

            # Real Live-API barge-in signal (LlmResponse.interrupted), not a
            # UI guess: the model actually stopped mid-turn because the user
            # started talking over it. Forwarded as its own message so the
            # UI's "witness yields" status is never shown without this.
            if event.interrupted:
                await websocket.send_text(json.dumps({"type": "interrupted"}))

            if event.input_transcription and event.input_transcription.text:
                _prev = pending_examiner_text
                pending_examiner_text, delta, replace = _reconcile_transcript_chunk(
                    pending_examiner_text, event.input_transcription.text
                )
                _diag_chunk(
                    session_id, "examiner", _prev, event.input_transcription.text, delta, replace
                )
                if delta:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "transcript",
                                "role": "examiner",
                                "text": delta,
                                "partial": True,
                                "replace": replace,
                            }
                        )
                    )

            if event.output_transcription and event.output_transcription.text:
                _prev = current_witness_text
                current_witness_text, delta, replace = _reconcile_transcript_chunk(
                    current_witness_text, event.output_transcription.text
                )
                _diag_chunk(
                    session_id, "witness", _prev, event.output_transcription.text, delta, replace
                )
                if delta:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "transcript",
                                "role": "witness",
                                "text": delta,
                                "partial": True,
                                "replace": replace,
                            }
                        )
                    )

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.inline_data and part.inline_data.data:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "audio",
                                    "data": base64.b64encode(part.inline_data.data).decode("ascii"),
                                }
                            )
                        )

            if TRANSCRIPT_DIAG and (event.turn_complete or event.interrupted):
                logger.warning(
                    "TDIAG %s turn_complete=%s interrupted=%s wit_len=%d",
                    session_id, event.turn_complete, event.interrupted, len(current_witness_text),
                )
            if event.turn_complete and current_witness_text:
                new_lines = [
                    f"Examiner: {pending_examiner_text}",
                    f"Witness: {current_witness_text}",
                ]
                transcript_lines.extend(new_lines)
                asyncio.create_task(firestore_store.append_transcript(session_id, new_lines))
                asyncio.create_task(
                    score_and_emit(pending_examiner_text, current_witness_text)
                )
                pending_examiner_text = ""
                current_witness_text = ""

        # Live connection ended (queue closed) — nothing more to stream.

    # Session shutdown. This used to be a plain
    # `asyncio.gather(upstream_task(), downstream_task())`, which never
    # returned: `upstream_task` exits on `end_session` and closes the
    # LiveRequestQueue, but closing the queue does NOT terminate the
    # `run_live()` async generator that `downstream_task` is iterating, so the
    # gather waited forever and the `finally` below — where the debrief is
    # built and sent — was simply unreachable. Measured, not guessed: a log
    # line at the top of that `finally` never appeared across seven sessions.
    #
    # So we no longer wait for the downstream side to end on its own. We wait
    # for upstream (which returns the moment the user ends the session), close
    # the queue, give the stream a short grace period to drain, and then cancel
    # it. Nothing here touches audio handling, RunConfig or SpeechConfig — only
    # the order in which the two tasks are shut down.
    down = asyncio.create_task(downstream_task())
    try:
        await upstream_task()
    except Exception:
        logger.exception("[%s] upstream task failed", session_id)
    finally:
        live_request_queue.close()
        try:
            # Grace period: let anything already in flight finish arriving.
            await asyncio.wait_for(asyncio.shield(down), timeout=2.0)
        except asyncio.TimeoutError:
            down.cancel()
        except Exception:
            logger.exception("[%s] downstream task failed", session_id)
        try:
            await down
        except (asyncio.CancelledError, Exception):
            pass
        logger.warning("[%s] session loop returned, building debrief", session_id)
        try:
            transcript = "\n".join(transcript_lines) or "(no exchanges recorded)"
            debrief = await debrief_agent.build(transcript, scored_events, focus=active_focus)
            cost_tracker.add_debrief(debrief.usage_metadata)
            debrief_payload = {
                "amta_score": debrief.amta_score,
                "headline": debrief.headline,
                "moments": [
                    {
                        "excerpt": m.excerpt,
                        "why_it_matters": m.why_it_matters,
                        "dxx": m.dxx,
                    }
                    for m in debrief.moments
                ],
                "practice_focus": debrief.practice_focus,
                "cost": cost_tracker.as_payload(),
                # F19 2b: the focus actually in effect at session end (the
                # upload-time one, or whatever a mid-session refocus shifted
                # it to) — reflected back so "You asked to be pressed on: X"
                # is never a stale claim.
                "focus": active_focus,
                # FEATURE 1: the same deduplicated transcript_lines the scorer
                # and this debrief were built from, handed to the client for
                # "Download transcript" — one source of truth, so the
                # downloaded file can never disagree with what was scored.
                "transcript_lines": transcript_lines,
                "role": "reverse" if reverse else "normal",
                "case_name": case.get("case_name", case_id),
            }
            await websocket.send_text(json.dumps({"type": "debrief", **debrief_payload}))
            await firestore_store.save_debrief(session_id, debrief_payload)
        except Exception:
            # This used to be a bare `pass`: if the debrief failed for any
            # reason the browser was left in the room forever with no message
            # and no way to know the session had ended. Log it, and tell the
            # client something went wrong so it can say so.
            logger.exception("[%s] debrief build/send failed", session_id)
            try:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "The debrief couldn't be built for this session."})
                )
            except Exception:
                pass
