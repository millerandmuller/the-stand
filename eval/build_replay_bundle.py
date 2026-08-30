"""F22 — builds the "Watch a replayed session" bundle from a real
`adk eval` run of `eval/eval_sets/live_audio_witness/` (see eval/run_eval.py
module docstring, item 6, for how that run is produced).

This is a one-off curation script, not part of the live app or the
WS/mic/run_live pipeline — it only reads an `.evalset_result.json` already
on disk and the same `RubricScorer` the live app uses (offline, against the
recorded transcript) to produce two static artifacts for the antechamber
replay feature:

  server/static/replay/session_audio.wav   — the witness's real Gemini Live
                                              output audio, concatenated in
                                              order with a short gap between
                                              turns
  server/static/replay/session_bundle.json — transcript + real per-exchange
                                              RubricScorer score events, each
                                              tagged with its audio-timeline
                                              offset in seconds

Nothing here is invented: every audio byte and every [D-xx] score event
traces back to this actual recorded session. If this session's transcript
never produced a live barge-in, the bundle simply has no interruption event
— see docs/plan_b.md / DIRECTORS_NOTES.md for how that's handled honestly
in the demo script, rather than fabricating one here.

Run: python eval/build_replay_bundle.py [path/to/*.evalset_result.json]
(defaults to the newest file under witness_agent/.adk/eval_history/)
"""

import base64
import json
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from rubric_scorer.scorer import RubricScorer
from witness_agent.agent import load_case

SAMPLE_RATE = 24000
GAP_SECONDS = 1.2
CASE_ID = "martinez_v_nordbay"

OUT_DIR = Path(__file__).parent.parent / "server" / "static" / "replay"
EVAL_HISTORY_DIR = Path(__file__).parent.parent / "witness_agent" / ".adk" / "eval_history"


def _latest_result_file() -> Path:
    files = sorted(EVAL_HISTORY_DIR.glob("*live_audio_witness*.evalset_result.json"))
    if not files:
        raise SystemExit(
            "No live_audio_witness eval result found under witness_agent/.adk/eval_history/ — "
            "run `adk eval witness_agent eval/eval_sets/live_audio_witness/live_audio_witness.evalset.json "
            "--config_file_path eval/eval_sets/live_audio_witness/test_config.json` first."
        )
    return files[-1]


def _b64decode(s: str) -> bytes:
    """The eval result's inline_data.data is URL-safe base64 (`-`/`_`) with
    padding stripped — Python's stdlib b64decode assumes standard alphabet
    and full padding, so both need normalizing first."""
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def _witness_audio_pcm(invocation: dict) -> bytes:
    """Concatenates the witness's real output-audio chunks for one
    invocation, in event order. Skips near-empty (<100 byte) chunks — those
    are Live API keepalive/boundary packets, not audible content."""
    events = invocation["actual_invocation"]["intermediate_data"]["invocation_events"]
    pcm = bytearray()
    for event in events:
        if event.get("author") != "witness_agent":
            continue
        for part in (event.get("content") or {}).get("parts") or []:
            inline = part.get("inline_data")
            if not inline or not (inline.get("mime_type") or "").startswith("audio/pcm"):
                continue
            data = _b64decode(inline["data"])
            if len(data) < 100:
                continue
            pcm.extend(data)
    return bytes(pcm)


def _text_of(content: dict) -> str:
    parts = (content or {}).get("parts") or []
    return " ".join(p["text"] for p in parts if p.get("text")).strip()


def main() -> None:
    result_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_result_file()
    print(f"Reading {result_path}")
    data = json.loads(result_path.read_text())
    invocations = data["eval_case_results"][0]["eval_metric_result_per_invocation"]

    case = load_case(CASE_ID)
    scorer = RubricScorer(case)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined_pcm = bytearray()
    silence_gap = b"\x00\x00" * int(SAMPLE_RATE * GAP_SECONDS)
    turns = []

    for inv in invocations:
        ai = inv["actual_invocation"]
        question = _text_of(ai["user_content"])
        answer = _text_of(ai["final_response"])
        pcm = _witness_audio_pcm(inv)
        if not question or not answer or not pcm:
            print(f"SKIP invocation {ai['invocation_id']} — missing text or audio, incomplete turn")
            continue

        offset_seconds = len(combined_pcm) / 2 / SAMPLE_RATE
        combined_pcm.extend(pcm)
        duration_seconds = len(pcm) / 2 / SAMPLE_RATE

        print(f"Scoring turn: {question!r} -> {answer!r}")
        result = scorer.score_exchange_sync(question, answer)
        events = [
            {
                "dxx": e.dxx,
                "criterion": e.criterion,
                "note": e.note,
                "triggered": e.triggered,
                "violation": e.violation,
                "score_delta": e.score_delta,
            }
            for e in result.events
        ]

        turns.append(
            {
                "question": question,
                "answer": answer,
                "offset_seconds": round(offset_seconds, 2),
                "duration_seconds": round(duration_seconds, 2),
                "events": events,
            }
        )
        combined_pcm.extend(silence_gap)

    if not turns:
        raise SystemExit("No complete turns (question + answer + audio) found — nothing to build.")

    wav_path = OUT_DIR / "session_audio.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(bytes(combined_pcm))

    total_seconds = len(combined_pcm) / 2 / SAMPLE_RATE
    bundle = {
        "label": "Recorded session, re-played by adk eval",
        "case_id": CASE_ID,
        "case_name": case["case_name"],
        "witness_name": case["witness"]["name"],
        "source_eval_result": result_path.name,
        "total_seconds": round(total_seconds, 2),
        "turns": turns,
    }
    bundle_path = OUT_DIR / "session_bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2))

    print(f"\nWrote {wav_path} ({total_seconds:.1f}s)")
    print(f"Wrote {bundle_path} ({len(turns)} turns, {sum(len(t['events']) for t in turns)} score events)")


if __name__ == "__main__":
    main()
