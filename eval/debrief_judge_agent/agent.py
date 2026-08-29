"""ADK-Agent wrapper around DebriefAgent's system prompt (F6), for the same
reason rubric_judge_agent wraps RubricScorer: DebriefAgent drives
`google.genai` directly, so it needs a turn-based eval double to be
exercised through the real `adk eval` / `AgentEvaluator` pipeline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google.adk.agents import Agent

from rubric_scorer.debrief import DEBRIEF_MODEL, _SYSTEM_PROMPT

root_agent = Agent(
    name="debrief_judge_agent",
    model=DEBRIEF_MODEL,
    description="Turn-based eval double for DebriefAgent's session-close judging logic.",
    instruction=_SYSTEM_PROMPT,
)
