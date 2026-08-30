"""DebriefAgent (F5): one gemini-3.7-flash call over the full transcript +
the RubricScorer's running events, producing an AMTA-scale score, the two
most important moments (transcript excerpts), and one practice focus.

Copy tone per brief section 5: terse, courtroom-sober. Not chirpy, not
gamified — "Session closed. Two moments worth replaying."
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

DEBRIEF_MODEL = "gemini-3.7-flash"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "amta_score": {"type": "INTEGER"},
        "headline": {"type": "STRING"},
        "moments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "excerpt": {"type": "STRING"},
                    "why_it_matters": {"type": "STRING"},
                    "dxx": {"type": "STRING"},
                },
                "required": ["excerpt", "why_it_matters", "dxx"],
            },
        },
        "practice_focus": {"type": "STRING"},
    },
    "required": ["amta_score", "headline", "moments", "practice_focus"],
}

_SYSTEM_PROMPT = """You are closing out a cross-examination training session for a
junior litigator. Write like a courtroom, not a coach: terse, sober, no
exclamation points, no gamified language ("great job!", "level up"), no
superlatives.

You get the full transcript and the rubric events already scored during the
session (each with a [D-xx] citation). Produce:
- amta_score: an integer 1-10 on the AMTA 10-point scale (Excellent/Average/
  Poor), reflecting overall witness control, objection timing, and
  impeachment execution across the session.
- headline: one short sentence in the voice of "Session closed." — factual,
  not celebratory.
- moments: exactly the two most important moments from the session, each as
  a short verbatim-style excerpt from the transcript, one sentence on why it
  mattered, and the [D-xx] id it relates to (reuse a dxx that was already
  scored during the session — never invent one).
- practice_focus: one concrete, specific recommendation for what to drill
  next session. Not generic ("keep practicing") — name the actual technique.

If a "User's requested focus" line is given below, practice_focus (or the
headline) should honestly reflect whether the session actually engaged that
focus area — do not claim it was covered if the transcript doesn't show it.
"""


@dataclass
class Moment:
    excerpt: str
    why_it_matters: str
    dxx: str


@dataclass
class Debrief:
    amta_score: int
    headline: str
    moments: list[Moment] = field(default_factory=list)
    practice_focus: str = ""
    usage_metadata: Optional[object] = None


class DebriefAgent:
    def __init__(self, api_key: Optional[str] = None):
        self._client = genai.Client(
            api_key=api_key or os.environ.get("GOOGLE_API_KEY")
        )

    def build_sync(self, transcript: str, scored_events: list[dict], focus: Optional[str] = None) -> Debrief:
        contents = (
            f"Transcript:\n{transcript}\n\n"
            f"Scored rubric events during the session:\n"
            f"{json.dumps(scored_events, indent=2)}"
        )
        if focus:
            contents += f"\n\nUser's requested focus for this session: {focus}"
        response = self._client.models.generate_content(
            model=DEBRIEF_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.2,
            ),
        )
        return self._parse(response.text, response.usage_metadata)

    async def build(self, transcript: str, scored_events: list[dict], focus: Optional[str] = None) -> Debrief:
        contents = (
            f"Transcript:\n{transcript}\n\n"
            f"Scored rubric events during the session:\n"
            f"{json.dumps(scored_events, indent=2)}"
        )
        if focus:
            contents += f"\n\nUser's requested focus for this session: {focus}"
        response = await self._client.aio.models.generate_content(
            model=DEBRIEF_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.2,
            ),
        )
        return self._parse(response.text, response.usage_metadata)

    def _parse(self, text: str, usage_metadata=None) -> Debrief:
        data = json.loads(text)
        moments = [Moment(**m) for m in data.get("moments", [])]
        return Debrief(
            amta_score=data["amta_score"],
            headline=data["headline"],
            moments=moments,
            practice_focus=data["practice_focus"],
            usage_metadata=usage_metadata,
        )
