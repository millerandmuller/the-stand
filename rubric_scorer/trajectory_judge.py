"""RubricTrajectoryJudge (F6): a session-level gemini-3.7-flash judge that
re-plays a WHOLE cross-examination transcript and decides, per rubric
criterion, whether the examiner demonstrated it somewhere across the session
— not per exchange, the way RubricScorer does live.

Extends RubricScorer's exact approach (same rubric-citation discipline, same
model, same JSON-schema-constrained output) to the trajectory level, which
is what the eval suite needs to grade a full simulated session: "ein Run
re-played die Demo-Session mit pass/fail je Kriterium" (brief F6 acceptance).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

JUDGE_MODEL = "gemini-3.7-flash"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdicts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "dxx": {"type": "STRING"},
                    "criterion": {"type": "STRING"},
                    "passed": {"type": "BOOLEAN"},
                    "evidence": {"type": "STRING"},
                },
                "required": ["dxx", "criterion", "passed", "evidence"],
            },
        }
    },
    "required": ["verdicts"],
}

_SYSTEM_PROMPT = """You are re-playing a full cross-examination session for a legal
training tool and deciding, for the WHOLE session (not one exchange), whether the
examiner satisfied each rubric criterion at least once. Only ever cite the exact
[D-xx] id given for a criterion — never invent a citation and never cite a
criterion that isn't in the list below.

Rules:
- "passed" means the examiner demonstrated the technique (or avoided the
  violation) somewhere in the session — you do not need every single
  question to show it, one clean instance is enough for a "triggered"-style
  criterion.
- For a criterion that describes a mistake to avoid (e.g. compound
  questions, losing control), "passed" means the examiner did NOT commit
  that mistake anywhere material in the session.
- "evidence" is one short quote or close paraphrase of the turn that
  justifies your verdict — never invent a quote that isn't in the
  transcript.
- Emit exactly one verdict per rubric criterion listed below, even if you
  judge it "passed": false — never omit a criterion.

Rubric criteria for this case (only cite these, only by their dxx id):
{rubric_block}
"""


def _rubric_block(rubric: list[dict]) -> str:
    lines = []
    for item in rubric:
        lines.append(f"- [{item['dxx']}] {item['criterion']} (source: {item['source']})")
    return "\n".join(lines)


@dataclass
class TrajectoryVerdict:
    dxx: str
    criterion: str
    passed: bool
    evidence: str


@dataclass
class TrajectoryResult:
    verdicts: list[TrajectoryVerdict] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return bool(self.verdicts) and all(v.passed for v in self.verdicts)


class RubricTrajectoryJudge:
    """Session-level counterpart to RubricScorer: one gemini-3.7-flash call
    over the full transcript instead of one call per exchange."""

    def __init__(self, case: dict, api_key: Optional[str] = None):
        self.case = case
        self.rubric = case["rubric"]
        self._client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self._system_prompt = _SYSTEM_PROMPT.format(rubric_block=_rubric_block(self.rubric))

    def judge_sync(self, transcript: str) -> TrajectoryResult:
        response = self._client.models.generate_content(
            model=JUDGE_MODEL,
            contents=f"Full session transcript:\n{transcript}",
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.1,
            ),
        )
        return self._parse(response.text)

    async def judge(self, transcript: str) -> TrajectoryResult:
        response = await self._client.aio.models.generate_content(
            model=JUDGE_MODEL,
            contents=f"Full session transcript:\n{transcript}",
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.1,
            ),
        )
        return self._parse(response.text)

    def _parse(self, text: str) -> TrajectoryResult:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return TrajectoryResult()
        verdicts = [TrajectoryVerdict(**v) for v in data.get("verdicts", [])]
        return TrajectoryResult(verdicts=verdicts)
