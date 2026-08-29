"""RubricScorer: a periodic, text-only critic over the cross-examination
transcript (F3).

This is deliberately NOT the Live model — it's a separate, cheap/fast
gemini-3.7-flash call per examiner/witness exchange, the "Critic/Judge"
pattern used by adk-samples/llm-auditor (see H.T stack contract). It watches
the last few turns of the transcript and decides which rubric criteria from
the active case file were just triggered or violated, citing the dossier's
[D-xx] source for every claim — never inventing a citation.

Latency of 2-3s behind audio is explicitly acceptable per the brief, so this
runs as a fire-and-forget async task per witness turn rather than blocking
the live audio pipeline.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

SCORER_MODEL = "gemini-3.7-flash"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "events": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "criterion": {"type": "STRING"},
                    "dxx": {"type": "STRING"},
                    "triggered": {"type": "BOOLEAN"},
                    "violation": {"type": "BOOLEAN"},
                    "note": {"type": "STRING"},
                    "score_delta": {"type": "INTEGER"},
                },
                "required": [
                    "criterion",
                    "dxx",
                    "triggered",
                    "violation",
                    "note",
                    "score_delta",
                ],
            },
        }
    },
    "required": ["events"],
}

_SYSTEM_PROMPT = """You are a cross-examination rubric judge for a legal training tool.

You watch one examiner question and one witness answer at a time and decide
which of the given rubric criteria were just triggered (the examiner did the
technique well) or violated (the examiner made the mistake the criterion
warns against). Only ever cite the exact [D-xx] id given for a criterion —
never invent a citation and never cite a criterion that isn't in the list
below.

Rules:
- Only emit an event for a criterion if this specific exchange is actually
  evidence for or against it. Most exchanges will trigger zero or one
  criterion — do not force matches.
- "triggered" means the examiner satisfied the technique (positive).
  "violation" means the examiner broke the rule the criterion describes
  (negative). Exactly one of triggered/violation should be true when you
  emit an event for a criterion; never both.
- score_delta is a small AMTA-scale nudge: +1 for a clean triggered
  technique, -1 for a violation, 0 if you're noting something ambiguous.
- note is one short courtroom-sober sentence, no exclamation points, no
  coaching tone — state what happened.
- If nothing in the rubric applies to this exchange, return an empty
  events list. Do not pad output with irrelevant events.

Rubric criteria for this case (only cite these, only by their dxx id):
{rubric_block}
"""


def _rubric_block(rubric: list[dict]) -> str:
    lines = []
    for item in rubric:
        lines.append(f"- [{item['dxx']}] {item['criterion']} (source: {item['source']})")
    return "\n".join(lines)


@dataclass
class ScoreEvent:
    criterion: str
    dxx: str
    triggered: bool
    violation: bool
    note: str
    score_delta: int


@dataclass
class ScoringResult:
    events: list[ScoreEvent] = field(default_factory=list)
    running_score_delta: int = 0
    usage_metadata: Optional[object] = None


class RubricScorer:
    """Scores one examiner/witness exchange against a case file's rubric."""

    def __init__(self, case: dict, api_key: Optional[str] = None):
        self.case = case
        self.rubric = case["rubric"]
        self._client = genai.Client(
            api_key=api_key or os.environ.get("GOOGLE_API_KEY")
        )
        self._system_prompt = _SYSTEM_PROMPT.format(
            rubric_block=_rubric_block(self.rubric)
        )

    async def score_exchange(
        self, examiner_question: str, witness_answer: str
    ) -> ScoringResult:
        """Scores a single examiner question + witness answer pair."""
        contents = (
            f'Examiner: "{examiner_question}"\n'
            f'Witness ({self.case["witness"]["name"]}): "{witness_answer}"'
        )
        response = await self._client.aio.models.generate_content(
            model=SCORER_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.1,
            ),
        )
        return self._parse(response.text, response.usage_metadata)

    def score_exchange_sync(
        self, examiner_question: str, witness_answer: str
    ) -> ScoringResult:
        """Synchronous variant for scripted test scenarios (no event loop)."""
        contents = (
            f'Examiner: "{examiner_question}"\n'
            f'Witness ({self.case["witness"]["name"]}): "{witness_answer}"'
        )
        response = self._client.models.generate_content(
            model=SCORER_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.1,
            ),
        )
        return self._parse(response.text, response.usage_metadata)

    def _parse(self, text: str, usage_metadata=None) -> ScoringResult:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return ScoringResult(usage_metadata=usage_metadata)
        events = [ScoreEvent(**e) for e in data.get("events", [])]
        delta = sum(e.score_delta for e in events)
        return ScoringResult(events=events, running_score_delta=delta, usage_metadata=usage_metadata)
