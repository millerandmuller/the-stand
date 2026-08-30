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
  {"type": "start", "case_id": "martinez_v_nordbay", "pressure_level": 1}
  {"type": "audio", "data": "<base64 pcm16 @16kHz>"}
  {"type": "dial", "level": 1|2|3}
  {"type": "end_session"}

Message contract, server -> browser:
  {"type": "audio", "data": "<base64 pcm16 @24kHz>"}
  {"type": "transcript", "role": "examiner"|"witness", "text": "...", "partial": bool}
  {"type": "interrupted"}  -- forwards the Live API's real LlmResponse.interrupted
                              signal (genuine barge-in, not a UI guess)
  {"type": "score", "events": [{"criterion","dxx","triggered","violation","note","score_delta"}]}
  {"type": "debrief", "amta_score": int, "headline": "...", "moments": [...], "practice_focus": "...", "cost": {...}}
  {"type": "error", "message": "..."}
"""

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
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
    UnsupportedModeError,
    UploadTooLargeError,
    build_case_dict,
    generate_case_content,
)

APP_NAME = "the_stand"
STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("the_stand.app")

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


@app.get("/api/cases")
async def list_cases():
    from witness_agent.agent import CASE_FILES

    out = [_case_summary(case_id, load_case(case_id)) for case_id in CASE_FILES]

    # F16: uploaded cases are read from Firestore on every list so a case
    # generated by another server instance (or before a restart) still shows
    # up — the acceptance criterion is "Fallakte überlebt Neustart". Firestore
    # is best-effort (see firestore_store.py): if it's unavailable or the
    # write for a case just made in THIS process hasn't landed yet, fall back
    # to the in-process cache so an upload never silently vanishes from its
    # own server's case grid.
    seen_ids = set()
    for case in await uploaded_case_store.list_cases():
        case_id = case["case_id"]
        seen_ids.add(case_id)
        _uploaded_cases_cache[case_id] = case
        out.append(_case_summary(case_id, case))
    for case_id, case in _uploaded_cases_cache.items():
        if case_id not in seen_ids:
            out.append(_case_summary(case_id, case))

    out.sort(key=lambda c: c["display_order"])
    return {"cases": out, "disclaimer": DISCLAIMER, "upload_modes": list(UPLOAD_MODE_TEMPLATE_CASE_ID)}


@app.post("/api/cases/upload")
async def upload_case(mode: str = Form(...), file: UploadFile = File(...), focus: str = Form(None)):
    """F16 Bring Your Own Case. Only "defense" and "sales" modes — the legal
    cross-exam mode stays fiction-only (brief Section 8, Rule 1.6/D-28).

    F19: optional `focus` form field — where the user wants to be grilled
    (e.g. "Chapter 4, methodology"). Passed through to generation so the
    goals anchor on that section; cite-or-GAP if the document doesn't
    support it (see `content.focus_note`, carried onto the case)."""
    if mode not in UPLOAD_MODE_TEMPLATE_CASE_ID:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(UPLOAD_MODE_TEMPLATE_CASE_ID)}")

    file_bytes = await file.read()
    mime_type = file.content_type or "application/pdf"
    # Never log file_bytes or any generated text (leitplanken: "kein Logging
    # des Volltexts") — filename/size/mode/focus only.
    logger.info(
        "upload_case: mode=%s filename=%s bytes=%d focus_set=%s",
        mode, file.filename, len(file_bytes), bool((focus or "").strip()),
    )

    try:
        content = await generate_case_content(mode, file_bytes, mime_type, focus=focus)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GenerationFailedError as exc:
        raise HTTPException(status_code=502, detail=f"couldn't read your case: {exc}") from exc

    template = load_case(UPLOAD_MODE_TEMPLATE_CASE_ID[mode])
    case_id = f"uploaded_{uuid.uuid4().hex[:10]}"
    case = build_case_dict(mode, content, template, case_id, focus=focus)
    _validate_case(case, case_id)  # same schema gate every static case file passes

    _uploaded_cases_cache[case_id] = case
    await uploaded_case_store.save_case(case_id, case)

    return {"case": _case_summary(case_id, case)}


@app.get("/api/cases/{case_id}/briefing")
async def case_briefing(case_id: str, role: str = "examiner"):
    """F17 Case-Briefing-Panel: read-only case context (parties, the user's
    own role, counterpart profile, affidavit summary, focus). Never includes
    hidden goals/strategy — see `_case_briefing` docstring. Available before
    a session starts (case-selection card) and during it (header toggle)."""
    try:
        case = await _resolve_case(case_id)
    except UnknownCaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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

    try:
        case = await _resolve_case(case_id)
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
        nonlocal pending_examiner_text
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
                text = event.input_transcription.text
                pending_examiner_text += text
                await websocket.send_text(
                    json.dumps({"type": "transcript", "role": "examiner", "text": text, "partial": True})
                )

            if event.output_transcription and event.output_transcription.text:
                current_witness_text += event.output_transcription.text
                await websocket.send_text(
                    json.dumps(
                        {"type": "transcript", "role": "witness", "text": event.output_transcription.text, "partial": True}
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

    try:
        await asyncio.gather(upstream_task(), downstream_task(), return_exceptions=True)
    finally:
        live_request_queue.close()
        try:
            transcript = "\n".join(transcript_lines) or "(no exchanges recorded)"
            debrief = await debrief_agent.build(transcript, scored_events, focus=case.get("focus"))
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
            }
            await websocket.send_text(json.dumps({"type": "debrief", **debrief_payload}))
            await firestore_store.save_debrief(session_id, debrief_payload)
        except Exception:
            pass
