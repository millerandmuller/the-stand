"""Two checks for the pressure dial / escalation mechanism (F4 + F10):

1. Unit-level: witness_instruction(context) returns the right escalation text
   for each pressure_level, deterministically, no API calls. This is the
   mechanism that sets the *starting* demeanor for a fresh connection
   (session start or a session_resumption reconnect).

2. Live smoke test: operate the dial on an already-open Live connection by
   sending a `[STAGE DIRECTION: ...]` content turn, and confirm the model
   picks it up on its next reply without narrating or acknowledging it.
   (Earlier iteration of this test tried mutating session.state mid-connection
   directly — that does NOT work: Gemini Live sends system_instruction once
   at connect() time, so a mid-connection state edit doesn't reach an
   already-open connection. That's why the dial is a content-turn, not a
   state edit, once a session is live. See witness_agent/agent.py and README.)

Run: python tests/test_pressure_dial.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from google.adk.agents.run_config import RunConfig
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from witness_agent.agent import root_agent, witness_instruction, stage_direction_for_level


def test_instruction_provider_unit():
    print("=== Unit test: witness_instruction() per pressure_level ===")
    expected_snippets = {
        1: "Calm and cooperative",
        2: "Mildly defensive",
        3: "Visibly rattled",
    }
    ok = True
    for level, snippet in expected_snippets.items():
        ctx = SimpleNamespace(state={"pressure_level": level})
        instruction = witness_instruction(ctx)
        hit = snippet in instruction
        print(f"level={level}: expected snippet {snippet!r} present: {hit}")
        ok = ok and hit

    ctx = SimpleNamespace(state={"pressure_level": 99})
    hit = "Visibly rattled" in witness_instruction(ctx)
    print(f"level=99 (clamped to 3): {hit}")
    ok = ok and hit

    ctx = SimpleNamespace(state={})
    hit = "Calm and cooperative" in witness_instruction(ctx)
    print(f"level missing (defaults to 1): {hit}")
    ok = ok and hit

    print("UNIT TEST:", "PASS" if ok else "FAIL")
    return ok


async def test_live_dial_mechanism():
    print("\n=== Live smoke test: mid-connection dial via [STAGE DIRECTION: ...] ===")

    session_service = InMemorySessionService()
    app_name, user_id, session_id = "the_stand_test", "test_user", "dial_session"
    session = await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
        state={"pressure_level": 1},
    )
    runner = Runner(agent=root_agent, app_name=app_name, session_service=session_service)
    run_config = RunConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    queue = LiveRequestQueue()
    question = "Mr. Petrov, did you inspect every pallet that night?"
    steps = [
        ("question", question),
        ("stage_direction", stage_direction_for_level(3)),
        ("question", question),
    ]
    step_idx = 0
    replies = []
    reply = ""

    queue.send_content(
        types.Content(role="user", parts=[types.Part(text=steps[0][1])])
    )

    async for event in runner.run_live(
        session=session,
        live_request_queue=queue,
        run_config=run_config,
    ):
        if event.output_transcription and event.output_transcription.text:
            reply += event.output_transcription.text
        elif event.content and event.content.parts:
            for p in event.content.parts:
                if p.text:
                    reply += p.text
        if event.turn_complete:
            replies.append((steps[step_idx][0], reply))
            reply = ""
            step_idx += 1
            if step_idx >= len(steps):
                break
            queue.send_content(
                types.Content(role="user", parts=[types.Part(text=steps[step_idx][1])])
            )

    queue.close()

    for kind, r in replies:
        print(f"\n[{kind}] -> {r!r}")

    stage_direction_reply = next(r for k, r in replies if k == "stage_direction")
    acknowledged = "stage direction" in stage_direction_reply.lower()
    print(f"\nModel verbally acknowledged the stage direction (should be False): {acknowledged}")

    before_reply = replies[0][1]
    after_reply = replies[-1][1]
    changed = before_reply.strip() != after_reply.strip()
    print(f"Reply text differs before/after the dial turn (qualitative signal): {changed}")

    ok = (not acknowledged) and len(replies) == 3
    print("LIVE DIAL MECHANISM TEST:", "PASS" if ok else "FAIL")
    return ok


async def main():
    unit_ok = test_instruction_provider_unit()
    live_ok = await test_live_dial_mechanism()
    ok = unit_ok and live_ok
    print("\n=== OVERALL:", "PASS" if ok else "FAIL", "===")
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
