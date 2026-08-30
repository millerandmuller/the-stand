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

# FEATURE 2: same schema plus an optional top-level "whisper" string — not
# in "required", so the model isn't forced to fill it (and it's simply
# absent from the schema entirely when whisper mode is off, see
# `_response_schema()` below).
_RESPONSE_SCHEMA_WHISPER = {
    **_RESPONSE_SCHEMA,
    "properties": {**_RESPONSE_SCHEMA["properties"], "whisper": {"type": "STRING"}},
}


def _response_schema(whisper_enabled: bool) -> dict:
    return _RESPONSE_SCHEMA_WHISPER if whisper_enabled else _RESPONSE_SCHEMA


# FEATURE 2 "Whisper mode": an optional addendum appended to the active
# system prompt when a session has the whisper toggle on. Reuses the
# RubricScorer's existing per-exchange call (no second model call, no new
# live codepath) — the model is simply asked to also produce one short,
# clearly-labeled suggestion line. cite-or-GAP: phrased as a suggestion,
# never claiming a document citation it doesn't have; left empty when the
# model has nothing worth suggesting this exchange (never forced).
_WHISPER_ADDENDUM_FORWARD = """

# Whisper mode (active this exchange)
Also include a "whisper" field: one short, concrete suggestion for what the
examiner could ask or say next to make progress (e.g. "Try an open discovery
question about her night-shift staffing"). Phrase it as a suggestion, not an
instruction, and never claim a document citation you don't have — keep it
generic instead of inventing one. Leave "whisper" as an empty string if
nothing in this exchange calls for a suggestion; do not force one.
"""

_WHISPER_ADDENDUM_REVERSE = """

# Whisper mode (active this exchange)
Also include a "whisper" field: one short suggestion for how the user could
respond to what the AI just said — name the pressure tactic the AI just used
and suggest how to parry it (e.g. "Name the pressure tactic before responding
to it"). Phrase it as a suggestion, not an instruction. Leave "whisper" as an
empty string if nothing in this exchange calls for one; do not force one.
"""

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


def _technique_block(techniques: list[dict]) -> str:
    lines = []
    for item in techniques:
        lines.append(f"- [{item['dxx']}] {item['name']} (source: {item['source']})")
    return "\n".join(lines)


_REVERSE_SYSTEM_PROMPT = """You are annotating a reverse-mode training session for a voice training
tool. In this mode the AI plays {reverse_role} and the user plays {user_role} — you are
NOT scoring or judging the user. You watch the AI's last statement and identify which of
the given techniques, if any, it just used.

Rules:
- Only emit an event for a technique if the AI's statement is actually evidence it used
  that technique. Most turns will trigger zero or one technique — do not force matches.
- "triggered" should be true whenever you emit an event here (this mode never scores a
  "violation" — there is no rule the AI is being held to, only technique identification).
  Leave "violation" false always.
- score_delta must always be 0 — this is descriptive annotation of the AI's technique, not
  an evaluation of the user, and the user must never be penalized or credited by it.
- note is one short, sober sentence naming what the AI just did — no coaching tone, no
  exclamation points, just an observation ("Opened with an open-ended discovery question"
  or "Anchored the conversation on price before asking about needs").
- If nothing in the technique list applies to this exchange, return an empty events list.

Techniques you may cite (only cite these dxx ids, only when the AI's statement is genuine
evidence of them; some have no published source and are labeled "uncited" — cite them the
same way, do not invent a source for them):
{rubric_block}
"""


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
    whisper: Optional[str] = None


class RubricScorer:
    """Scores one examiner/witness exchange against a case file's rubric.

    `reverse=True` (F18): switches from "score the user's (examiner) turn
    against the rubric" to "annotate which technique the AI's (witness')
    turn just used" — same pipeline, different system prompt and criteria
    list (`case["reverse"]["techniques"]` instead of `case["rubric"]`), and
    every event's score_delta is forced to 0 regardless of what the model
    returns, since reverse mode never scores the user."""

    def __init__(self, case: dict, api_key: Optional[str] = None, reverse: bool = False):
        self.case = case
        self.reverse = reverse
        self._client = genai.Client(
            api_key=api_key or os.environ.get("GOOGLE_API_KEY")
        )
        if reverse:
            reverse_block = case["reverse"]
            self.rubric = reverse_block["techniques"]
            self._system_prompt = _REVERSE_SYSTEM_PROMPT.format(
                reverse_role=reverse_block["role"],
                user_role=reverse_block["user_role"],
                rubric_block=_technique_block(self.rubric),
            )
        else:
            self.rubric = case["rubric"]
            self._system_prompt = _SYSTEM_PROMPT.format(
                rubric_block=_rubric_block(self.rubric)
            )
        # F19 2b: the case's upload-time focus, if any — mutable via
        # set_focus() when the operator shifts it mid-session. Context only,
        # never a citation source: it nudges which rubric events get judged
        # relevant, it does not add or invent a [D-xx].
        self.active_focus = (case.get("focus") or "").strip() or None
        # FEATURE 2: default OFF — the room stays sober unless the operator
        # opts in via the header toggle (see server/app.py "whisper" WS msg).
        self.whisper_enabled = False

    def set_focus(self, focus: Optional[str]) -> None:
        self.active_focus = (focus or "").strip() or None

    def set_whisper(self, enabled: bool) -> None:
        self.whisper_enabled = bool(enabled)

    def _system_instruction(self) -> str:
        instruction = self._system_prompt
        if self.active_focus:
            instruction += (
                f'\n\nThe examiner has asked to be pressed on: "{self.active_focus}". '
                f"Use this only as context for which criteria are likely relevant — "
                f"never force a match, and never cite it as a document source."
            )
        if self.whisper_enabled:
            instruction += _WHISPER_ADDENDUM_REVERSE if self.reverse else _WHISPER_ADDENDUM_FORWARD
        return instruction

    def _contents(self, examiner_question: str, witness_answer: str) -> str:
        if self.reverse:
            witness_label = self.case["reverse"]["role"]
            return (
                f'AI ({witness_label}): "{witness_answer}"\n'
                f'User response: "{examiner_question}"'
            )
        return (
            f'Examiner: "{examiner_question}"\n'
            f'Witness ({self.case["witness"]["name"]}): "{witness_answer}"'
        )

    async def score_exchange(
        self, examiner_question: str, witness_answer: str
    ) -> ScoringResult:
        """Scores a single examiner question + witness answer pair."""
        response = await self._client.aio.models.generate_content(
            model=SCORER_MODEL,
            contents=self._contents(examiner_question, witness_answer),
            config=types.GenerateContentConfig(
                system_instruction=self._system_instruction(),
                response_mime_type="application/json",
                response_schema=_response_schema(self.whisper_enabled),
                temperature=0.1,
            ),
        )
        return self._parse(response.text, response.usage_metadata)

    def score_exchange_sync(
        self, examiner_question: str, witness_answer: str
    ) -> ScoringResult:
        """Synchronous variant for scripted test scenarios (no event loop)."""
        response = self._client.models.generate_content(
            model=SCORER_MODEL,
            contents=self._contents(examiner_question, witness_answer),
            config=types.GenerateContentConfig(
                system_instruction=self._system_instruction(),
                response_mime_type="application/json",
                response_schema=_response_schema(self.whisper_enabled),
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
        if self.reverse:
            # Deterministic guarantee, not just a prompt instruction: reverse
            # mode never scores the user, no matter what the model returns.
            for e in events:
                e.score_delta = 0
                e.violation = False
        delta = sum(e.score_delta for e in events)
        whisper = (data.get("whisper") or "").strip() or None if self.whisper_enabled else None
        return ScoringResult(
            events=events, running_score_delta=delta, usage_metadata=usage_metadata, whisper=whisper
        )
