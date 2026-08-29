"""ADK-Agent wrapper around RubricScorer's judging logic (F6).

RubricScorer itself is NOT an ADK Agent — it drives `google.genai` directly
so the live server can fire-and-forget it per witness turn (see
`rubric_scorer/scorer.py`). `adk eval` / `AgentEvaluator` need something with
a `root_agent` they can actually invoke through a Runner, and WitnessAgent
can't be that thing either: it's a Live/bidi agent (`run_live`), and stock
`adk eval` drives agents through `run_async`/text turns — there is no
session-replay path for a bidi Live connection in ADK 2.8.0's evaluation
framework (checked adk-docs `evaluate/index.md`: web UI, pytest, and
`adk eval` CLI all evaluate via `AgentEvaluator`, and every worked example
there is a turn-based text agent).

So this wrapper exists purely as an eval-time double: a turn-based `Agent`
that shares RubricScorer's exact system prompt (reused, not duplicated) and
takes the same "Examiner: ...\\nWitness: ..." turn format, so the actual
judging logic gets exercised through the real `adk eval` pipeline instead of
a bespoke test harness pretending to be one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google.adk.agents import Agent

from rubric_scorer.scorer import SCORER_MODEL, _SYSTEM_PROMPT, _rubric_block
from witness_agent.agent import load_case

_case = load_case("martinez_v_nordbay")

root_agent = Agent(
    name="rubric_judge_agent",
    model=SCORER_MODEL,
    description="Turn-based eval double for RubricScorer's per-exchange judging logic.",
    instruction=_SYSTEM_PROMPT.format(rubric_block=_rubric_block(_case["rubric"])),
)
