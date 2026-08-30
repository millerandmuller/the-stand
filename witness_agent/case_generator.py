"""CaseGenerator (F16 — Bring Your Own Case): turns an uploaded PDF/text
document into a playable case file, once, at upload time.

Explicitly NOT run inside a live session (the brief's leitplanken: "NIE live
in der Session" — a multi-second Gemini call would stall the voice loop).
The document is sent to gemini-3.7-flash as inline data (native PDF support,
no separate File API upload needed at this size) and asked for exactly the
per-document parts a case needs: a persona's likely attack lines WITH
page/section citations into the document, an affidavit-style summary, and
display copy. Everything else (escalation ladder text, rubric, base
disposition/role) comes from a curated MODE_TEMPLATE — the same technique
rubric every case in that mode already uses (S-01..S-04 for sales, the
researched defense rubric for defense), never invented per-upload.

Only two modes are allowed (F16 leitplanken / brief Section 8): "defense"
and "sales". The legal cross-exam mode stays fiction-only (Rule 1.6, D-28) —
callers must reject "legal" before this module is ever reached.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types

GENERATOR_MODEL = "gemini-3.7-flash"

# MAX_UPLOAD_BYTES: generous per the brief ("Dokumentgröße großzügig,
# 1M-Token-Kontext"), but bounded well under Cloud Run's request-timeout and
# the container's 512Mi-OOM trap (execution/stack_limits.yaml) — a bound
# dissertation or a product briefing PDF is comfortably under this.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_ALLOWED_MODES = ("defense", "sales")

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "card_summary": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "affidavit": {"type": "STRING"},
        "goals": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reverse_affidavit": {"type": "STRING"},
        "reverse_goals": {"type": "ARRAY", "items": {"type": "STRING"}},
        "focus_note": {"type": "STRING"},
    },
    "required": [
        "title",
        "card_summary",
        "summary",
        "affidavit",
        "goals",
        "reverse_affidavit",
        "reverse_goals",
        "focus_note",
    ],
}

_SYSTEM_PROMPT = """You are preparing a training-sparring persona from an uploaded document, for a
voice cross-examination/discovery training tool called The Stand.

Mode: {mode_label}

{mode_instruction}
{focus_instruction}

Read the attached document and produce, in your own words, TWO persona angles for the
SAME document in one pass (F18 reverse mode reuses this document without a second
generation call):

FORWARD persona (the AI grills the user about the document):
- title: a short (<=8 word) name for this case, drawn from the document's own subject
- card_summary: one sentence (<=140 chars) teaser for a case-selection card
- summary: 2-3 sentences describing what this session is about
- affidavit: a short first-person-style statement of what the document claims/argues,
  written as something the persona would open with
- goals: 3-5 bullet points, each a LIKELY LINE OF ATTACK OR PROBING QUESTION the persona
  would use against this specific document, and EACH ONE MUST include a concrete citation
  back into the document (a page number, section heading, or a short quoted phrase from the
  document) — never a generic goal with no citation. If the document doesn't clearly support
  a citation for a goal, drop that goal rather than inventing a page/section reference.

REVERSE persona (the AI instead DEFENDS/SELLS the document, the user grills it):
- reverse_affidavit: a short first-person opening statement the AI would use if it were
  the document's own author/advocate presenting and standing behind it
- reverse_goals: 3-5 bullet points, each something the AI (as the document's author/
  advocate) would actively DO or ARGUE to defend/sell the document's content — again each
  one grounded in the document with a concrete citation where the document supports it; if
  a natural defense/sell point has no citable anchor in the document, phrase it as a
  general technique instead of inventing a page reference for it

- focus_note: one sentence stating whether (and how) the requested focus area, if any, was
  actually found and anchored in the document, or "no focus area was requested" if none was
  given, or an honest note that the requested focus wasn't clearly present in the document
  if it wasn't — never claim a focus was covered if it wasn't actually there

Never invent facts not in the document. Never produce a goal (forward or reverse) without a
real citation into the document text you were given, unless explicitly framed as an
uncited general technique per the rules above.
"""

_NO_FOCUS_INSTRUCTION = ""
_FOCUS_INSTRUCTION_TEMPLATE = """
Focus area requested by the user: "{focus}"
Anchor the majority of goals (forward) and reverse_goals (reverse) on this section of the
document, with page/section citations, IF the document actually supports it. If the
document does not clearly contain this section/topic, say so plainly in focus_note instead
of inventing citations to force a match — fall back to the document's actual content for
the goals in that case.
"""

_MODE_INSTRUCTION = {
    "defense": (
        "The persona is a doctoral dissertation defense committee, probing the uploaded "
        "dissertation for weak methodology, unanswered questions, and unacknowledged "
        "limitations — exactly the kind of scrutiny a real defense committee applies."
    ),
    "sales": (
        "The persona is a skeptical B2B buyer reading the uploaded product/briefing "
        "document, looking for the gap between what it claims and what it actually proves — "
        "exactly the kind of scrutiny a real skeptical prospect applies."
    ),
}

_MODE_LABEL = {"defense": "Dissertation Defense", "sales": "Sales Discovery"}


class UploadTooLargeError(ValueError):
    """Raised when the uploaded document exceeds MAX_UPLOAD_BYTES."""


class UnsupportedModeError(ValueError):
    """Raised when mode isn't one of the F16-allowed modes (defense, sales)."""


class GenerationFailedError(RuntimeError):
    """Raised when Gemini's response can't be parsed into the case schema."""


@dataclass
class GeneratedCaseContent:
    title: str
    card_summary: str
    summary: str
    affidavit: str
    goals: list[str]
    reverse_affidavit: str = ""
    reverse_goals: list[str] = None
    focus_note: str = ""

    def __post_init__(self):
        if self.reverse_goals is None:
            self.reverse_goals = []


def _validate_mode(mode: str) -> str:
    if mode not in _ALLOWED_MODES:
        raise UnsupportedModeError(
            f"Bring Your Own Case only supports modes {_ALLOWED_MODES!r} "
            f"(legal cross-exam stays fiction-only per Rule 1.6/D-28), got {mode!r}"
        )
    return mode


async def generate_case_content(
    mode: str,
    file_bytes: bytes,
    mime_type: str,
    focus: Optional[str] = None,
    api_key: Optional[str] = None,
) -> GeneratedCaseContent:
    """Runs the one-time, upload-time generation call. Never called from the
    live session path. Does not log file_bytes or the generated text — the
    leitplanken forbid logging the uploaded document's full content.

    `focus` (F19): optional freeform text naming where in the document the
    user wants to be grilled/defend — passed into the generation prompt so
    goals/reverse_goals anchor on that section, cite-or-GAP if the document
    doesn't actually support it (see focus_note)."""
    _validate_mode(mode)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(
            f"upload is {len(file_bytes)} bytes, over the {MAX_UPLOAD_BYTES}-byte limit"
        )

    client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
    focus = (focus or "").strip()
    focus_instruction = (
        _FOCUS_INSTRUCTION_TEMPLATE.format(focus=focus) if focus else _NO_FOCUS_INSTRUCTION
    )
    system_prompt = _SYSTEM_PROMPT.format(
        mode_label=_MODE_LABEL[mode],
        mode_instruction=_MODE_INSTRUCTION[mode],
        focus_instruction=focus_instruction,
    )
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type=mime_type, data=file_bytes)),
                    types.Part(text="Generate the case content for this document."),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0.3,
        ),
    )
    try:
        data = json.loads(response.text)
        goals = list(data["goals"])
        # A document the model couldn't extract citable attack lines from
        # (unreadable, empty, off-topic) correctly comes back with goals: []
        # per the system prompt's "drop the goal rather than invent a
        # citation" instruction — but a case with zero scripted goals is not
        # a playable case, it's a silent failure wearing a 200. Surface it
        # as a real generation failure instead of letting it become a case.
        if not goals:
            raise GenerationFailedError(
                "the document didn't yield any citable attack lines — "
                "try a different file or a clearer document"
            )
        # reverse_goals empty is not fatal the way forward goals are — reverse
        # mode is an additive feature (F18); if the model couldn't find
        # citable defend/sell angles, the upload still yields a playable
        # forward case, it just won't offer reverse mode (build_case_dict
        # only adds a `reverse` block when reverse_goals is non-empty).
        return GeneratedCaseContent(
            title=data["title"],
            card_summary=data["card_summary"],
            summary=data["summary"],
            affidavit=data["affidavit"],
            goals=goals,
            reverse_affidavit=data.get("reverse_affidavit", ""),
            reverse_goals=list(data.get("reverse_goals") or []),
            focus_note=data.get("focus_note", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GenerationFailedError(f"couldn't parse generated case content: {exc}") from exc


def build_case_dict(
    mode: str, content: GeneratedCaseContent, template: dict, case_id: str, focus: Optional[str] = None
) -> dict:
    """Merges generated per-document content into a mode's curated template
    (escalation ladder, rubric, disposition, short_role) to produce a full,
    schema-valid case dict — same shape `witness_agent.agent._validate_case`
    already enforces for the shipped case files. Nothing here invents a
    rubric criterion or an escalation line; only `title`/`summary`/
    `card_summary`/`affidavit`/`goals` come from the document.

    F18: if the template declares a `reverse` scaffold (role/escalation/
    techniques — sales_discovery_call.yaml and dissertation_defense.yaml
    both do) AND the generation produced non-empty `reverse_goals`, the
    uploaded case gets its own `reverse` block too — same one-document,
    two-personas generation the brief asks for, no second API call.
    F19: `focus` (the user's freeform focus request, if any) and the
    generator's own honest `focus_note` are carried onto the case so F17's
    briefing panel and the debrief can reflect them back to the user."""
    template_witness = template["witness"]
    case = {
        "case_name": f"{content.title} [uploaded]",
        "display_name": content.title,
        "case_number": None,
        "case_type": template["case_type"],
        "display_order": 90,
        "card_summary": content.card_summary,
        "summary": content.summary,
        "user_role": template.get("user_role"),
        "focus": (focus or "").strip() or None,
        "focus_note": content.focus_note or None,
        "witness": {
            "name": template_witness["name"],
            "role": template_witness["role"],
            "disposition": template_witness["disposition"],
            "short_role": template_witness["short_role"],
            "goals": content.goals,
            "affidavit": content.affidavit,
            "escalation": template_witness["escalation"],
        },
        "rubric": template["rubric"],
        # P1 fix: never persist an explicit `None` here. A generated case's
        # dict previously carried a literal `"language": None` whenever its
        # template had no language block, and `case_language_code()`
        # crashed the whole session at handshake on exactly that shape
        # (`None.get("code")`) — the bug never showed up on the raw
        # curated case files because they simply omit the key instead of
        # setting it to None. Falling back to the same explicit English
        # default every curated case now declares (Lopez-shaped: code/name)
        # closes that gap at the source, on top of case_language_code's own
        # defensive fix.
        "language": template.get("language") or {"code": "en-US", "name": "English"},
        "session_verb": template.get("session_verb"),
        "uploaded": True,
        "source_case_id": case_id,
    }
    template_reverse = template.get("reverse")
    if template_reverse and content.reverse_goals:
        case["reverse"] = {
            "role": template_reverse["role"],
            "short_role": template_reverse["short_role"],
            "user_role": template_reverse["user_role"],
            "disposition": template_reverse["disposition"],
            "affidavit": content.reverse_affidavit or template_reverse.get("affidavit", ""),
            "goals": content.reverse_goals,
            "escalation": template_reverse["escalation"],
            "opening_stage_direction": template_reverse["opening_stage_direction"],
            "techniques": template_reverse["techniques"],
        }
    return case
