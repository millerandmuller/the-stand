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
  {"type": "score", "events": [{"criterion","dxx","triggered","violation","note","score_delta"}]}
  {"type": "debrief", "amta_score": int, "headline": "...", "moments": [...], "practice_focus": "..."}
  {"type": "error", "message": "..."}
"""

import asyncio
import base64
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from rubric_scorer.debrief import DebriefAgent
from rubric_scorer.scorer import RubricScorer
from server.firestore_store import SessionStore
from witness_agent.agent import DISCLAIMER, load_case, make_agent_for_case

APP_NAME = "the_stand"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

session_service = InMemorySessionService()
firestore_store = SessionStore()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/cases")
async def list_cases():
    from witness_agent.agent import CASE_FILES

    out = []
    for case_id in CASE_FILES:
        case = load_case(case_id)
        out.append(
            {
                "case_id": case_id,
                "case_name": case["case_name"],
                "witness_name": case["witness"]["name"],
                "summary": case["summary"].strip(),
            }
        )
    return {"cases": out, "disclaimer": DISCLAIMER}


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
    pressure_level = int(first.get("pressure_level", 1))
    user_id = "operator"

    case, agent, stage_direction_for_level = make_agent_for_case(case_id)
    scorer = RubricScorer(case)
    debrief_agent = DebriefAgent()

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={"pressure_level": pressure_level},
    )
    await firestore_store.start_session(session_id, case_id, pressure_level)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    live_request_queue = LiveRequestQueue()

    # Transcript bookkeeping for the RubricScorer (per-exchange) and the
    # DebriefAgent (whole session) — the scorer needs one examiner question
    # paired with the witness answer that follows it.
    transcript_lines: list[str] = []
    scored_events: list[dict] = []
    pending_examiner_text = ""
    current_witness_text = ""

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
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "audio":
                    audio_bytes = base64.b64decode(msg["data"])
                    live_request_queue.send_realtime(
                        types.Blob(mime_type="audio/pcm;rate=16000", data=audio_bytes)
                    )
                elif mtype == "dial":
                    level = int(msg.get("level", 1))
                    direction = stage_direction_for_level(level)
                    live_request_queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=direction)])
                    )
                elif mtype == "end_session":
                    break
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
            debrief = await debrief_agent.build(transcript, scored_events)
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
            }
            await websocket.send_text(json.dumps({"type": "debrief", **debrief_payload}))
            await firestore_store.save_debrief(session_id, debrief_payload)
        except Exception:
            pass
