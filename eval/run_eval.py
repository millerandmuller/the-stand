"""The Stand's eval suite (F6) — the CI-shaped entry point the brief asks
for: "ein Run re-played die Demo-Session mit pass/fail je Kriterium."

What this actually runs, and why (read this before trusting a PASS):

1. `rubric_judge_agent` / `debrief_judge_agent` are turn-based ADK Agent
   doubles that share RubricScorer's / DebriefAgent's exact system prompts
   (see eval/rubric_judge_agent/agent.py, eval/debrief_judge_agent/agent.py).
   They exist because stock `adk eval` / `AgentEvaluator` evaluate agents
   through `run_async` on text turns — there is no session-replay path for
   a bidi Live connection in ADK 2.8.0's evaluation framework (checked
   adk-docs `evaluate/index.md`), so WitnessAgent itself (Live/bidi) cannot
   be evaluated this way. This is the honest scoped-down equivalent the
   brief allows: evaluate the turn-based judging logic that IS the
   product's trust mechanism, through the real `AgentEvaluator` pipeline,
   using ADK's `rubric_based_final_response_quality_v1` /
   `rubric_based_multi_turn_trajectory_quality_v1` LLM-as-judge criteria
   (gemini-3.7-flash) with per-case rubrics instead of exact-match JSON
   diffing.
2. `rubric_scorer.evalset.json` — T-01..T-05 scripted deterministic
   exchanges from expert_dossier.md.
3. `novice_trajectory.evalset.json` — the scoped-down simulator: a
   hand-authored 4-turn NOVICE-examiner-persona trajectory (a full
   TTS-User-Simulator per the brief's architecture section was out of
   scope for this milestone; a text persona playing a clumsy novice across
   a multi-turn session is the acceptable scoped version, and this is that
   version, run through `rubric_based_multi_turn_trajectory_quality_v1`).
4. `debrief.evalset.json` — T-06, DebriefAgent's session-close output.
5. `RubricTrajectoryJudge` (rubric_scorer/trajectory_judge.py) — run
   DIRECTLY (not through ADK eval) against the same novice trajectory, to
   exercise the actual session-level judge the product would use at demo
   time, independent of the AgentEvaluator wrapper.

Run: python eval/run_eval.py
"""

import asyncio
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from google.adk.evaluation.agent_evaluator import AgentEvaluator

from rubric_scorer.trajectory_judge import RubricTrajectoryJudge
from witness_agent.agent import load_case

EVAL_DIR = Path(__file__).parent
EVAL_SETS = EVAL_DIR / "eval_sets"

results: list[tuple[str, bool, str]] = []


async def run_agent_eval(label: str, agent_module: str, evalset_path: Path) -> None:
    # AgentEvaluator auto-discovers a sibling `test_config.json` next to the
    # eval set file (AgentEvaluator.find_config_for_test_file) — that's why
    # each eval set below lives in its own subdirectory with its own config
    # rather than sharing eval_sets/test_config.json.
    try:
        await AgentEvaluator.evaluate(
            agent_module=agent_module,
            eval_dataset_file_path_or_dir=str(evalset_path),
            num_runs=1,
            print_detailed_results=True,
        )
        results.append((label, True, "all criteria met threshold"))
    except AssertionError as exc:
        results.append((label, False, str(exc)[:500]))
    except Exception as exc:  # eval-suite failure must not crash the whole run
        results.append((label, False, f"ERROR: {exc!r}"))


def run_trajectory_judge_direct() -> None:
    case = load_case("martinez_v_nordbay")
    judge = RubricTrajectoryJudge(case)

    transcript = (
        "Examiner: Can you just tell me what happened that night?\n"
        "Witness: Sure — I supervised the loading dock as usual, inspected every "
        "pallet per protocol before it left. Nothing out of the ordinary that I recall.\n"
        "Examiner: You saw the pallet arrive, checked the manifest, and then signed "
        "off without opening it, correct?\n"
        "Witness: I mean, that's roughly what happened, yes.\n"
        "Examiner: The loading dock log shows no scan entry for pallet 4471-B between "
        "9 PM and end of shift — isn't that right?\n"
        "Witness: I... I don't have an explanation for that gap right now.\n"
        "Examiner: Why not?\n"
        "Witness: It's possible, but I'd have to check — a lot was happening on the "
        "floor that night, and honestly, why does that one gap matter more than the "
        "rest of the log?"
    )

    print("\n=== RubricTrajectoryJudge (direct, gemini-3.7-flash) over novice session ===")
    result = judge.judge_sync(transcript)
    if not result.verdicts:
        results.append(("RubricTrajectoryJudge direct run", False, "no verdicts returned"))
        print("  FAIL: no verdicts returned")
        return

    valid_dxx = {item["dxx"] for item in case["rubric"]}
    invented = [v.dxx for v in result.verdicts if v.dxx not in valid_dxx]
    for v in result.verdicts:
        status = "PASS" if v.passed else "FAIL"
        print(f"  [{v.dxx}] {status}: {v.criterion}\n    evidence: {v.evidence}")

    ok = not invented
    note = "no invented dxx ids" if ok else f"invented dxx ids: {invented}"
    results.append(("RubricTrajectoryJudge direct run (no invented citations)", ok, note))


async def main() -> None:
    await run_agent_eval(
        "rubric_judge_agent vs rubric_scorer.evalset.json (T-01..T-05)",
        agent_module="eval.rubric_judge_agent",
        evalset_path=EVAL_SETS / "rubric_scorer" / "rubric_scorer.evalset.json",
    )
    await run_agent_eval(
        "rubric_judge_agent vs novice_trajectory.evalset.json (simulated NOVICE)",
        agent_module="eval.rubric_judge_agent",
        evalset_path=EVAL_SETS / "novice_trajectory" / "novice_trajectory.evalset.json",
    )
    await run_agent_eval(
        "debrief_judge_agent vs debrief.evalset.json (T-06)",
        agent_module="eval.debrief_judge_agent",
        evalset_path=EVAL_SETS / "debrief" / "debrief.evalset.json",
    )
    run_trajectory_judge_direct()

    print("\n\n=== EVAL SUITE SUMMARY ===")
    for label, passed, note in results:
        print(f"{'PASS' if passed else 'FAIL'}: {label}\n  {note}")

    all_passed = all(p for _, p, _ in results)
    print("\nOVERALL:", "PASS" if all_passed else "FAIL (see per-criterion notes above)")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
