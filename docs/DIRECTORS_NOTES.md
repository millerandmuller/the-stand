# Director's Notes — Round 1 (Creative Review Fixes)

> Source: `creative_review.md` (JURY-VERDICT: FIX-THEN-SUBMIT, 2026-08-29), Fix 3. For `/academy-submit` to fold into `demo_script.md` and the shot list.

## Cut timing
- Pull the interruption moment ("Oh!"-Moment, mid-sentence witness cut-off → in-character answer) as early as possible in Beat 3 (Brief 1.6). Current rehearsal lands it ~1:10 — target is *before* the 1:00 mark. [W-20]
- This is the single moment the video is built around; every other beat can flex around it, not the reverse.

## Thumbnail (300×300, Devpost)
- Show the room (the witness stand / cross-exam view) or the rubric sidebar.
- Do **not** use an architecture diagram — that's the wrong register for the 5-second thumbnail test (save the diagram for the Close beat / repo asset).

## Rubric sidebar polish (optional, time permitting)
- Aesthetics is currently 7/8 (Mike Swift, 6D diagnosis) — "clean but generically clean."
- If time remains after the demo-critical path is locked: nudge the sidebar's typography toward more "verdict weight" (heavier rule lines, a more judicial type scale). This is a finish pass, not a rebuild — no layout changes.

# Round 2 (UI Redesign + German Sales Case)

> Source: `creative_prompts_round_2.md` (Prompt 4 + 5), design canvas `design/the-stand/*.dc.html`.

## German case (`discovery_call_de`, Britta Vogel / Rheinwerk Logistik) — voice QA

- **Functionally verified (agent, not ear):** `case_language_code()` resolves `de-DE` correctly for this case, `make_agent_for_case("discovery_call_de")` builds a working `LlmAgent` bound to the German persona, and a synthetic-audio WS session against it connects cleanly (no `1008`, no server error, upstream audio chunks flow) — same regression check run against the redesigned UI for all 5 cases.
- **Not verified by any agent in this pipeline: how the German actually *sounds*.** No agent in this project can hear audio. A synthetic sine-tone probe proves the pipe is open, not that `gemini-3.1-flash-live-preview` produces natural, accent-free German speech.
- **Before this case goes anywhere near the demo video:** a human needs to run one real session against Rheinwerk Logistik and listen. If the German voice quality is weak (robotic, mispronounced, or clearly non-native-sounding), ship the case anyway (it's a legitimate F12 feature and passes every functional check) but leave it out of the recorded demo — this note is the required documentation for that decision, per Prompt 5's acceptance criteria. If it sounds good, Beat 6 (Vision) can use it as a second language-swap beat alongside the existing Spanish Lopez case.

# Round 3 (Dissertation Defense + Bring Your Own Case)

> Source: `creative_prompts_round_2.md` (Prompt 6 + 7). This is the round the prompt file itself marks as the **final** feature scope — no further Build→Examine→Revise cycle should add new features after this one lands and passes re-review.

## Dissertation defense (`dissertation_defense.yaml`) — rubric honesty

- Rubric research was done live (WebSearch + WebFetch, actually reading fetched university PDFs, not from memory) against Andrews University, University of Rochester, and SMU Ph.D. Program in Clinical Psychology's published dissertation-defense evaluation rubrics. 4 of 5 rubric criteria are direct paraphrases of a verbatim-quoted line from a real, fetched `.edu` PDF (URLs + quotes are in the case file itself).
- The 5th criterion ("stays factual and composed under an adversarial question") has **no citable published rubric line** in any of the three sources actually read. Marked `"Common defense-prep practice, uncited"` in the case file and called out in `README.md`'s new F14-F16 section, per the prompt's own explicit escape hatch for this exact situation — not invented as if sourced.
- Functionally verified with a real (not synthetic) `RubricScorer` call against the live Gemini API: an exchange about methodology choice correctly triggered `DEF-02` with an honest, non-templated note. `make_agent_for_case("dissertation_defense")` builds successfully; escalation levels 1-3 (wohlwollend → bohrend → feindselig-forensisch) all present and validated.

## Bring Your Own Case (F16) — real end-to-end proof, not just unit tests

- Ran the actual upload flow against a running local server with a real (non-fictional-legal) test document — a short synthetic ML-paper excerpt with page-cited claims — through both `POST /api/cases/upload?mode=defense` and `mode=sales`. Both completed in well under a minute against the live Gemini API and returned a playable, schema-valid case (`_validate_case` passes) whose `card_summary`/`summary` were genuinely grounded in the uploaded text's specific content (drift-reweighted FedAvg, CIFAR-10, Byzantine-client limitation) — not generic filler.
- **Found and fixed a real bug during this verification:** `/api/cases` only ever read uploaded cases from Firestore, never the in-process cache — so a freshly uploaded case could vanish from the very next case-list call on the *same* server that just generated it. Fixed by having `/api/cases` fall back to the in-process cache for any uploaded `case_id` Firestore's listing didn't return.
- **Correction — the fallback above was masking a second, more serious bug.** "Firestore reachable/writable on Cloud Run" was an assumption, not something actually checked at the time. `gcloud logging read` on the live service showed every single upload's Firestore write failing with `One or more components is not a string or is empty` — the in-process-cache fallback made the case *look* persisted (served by the same warm instance) when it actually wasn't landing in Firestore at all, on Cloud Run or locally. Root cause: PyYAML parses a case's `escalation:` block into a dict with integer keys, which `google-cloud-firestore` can't serialize as a map field. Fixed with key-stringifying/de-stringifying helpers in `server/firestore_store.py`. Re-verified three independent ways that don't trust the app's own cache: no more error in Cloud Run logs after a fresh upload, a direct Firestore client read (bypassing the app) confirms the document exists, and it survives the next `/api/cases` call. Deployed as `the-stand-00010-f9m`. Full writeup: `revision_log.md`'s Round 5 section, finding #34.
- Legal cross-exam mode confirmed absent from `upload_modes` (`{"defense", "sales"}` only) — the brief Section 8 confidentiality boundary holds.
- Not verified by any agent: what the generated persona *sounds* like reciting a citation live, or a real human's judgment of whether the generated attack lines feel "smart" rather than merely schema-valid — same category of gap as every other voice-quality note in this file. Worth one real human upload+session test (own dissertation or a product doc) before this goes in the demo video, per Prompt 7's own "Demo-Vorbereitung" note about pre-uploading Lutfiya's dissertation before the shoot.

# Round 4 (Repo-Grün, Cold-Start, Replay, Reverse-Beat, Antechamber)

> Source: `creative_prompts_round_3.md`, Prompts 1–4, plus `build-prompts/f24-antechamber.md` (F24, added mid-round).

## F20 — Repo-Grün + Judge-De-Flake

- `pytest -q` now runs `tests/test_pressure_dial.py::test_live_dial_mechanism` for real (added `pytest.ini` with `asyncio_mode = auto` + `pytest-asyncio` as a dev dependency in `requirements-dev.txt`, previously the async test silently errored out of collection). 7/7 passing, verified locally against the real Gemini Live connection.
- Two empty `<main>` landmarks (`roomView`, `debriefView` — both `hidden` by default, only one view is ever the actual document's main content) changed to `<div>`; `caseSelectView` keeps the one real `<main>`. No JS or CSS referenced the tag name, only element ids — zero behavior change.
- Favicon added as an inline data-URI SVG (small gold gavel-and-scales glyph matching the accent color) — no more console 404.
- `rubric_scorer/trajectory_judge.py`'s dxx-citation matcher now strips bracket wrapping (`[[D-01]]`, `[D-01]`) before matching against the rubric's bare `D-01` ids — see `directives/common_issues.md`'s 2026-08-29 entry, now marked fixed. `python eval/run_eval.py` → `OVERALL: PASS`, verified 2x consecutive plus the run that produced the F22 replay bundle (3rd).
- `UploadedCaseStore.delete_case` (already non-routed) now has a real invocation path: `python -m server.admin_prune_case <case_id>` — used for the Prompt 6 grid-hygiene step (deleting the stale `uploaded_cf7074ea6e`).

## F21 — Cold-Start

- Cloud Run service `the-stand`: `min-instances=1` set (`gcloud run services update the-stand --min-instances=1 --project=the-stand-2026 --region=europe-west1`), applied with explicit user sign-off given the ongoing cost. Cold curl after the change: 0.15s.
- **Superseded by F24's cold-test gate, not yet rolled back:** per the F24 acceptance criterion, `min-instances` only goes back to `0` after the antechamber's own cold-test passes 3x reproduced (service genuinely cold → open antechamber → ~15s → launch responds <1s). Until that's run, both `min-instances=1` and the antechamber stay active in parallel — no window without a net.

## F22 — "Watch a replayed session"

- Built via `eval/build_replay_bundle.py`, run once against a real `adk eval` invocation of `eval/eval_sets/live_audio_witness/` (see `eval/run_eval.py`'s docstring item 6 for how that run is produced). Extracts the witness's real Gemini Live output-audio chunks (base64 is URL-safe/unpadded — needed a custom decode step, `_b64decode`, not stdlib `base64.b64decode`), concatenates them into `server/static/replay/session_audio.wav`, and runs the transcript through the **same `RubricScorer` the live app uses** (offline, against the recorded Q&A pairs) to get real `[D-xx]`-cited score events — nothing in the bundle is invented, every audio byte and every citation traces back to the actual recorded session.
- Frontend (`server/static/replay.js`) is fully self-contained: does not touch `app.js`, the WS handshake, mic code, or `run_live` — confirmed by `git diff --stat` showing zero changes to those files this round.
- **Honesty-boundary note (important for the demo script):** this recorded session is a clean, linear Q&A exchange that ends with "Nothing further, Your Honor" — it does **not** contain a genuine interruption/barge-in event. The brief's replay acceptance criterion mentions the interruption moment as one of the sidebar states to reproduce; fabricating one here would cross the honesty boundary ("nichts wird als live behauptet" / never inventing evidence), so the replay only shows what actually happened in this session. If an interruption beat is wanted in the replayed artifact specifically (as opposed to the live demo, which already has one per the Hero Moment), it needs a fresh `adk eval` recording engineered to include a real barge-in — out of scope for this round, flagged as a GAP rather than silently worked around.
- **Verification gap (needs a human or a different environment):** this session's Chrome automation sandbox cannot decode *any* audio — confirmed with a trivial 1-sample data-URI WAV (`loadedmetadata`/`canplay` never fire, `readyState` stays 0 indefinitely) even via a blob URL with zero network involved. Everything server-side and file-level was verified directly instead: the WAV is a valid RIFF/PCM16/24kHz file (`file` command), the real FastAPI/Starlette static server returns correct `206 Partial Content` for Range requests (a plain `python -m http.server` does not, and was ruled out as the cause), a raw `fetch()` in the same sandboxed page downloads the full file successfully, and the transcript/sidebar sync logic was verified by manually driving `renderTurnUpTo`'s render path with the real bundle JSON (produces the correct running score and score-line count). **What's not yet verified: that clicking play in a real browser with working audio actually hears the witness and sees the sidebar advance in sync with real `timeupdate` events.** This is the Examiner's/human's job on this round's demo-rehearsal pass — do not treat the above as a substitute for one real hard-refresh, mic-permission-free play-through on a normal machine.

## F23 — Reverse-Beat in the Drehbuch

- Brief Section 1.6 (Vision beat) and this file updated to show a 10–15s role-swap using F18's existing Reverse Case mechanism: the AI opens the sales pitch unprompted, the sidebar visibly switches from scoring the user to annotating the AI's own techniques with citations where available. Narration line: "Now switch sides — the AI sells, and the sidebar names every technique it uses on you." Total Vision beat window (2:45–3:20) unchanged; the reverse beat is a sub-segment near the end of it, not an extension. Plan B (a declared recording) still applies to this beat like every other live segment.
- No code changes — F18 (Reverse Case) already ships and passed its own round. This is a documentation/direction change only.

## F24 — Antechamber (Firebase Hosting)

- Static landing page (`product/hosting/index.html`) deployed to Firebase Hosting at `https://the-stand-2026.web.app` (same GCP project as Cloud Run, `firebase.json` + `.firebaserc` added to the repo). Fires an invisible `fetch(..., {mode: "no-cors"})` warm-up ping at the Cloud Run URL on load; the "Take the stand" launch button stays disabled ("Waking the room…") until that resolves (or a 20s timeout elapses unconditionally, so a juror is never locked out). Launch is a real `window.location.href` navigation to the Cloud Run URL — never an iframe, since microphone permissions inside iframes are unreliable across browsers (notably Safari).
- Verified live against the real deployed page and the real Cloud Run URL (not a stub): the warm-up ping resolves and the button arms correctly.
- **Cold-test acceptance criterion (3x reproduced, service genuinely cold) is not yet run** — that's a human step, same category as the audio-playback gap above. `min-instances=0` should not be applied until that's done (see F21 note).
- Devpost URL should point at the antechamber; the direct Cloud Run URL stays in `README.md`'s try-it-out section per the brief.

# Round 5 (Upload Privacy, Upload UX)

> Source: manual test session on the deployed Cloud Run URL, 2026-08-30 evening. Findings: an uploaded case (including a real document, Lutfiya's dissertation) was visible to every visitor of the public URL, the upload focus field went unnoticed, and reverse mode silently disappeared on an older-schema uploaded case.

## Upload privacy (P1)

- Root cause: `/api/cases` and `GET /api/cases/{id}/briefing` served every uploaded case in Firestore to every caller — no ownership concept existed at all. Verified live from an outside network before the fix (the reported bug), and the exact same case was confirmed gone from a token-less client's view after.
- Fixed with an anonymous per-browser `owner_token` (frontend mints one via `crypto.randomUUID()`, stored in `localStorage` behind a try/catch so a blocked/private-mode store degrades to "works this tab, doesn't survive reload" instead of failing the upload). Sent as the `owner_token` form field on upload, the `X-Owner-Token` header on `/api/cases` and the briefing endpoint, and an `owner_token` field on the WebSocket `start` message. Server stores it on the case dict, never returns it in `_case_summary`, and a case with no `owner_token` (only possible pre-fix) is hidden from everyone rather than falling back to the old "visible to all" behavior.
- Starting a session on an uploaded `case_id` with a missing/wrong token gets the same generic "unknown case" WS error a made-up id would — no "found but not yours" signal to probe with.
- **Found and fixed a second, independent bug during verification**, not in the original report: `_to_firestore_case`/`_from_firestore_case` (the int-key stringify/destringify helpers from the Round 4 Firestore fix) only ever handled `witness.escalation`, never `reverse.escalation` — so every upload of a case whose template declares reverse mode (both current templates do) was *still* silently failing its Firestore write, the same failure signature as the bug F20 supposedly closed. Fixed by applying the same key-round-trip to `case["reverse"]["escalation"]`. Re-verified with a live upload + a direct Firestore read outside the app: document lands, `reverse.escalation` keys come back as ints.
- Pruned the reported case (`uploaded_cf7074ea6e`, `the-stand-2026` Firestore project — not the project this shell's default `gcloud` config pointed at, worth flagging for whoever runs `admin_prune_case.py` next) plus two other pre-fix uploads that had no `owner_token` and were therefore unreachable by anyone going forward anyway.
- **Demo implication:** an upload now lives in the browser tab that made it. Beat 5 (Lutfiya's fresh upload) must happen on the same machine/browser used for the recording — re-upload takes under a minute if the token is ever lost (private window, cleared site data), an accepted and honestly-stated trade-off, not a bug.

## Upload UX (P2)

- **Focus step (2a):** the focus field moved out from under the mode buttons — it's now its own step, shown only after a file is actually chosen ("Where should the committee press you?", Skip/Start buttons), matching what the tester actually looked for and when.
- **In-session refocus (2b):** a "Shift the pressure: ___" input next to the pressure dial, using the exact same `send_content` stage-direction mechanism the dial already uses — no new codepath into the live loop, no generation call during a live session. The active focus is also handed to the `RubricScorer`'s prompt as context (never as a citation source) and echoed back at the end in the debrief ("You asked to be pressed on: ..."). Honesty boundary: the stage direction is deliberately generic ("press specifically on X") — if the shifted focus falls outside what was cited at upload time, the witness presses on the topic in character and the UI never claims a document source for it.
- **Change focus between sessions (2c):** a "Change focus" link on an uploaded case's card re-attaches the file (document text was never stored server-side, so re-generation needs the file again) and regenerates the cited attack lines against the new focus, in place — same `case_id`, so it doesn't fork into a duplicate grid entry. Gated by the same `owner_token` check as everything else uploaded.
- **Reverse-toggle hardening (Fix 3):** an uploaded case built before reverse mode existed (old schema, no `reverse` block) now shows an honest "re-upload to unlock the other chair" label instead of the button silently not appearing. The toggle itself already renders on the selection card before any session starts (`card-actions`, no CSS gating it behind a breakpoint) — confirmed at both a desktop width and a narrow (390px) viewport.

## Regression

- `pytest -q` — 7/7 passing, unchanged from Round 4.
- Curated case list (6 cases), `/api/cases/{id}/briefing` for curated cases, and the F22 replay bundle were not touched by this round's diff and were re-checked after: identical behavior.
- Deployed to Cloud Run (`the-stand`, `the-stand-2026`, `europe-west1`) and re-verified live: a token-less client sees only the 6 curated cases and gets a clean WS error on a guessed uploaded id; an uploader's own upload (with a focus set) shows up with the focus in its briefing panel and the reverse toggle, both before the session starts.

# Round 6 (Real Mic-Test Findings + UI Repositioning)

> Source: real microphone test 2026-08-30 evening against `the-stand-00016-hn6`, `build-prompts/fixes-mic-round-2.md` + `build-prompts/ui-repositioning.md`. Voice-pipeline server core (`run_live`, audio up/downstream, `SpeechConfig`) untouched per the fix order — every change below is in the client, the stage-direction/instruction text, or the RubricScorer prompt.

## BUG 1 — In-session refocus ("Shift") not landing

- Root cause was the instruction text, not the wiring: the `[STAGE DIRECTION: ...]` handling in `witness_agent/agent.py` only ever described a *demeanor* note ("adjust your pressure level"), so the model had no instruction telling it a stage direction could also mean "steer the topic" — the refocus frame reached the server and `LiveRequestQueue` correctly the whole time (confirmed via added `print(f"[{session_id}] refocus applied: ...")` diagnostic logging in `server/app.py`).
- Fixed by generalizing the "Stage directions" section in both the forward and reverse instruction builders to name a second kind of note explicitly, and by sharpening the refocus message itself to `"starting with your very next answer, aggressively steer toward \"<focus>\" — bring it up yourself..."` (was: a softer "the examiner wants the questioning shifted — press specifically on ...").
- UI confirmation (`refocusStatus` → "Pressing on: X") already existed from Round 5's 2b work and was left as-is — it was never the missing piece.
- Not verified by ear this round (same category of gap as every voice-quality note above): the fix is verified at the prompt-and-wiring level (instruction text change, confirmed frame delivery via logs) and by the Examiner's synthetic-session probes. A real human re-run of the original EN/DE/reverse mic-test scenario is the next honest verification step before this goes in the demo.

## BUG 2 — Doubled transcript lines

- Root cause: the Live API's `output_transcription`/`input_transcription` chunks are not always pure incremental deltas — the naive `+=` concatenation in `server/app.py` could double a turn's text when a cumulative or exact-repeat chunk arrived (most visibly at turn end).
- Fixed with a `_reconcile_transcript_chunk()` helper (`server/app.py`) that detects three cases per chunk — exact/suffix repeat (emit nothing), cumulative resend (replace, emit only the new suffix), genuine delta (append, emit as-is) — applied to both `pending_examiner_text` and `current_witness_text`. The client's own `+=` accumulation (`app.js`) needed no change once the server only ever sends genuine deltas.
- Debrief/scorer consistency: `transcript_lines` (fed to both the `RubricScorer` and the `DebriefAgent`) is built from the same reconciled text, so the fix closes the display bug and the scoring-input bug with one change, not two.

## BUG 3 — "End session" didn't silence audio immediately

- Fixed client-side only (`server/static/app.js`): every scheduled `AudioBufferSourceNode` is now tracked in `activeAudioSources`; `endBtn` (and the new Back-button path) call `stopAllAudioImmediately()`, which calls `.stop()` on all of them synchronously before the `end_session` WS message is even sent. A `sessionEnded` flag also blocks any audio frame still in flight from starting playback after the click.

## FEATURE 1 — Download transcript

- Debrief payload (`server/app.py`) now includes the same deduplicated `transcript_lines` used for scoring, plus `role` and `case_name` — no new endpoint. Client (`app.js`) builds a Markdown file client-side (`buildTranscriptFile`/`downloadTranscript`) with case name, date, role, full transcript, every score/technique event with its `[D-xx]` citation and timestamp, and the debrief (score, moments, practice focus), triggered via a `Blob` + temporary `<a>` download.
- **GAP (documented, not built this round):** audio download is explicitly out of scope — MediaRecorder-mixing microphone + playback into one recording is real infrastructure work, deferred to post-hackathon. The transcript download honestly covers the core need ("the discussion isn't lost") without overclaiming a recording feature that doesn't exist.

## FEATURE 2 — Whisper mode

- Implemented inside the existing per-exchange `RubricScorer` call — no second model call, no new live codepath. `rubric_scorer/scorer.py` adds an opt-in prompt addendum (`_WHISPER_ADDENDUM_FORWARD`/`_REVERSE`) and an optional `whisper` field on the response schema, only added to the schema when the toggle is on (`_response_schema()`), so the model isn't even asked for it when the room is silent. Toggled per-session via a new `{"type": "whisper", "enabled": bool}` WS message; the header toggle (`#whisperToggle`, next to "Case file" — position per the user's screenshot) defaults OFF every session, matching leitplanke (a).
- Rendered as a quiet, dashed-border-topped line under the transcript with a `"counsel's whisper — "` label prefix (CSS `::before`), deliberately not a chat bubble — cleared on every new examiner turn so a whisper is never shown as stale advice for the wrong exchange.
- **Story-Bonus:** "leads the way when you're stuck" is close to literal Collaborative-Partner-track language — a candidate sentence for the writeup/video, not yet applied there (submission-phase work).

## UI — Back button + three case sections + repositioning

- **Back-button fix:** a `popstate`-trapping pattern (`pushAppState()` called on load, on entering the room, and after every popstate) means Browser-Back always lands on the app's own handler instead of falling through to a blank page or leaving the SPA. The handler closes an open briefing panel first if one is open, otherwise ends the session immediately (sharing `stopAllAudioImmediately()`/`stopMic()` with the End-session button, per BUG 3) and returns to case selection. A visible "← All cases" link in the room header (`#backToCasesBtn`) calls `history.back()` — the exact same codepath as a real hardware/browser Back press, not a separate implementation.
- **Three sections:** case cards are now routed into "The Courtroom" / "The Boardroom" / "Your Own Case" by `case_type` (`gridForCase()` in `app.js`) rather than a hardcoded id list, so a case new to a section is placed correctly without a client change next time. Dissertation Defense and all uploaded/BYOC cases (including the upload card itself) live in "Your Own Case" — the builder's call the brief left open, made for consistency (defense-flavored cases and "bring your own document" share the same "prepare your own thing" register). Mobile breakpoints added (2 columns ≤900px, 1 column ≤560px).
- **Copy repositioning:** hero headline changed from "A hundred courtrooms before your first real one." to "The day that matters is coming. Walk in rehearsed." (Option A, per the user's decision) with the new subline, applied identically to the app's case-selection hero and the antechamber landing page (`hosting/index.html`), plus both pages' `<meta name="description">` (neither page had one before). The header tagline ("The room for your hardest conversations.") was left unchanged per instruction. Case-internal text, rubric language, and disclaimers were not touched.
- **Submission-phase carryover:** Devpost/writeup copy has not been re-aligned to this positioning yet — that happens in `/academy-submit`. Courtroom stays the flagship story for the demo video; the new "your important day" framing is the app's front door, not a replacement for the video's narrative.

## Regression (this round)

- `tests/test_case_language_handshake.py` + full `pytest -q` suite, privacy-scoping (owner-token), reverse-mode toggles, briefing-panel links, and the F22 replay path are all outside this round's diff surface (server core untouched, case files untouched) — re-run and confirmed green/unchanged as part of the Examine phase below.

---

# Round 3 of the microphone fixes — 2026-08-31

> Source: `build-prompts/fixes-mic-round-3.md`, a real microphone pass against
> `the-stand-00017-f2d`. Voice-pipeline server core (`run_live`, audio
> up/downstream, `SpeechConfig`) tabu again this round — confirmed untouched.
>
> **BUG 2 and BUG 3 were on their third attempt.** Both had been declared fixed
> in round 2 and both were still broken. The fix order forbade a third blind
> patch after reading the code, and required instrumentation first. That is what
> happened, and it changed both diagnoses — the round-2 fixes were not merely
> incomplete, they were aimed at the wrong thing.

## BUG 1 — Empty uploads invented whole cases (honesty P1)

**Measured:** `witness_agent/case_generator.py` checked only the *upper* size
bound. An empty file went to the model with no document content, and the model
filled the vacuum: a complete "Doctoral Dissertation Defense Examination" with
committee and methodology attack lines from zero bytes, presented under this
product's citation rubric. That contradicts the single claim the whole product
stands on.

**Fixed:** a pre-generation gate, `assert_document_has_substance()`, that
extracts text (native decode for text formats, `pypdf` text-layer extraction
for PDFs) and refuses before the model is ever called. HTTP 422, plain-language
message rendered on the upload card, no case, no grid entry, no template
fallback. The existing `focus_note` cite-or-GAP path is untouched — this is the
stage before it.

**Threshold rationale (as required by the fix order):** `MIN_DOCUMENT_CHARS =
400` **and** `MIN_DOCUMENT_WORDS = 50`, both must be met. It sits there because
the generator is asked for *cited* attack lines: below roughly one substantial
paragraph there is nothing left for a citation to point at, so anything the
model returns is necessarily invention. Real uploads (a dissertation chapter, a
proposal, a product brief) clear this by one to three orders of magnitude, so
the gate is invisible to legitimate use. The asymmetry is deliberate: rejecting
one thin-but-real document costs a re-upload, accepting one empty document costs
the product's credibility. **We only ever refuse on positive evidence** — a
format this module cannot extract (e.g. `.docx`) is passed through to the model
exactly as before, so the gate can never start rejecting real documents it
simply failed to read.

**Regression:** `tests/test_upload_substance_gate.py` — empty / whitespace-only
/ image-only-PDF (a real generated PDF with an image and no text layer) across
both modes, driven through the real HTTP upload route, asserting both the
refusal *and* that `generate_case_content` is never reached and the case cache
is unchanged. Plus the counter-test that a real document still passes.

## BUG 2 — Doubled transcript: the captured chunks

**Instrumented first.** `THE_STAND_TRANSCRIPT_DIAG=1` (new, off by default)
logs every raw `input_transcription`/`output_transcription` chunk with the
accumulated state around it. A real reverse-mode session against
`sales_discovery_call` produced the shape that was doubling the screen:

    accumulated (612 chars): "...limited slots open for installations this quarter, I wanted to make..."
    final chunk (615 chars): "...limited slots open for installations this quarter, so I wanted to make..."

    chunk in accumulated      -> False
    chunk.startswith(accum)   -> False
    difflib similarity        -> 0.9976
    word-level diff           -> exactly one insertion: "so"

At turn end the Live API re-sends the **entire turn as one chunk, lightly
revised** now that it has heard the whole utterance. That chunk is neither a
substring of the accumulation nor prefixed by it, so *both* of round 2's checks
necessarily missed it and it fell into the `accumulated + chunk` branch —
printing the whole turn twice. This is exactly what the user's screenshots
showed ("comes around 500k" / "comes in around 500k", "I figured" / "so I
figured"). **The round-2 fix could not have caught this case by construction**,
which is why it read convincingly and failed anyway.

**Fixed** in two parts, as the fix order specified:
1. `_reconcile_transcript_chunk()` gained a fourth case — a normalized-similarity
   restatement check (`_is_restatement`: punctuation/case-insensitive, a 0.6
   length-ratio floor so a short delta can never be mistaken for a re-send, and
   a 0.82 `difflib` similarity floor). A restatement **replaces** the turn.
2. The protocol changed, because replacing requires the client to be able to
   replace: the transcript message now carries `"replace": bool`, and on
   `replace: true` the server sends the **full corrected turn** and the client
   swaps the node instead of `+=`. Delta-level half-measures had failed twice.

Both directions use the identical function, so the YOU line is covered as well —
the screenshots show the input side was equally affected. `transcript_lines`
(scorer + debrief + download) is built from the same reconciled variables, so
that path is fixed by the same change rather than by a parallel one.

**Verified live, 4 sessions, EN and DE, forward and reverse:** `replace=true`
fired once per session at turn end, and the on-screen turn appears exactly once
in every case (witness line and YOU line both). Captured chunks are pinned in
`tests/test_transcript_reconcile.py`, including a guard test that asserts
neither old check would have matched — so that test can never pass for the
wrong reason.

## BUG 3 + BUG 4 — One root cause, and it was never the audio code

**Measured, not read.** A diagnostic mode (`?diag=1`, off by default) counts
AudioContexts, taps the real output graph with a pass-through `AnalyserNode`
(production routing byte-for-byte unchanged), samples output RMS every 20 ms,
and records every `playPcm16`/stop call with the state live at that moment. Then
a real session, with a real WAV fed through Chromium's fake capture device.

What the instrumentation ruled **out** — every hypothesis in the fix order:

| Hypothesis | Measurement | Verdict |
|---|---|---|
| (a) second AudioContext | `ctxCount == 1` in every run | ruled out |
| (b) `sessionEnded` reset after end | no reset observed post-`end_session` | ruled out |
| (c) a second playback path | 100% of frames go through `playPcm16` | ruled out |
| (e) server keeps streaming | `0` audio frames arrive after the click | ruled out |
| **(d) the handler never fires** | see below | **confirmed** |

And what it ruled **in**: when `endSessionNow()` actually runs, the stop works
perfectly — 58 scheduled sources killed, measured output RMS **0.43 → 0 inside
one 20 ms sample**, zero loud samples in the following 4 seconds. *The silence
code was never the bug.* Two rounds were spent fixing code that was correct.

The real cause is layout, and it is the same defect as BUG 4. `.room-main` was
`justify-content: center` with the default `overflow: visible` and no
`min-height: 0` — so a turn taller than the room overflowed the box **in both
directions**. `document.elementFromPoint()` at real viewport sizes, with the
doubled turn from BUG 2 on screen:

| Viewport | Whisper button — element actually on top | "End session." |
|---|---|---|
| 1440x900, doubled turn | `DIV.witness-block` (transcript) | y=1005 in a 900px viewport — **below the fold, `elementFromPoint` returns nothing** |
| 1280x800, single turn | `#avatarInitials` | y=797, cut off |
| 1280x800, doubled turn | `svg#waveform` | y=955 — off-screen |
| 1512x982, doubled turn | `#avatarInitials` | y=1046 — off-screen |
| 1280x620, doubled turn | `DIV.witness-line` | y=865 — off-screen |

So the Whisper toggle was **buried, not broken** — exactly the hypothesis in the
fix order, now confirmed by hit test, and exactly what the screenshot shows. And
"End session." was pushed past the bottom of the window, reachable only by
scrolling 145px first. A click where the user expects the button lands on
nothing, `endSessionNow()` never runs, `sessionEnded` stays false, and the
client plays out everything already buffered — which the instrumentation
measured at **up to 24.3 seconds queued ahead across 80 scheduled sources**.
That is "the AI keeps talking".

It also explains the correlation the user noticed: Whisper worked in the shorter
Rheinwerk session and not in the longer Vantage one. Short transcript, no
overflow; doubled transcript, overflow. BUG 2 was feeding BUG 4 and BUG 3.

**Fixed structurally, once:** `.room-main` gets `min-height: 0` and
`overflow-y: auto`, `justify-content: flex-start` plus an auto-margin pair
(`> *:first-child { margin-top: auto }` / `> *:last-child { margin-bottom:
auto }`) so content is centered only while there is free space — auto margins
resolve to 0 once space goes negative, so nothing is ever pushed above
`scrollTop: 0` (the same trap that made the case grid unreachable in round 7).
The transcript itself is now bounded and self-scrolling (`max-height: 38vh;
overflow-y: auto`) with auto-scroll-to-bottom, so a turn of any length scrolls
inside its own region and can never grow the room.

**Re-measured after the fix, all 8 configurations** (4 viewport sizes x
single/doubled turn): every header control reachable (`elementFromPoint`
returns the control itself), "End session." in the viewport in all 8,
`scrollHeight == clientHeight` everywhere — no overflow at all. And live:
**0 ms of audible output after the click in all four EN/DE forward/reverse
sessions** (0 loud samples out of ~200, 20 ms resolution — well inside the
0.3 s requirement).

**Still needs a human ear.** The instrumentation proves silence at the audio
graph in headless Chromium. It cannot prove what a speaker on a real machine
does in the last few milliseconds of hardware buffer. Per the acceptance
criterion, one of the three reproductions must be confirmed by a human with
ears, on the deployed revision — that is the one step no agent here can take.

## FEATURE 5 — Whisper explains itself

Line under the toggle: **"Stuck? It suggests your next question."** Quiet
register, same family as the `counsel's whisper` line, not a tooltip (the user
never found the tooltip). Default still OFF.

**Writeup candidate (Collaborative-Partner track):** the button is literally a
partner that leans over and suggests your next move when you're stuck — that
sentence is close to the track's own language and is a candidate for the
submission text.

## FEATURE 6 — "Take the stand." follows the selected case

The start block moves into the grid, onto the row directly beneath the selected
card and aligned to that card's column, and moves with the selection. With no
selection it returns to its original place under the grid. Column count is read
from the computed grid at render time and recomputed on resize, so the mobile
breakpoints stay intact.

## FEATURE 7 — Delete your own upload

`DELETE /api/cases/{case_id}`, ownership enforced **server-side**, reusing
`UploadedCaseStore.delete_case()` (the same path as `server/admin_prune_case`,
not a second implementation) and additionally dropping the in-process cache
entry, which the admin CLI cannot reach. A wrong or absent token gets the same
404 as an unknown id, so the endpoint can't be used to probe which upload ids
exist; a curated case is never deletable (403). Confirmation prompt before the
delete.

## FEATURE 8 — Transcript download in the technique column

The debrief now keeps the technique/rubric column that stood beside the session
(same `[T-xx]`/`[S-xx]` annotation lines, same label, same total), and the
download sits at the foot of it — where the user looked for it. Same
`downloadTranscript()`, same file: verified byte-identical to the existing
debrief button's download. The original button stays.

## DECISION 9 — "Watch a replayed session" withdrawn as a product entry

Removed from case selection (user's call: the replay shows only the AI's half,
so as a shop window it misrepresents a sparring product, and a juror who clicks
it hears a monologue). `replay.js` no-ops without the button, by its own
existing guard.

**Explicitly NOT touched:** `eval/eval_sets/live_audio_witness`, the recorded
bundle and the playback screenshot all stay in the repo. They remain the
engineering proof for the Live-audio eval and are cited as such in the writeup.
F22 is withdrawn as a *product entry point*, not as *evidence*.

## Revision — what the adversarial review caught that the build missed

Three findings, two of them P1, all fixed and re-verified. Worth recording,
because the fresh-context pass earned its keep twice in one round:

### P1 — BUG 2 had a SECOND cause, and the first fix didn't touch it

The restatement fix above is correct but only looks *inside* one turn. The
server clears `pending_examiner_text`/`current_witness_text` at `turn_complete`;
the browser has no `turn_complete` signal, so the first chunk of every new turn
arrived with `replace: false` and was **appended to the previous turn**. By the
third question the "YOU —" line was a run-on of every question ever asked:

    "Where were you on the night of the 14th?Can anyone corroborate that?"

That — not the restatement — is what the user's Rheinwerk screenshot actually
shows: *"...wo Sie mir dasHaben sie Okay, wann wann wären sie da?"*. The missing
space between "das" and "Haben" is a concatenation seam, not a repetition. Two
different defects were producing one symptom, which is why fixing either alone
kept looking wrong.

**Fixed:** a turn's first chunk now returns `replace=True`, so "the server
cleared the turn" and "the browser cleared the turn" are the same event instead
of two that drift apart. **Verified live** in a real two-turn session with a
fake microphone: question 1 (`"Tell me how your pricing works."`) is cleanly
replaced by question 2 (`"And what exactly is included in that number?"`) —
question 1 is gone from the screen, not glued to it.

### P1 — the upload gate had a hole big enough for a .docx

`assert_document_has_substance()` waved through **every** format outside
text/* and PDF as "unknown, let it through" — the deliberate "only refuse on
positive evidence" rule, applied too broadly. A `.docx`, an image or a
password-protected PDF therefore reached the generator, which rejected the
payload, and since `upload_case` had no catch-all the raw upstream error became
a **bare 500 on the Bring-Your-Own-Case card** — a visible crash on the demo's
second headline feature.

**Fixed:** the extractor now distinguishes "could not determine" (still let
through — that rule was right where it applies) from "we know we cannot read
this, and neither can the model". A `.docx`/image gets *"it isn't a format we
can read. Upload a PDF or a text file."*, a locked PDF gets its own message, and
`upload_case` finally has a catch-all so no upstream failure reaches the browser
unexplained. All four verified live: 422 with a distinct, accurate message,
never a 500.

### P2 — restatement detection could erase a word on a short turn

`_is_restatement()` had no lower bound on the accumulated turn's length, so on a
3-character accumulation the ratio and similarity guards were both trivially
satisfied: `"the"` + `" then"` was classified as a correction and replaced,
losing "the". The `na in nc` branch also matched a mid-string coincidence
(`"no"` inside `" I said no"`), which is a delta, not a correction.

**Fixed:** a 60-normalized-character floor (a real restatement is a whole turn —
the captured one was 612 characters), `startswith` instead of `in`, and only an
exact duplicate may still "replace" below the floor, because replacing text with
itself cannot lose anything. **The principle now encoded in the code: replacing
is destructive and appending is not, so the tie goes to appending.**

### Also probed and found sound (no action needed)

Delete-endpoint auth and id enumeration (wrong token, absent token, token
prefix, path traversal, double-delete, cross-owner — all correctly 404 and
indistinguishable from an unknown id; curated cases 403); owner-token privacy
scoping unchanged by this round; `placeStartBlock()`'s DOM surgery under resize
spam, stale selection and an empty grid (start block count stayed exactly 1,
click handler stayed bound); the room layout re-probed with a 10,000-character
transcript down to 900x500 (all controls reachable); model IDs and Cloud Run
timeout/memory against brief Section H.T (exact match).

## OPEN, demo-critical, NOT fixed this round — the debrief never arrives

Found by the demo rehearsal, independently reproduced twice by the coordinator
(a raw websocket client, no browser involved). **After "End session." no
`{"type":"debrief"}` frame is ever sent — not within 120 seconds, with no error
and no close frame. The room simply goes silent.** This is the last step of the
Kern-Loop and the entire basis of the Proof beat's on-screen `[D-08]`/AMTA
comparison, and of dossier test case T-06.

**Root cause, established at the log level, not inferred:**

    upstream_task()  -- breaks on end_session, finally: live_request_queue.close()
    downstream_task() -- `async for event in runner.run_live(...)` NEVER RETURNS
    await asyncio.gather(upstream_task(), downstream_task())   <-- hangs forever
    finally:  <-- never reached; this is where the debrief is built and sent

A log line placed at the top of that `finally` block (`"session loop returned,
building debrief"`) **never appears**. So the debrief code is not failing — it
is never reached. Closing the `LiveRequestQueue` does not terminate the
`run_live()` async generator.

**Confirmed NOT a regression from this round:** the diff `8699843..ebfb55c`
does not touch `end_session`, the `gather`, or `run_live`. Pre-existing, and it
has been latent through every prior round because no agent could reach the
debrief screen and no human test happened to wait for it.

**Also confirmed, so the fix has something to work with:** in a real browser
session `event.turn_complete` fires correctly (measured: twice in a two-turn
session, `wit_len=175` and `wit_len=181`), so `transcript_lines` and
`scored_events` do populate. The debrief would have real content the moment the
handler is allowed to reach it. (A raw-websocket harness does *not* produce
turn-ends — worth knowing before anyone re-tests this and draws the wrong
conclusion from an empty transcript.)

**Fixed this round (safe, in-scope part only):** the debrief block's
`except Exception: pass` — a silent swallow that would have left the user in an
empty room with no message even once the hang is fixed — now logs the exception
and sends the client an error frame.

**NOT fixed:** the hang itself. The fix lives in the shutdown sequencing around
`run_live` — i.e. inside the voice-pipeline core this round's fix order
explicitly declared tabu. Changing it unilaterally at feature-freeze, on the
one part of the system that took two rounds to get working, is the user's call,
not the builder's. The shape of the fix is small and does not touch audio
handling or `SpeechConfig`: stop awaiting both tasks together — await the
upstream task, then cancel the downstream task after a short grace period, so
the handler reaches its `finally`.
