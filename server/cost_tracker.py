"""Per-session cost telemetry (Demo-Drehbuch Close beat, brief 1.6).

Accumulates real `usage_metadata` token counts pulled off the actual Gemini
responses already flowing through the session — never an extrapolated or
estimated figure. Two different pricing bases are in play, so they're kept
separate instead of blended into one misleading number:

- RubricScorer + DebriefAgent both call `gemini-3.7-flash` as plain
  text-in/text-out requests, which Google prices per token. A USD estimate
  for those calls is computed from the published per-1M-token rate.
  Source: https://ai.google.dev/gemini-api/docs/pricing (fetched
  2026-08-29) — gemini-3.7-flash paid tier, standard: input $0.75 / 1M
  tokens, output $3.75 / 1M tokens (through 2026-12-31).
- WitnessAgent runs on `gemini-3.1-flash-live-preview`, a Live/bidi audio
  model Google prices per minute of audio, not per token. Its
  `usage_metadata` token counts are real and reported, but converting them
  to USD would require a token-to-audio-duration assumption this module
  has no real data for — so witness cost is reported as token counts only,
  with no dollar figure, rather than a fabricated one.
"""

from dataclasses import dataclass, field

# gemini-3.7-flash paid tier, standard, per ai.google.dev/gemini-api/docs/pricing
# (checked 2026-08-29; rate holds through 2026-12-31).
GEMINI_3_7_FLASH_INPUT_USD_PER_1M = 0.75
GEMINI_3_7_FLASH_OUTPUT_USD_PER_1M = 3.75
PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (gemini-3.7-flash, paid tier, standard, checked 2026-08-29)"


@dataclass
class _Bucket:
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, usage_metadata) -> None:
        if usage_metadata is None:
            return
        self.prompt_tokens += usage_metadata.prompt_token_count or 0
        self.candidates_tokens += usage_metadata.candidates_token_count or 0
        self.total_tokens += usage_metadata.total_token_count or 0
        self.calls += 1

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "candidates_tokens": self.candidates_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
        }


class CostTracker:
    """Accumulates real token usage for one session, split by source."""

    def __init__(self) -> None:
        self.witness = _Bucket()
        self.scorer = _Bucket()
        self.debrief = _Bucket()

    def add_witness(self, usage_metadata) -> None:
        self.witness.add(usage_metadata)

    def add_scorer(self, usage_metadata) -> None:
        self.scorer.add(usage_metadata)

    def add_debrief(self, usage_metadata) -> None:
        self.debrief.add(usage_metadata)

    def _token_priced_usd(self) -> float:
        """USD for the token-priced (gemini-3.7-flash) calls only."""
        prompt_tokens = self.scorer.prompt_tokens + self.debrief.prompt_tokens
        candidates_tokens = self.scorer.candidates_tokens + self.debrief.candidates_tokens
        return (
            prompt_tokens / 1_000_000 * GEMINI_3_7_FLASH_INPUT_USD_PER_1M
            + candidates_tokens / 1_000_000 * GEMINI_3_7_FLASH_OUTPUT_USD_PER_1M
        )

    def as_payload(self) -> dict:
        total_tokens = (
            self.witness.total_tokens + self.scorer.total_tokens + self.debrief.total_tokens
        )
        return {
            "witness_tokens": self.witness.as_dict(),
            "scorer_tokens": self.scorer.as_dict(),
            "debrief_tokens": self.debrief.as_dict(),
            "total_tokens": total_tokens,
            "text_calls_usd_estimate": round(self._token_priced_usd(), 4),
            "pricing_source": PRICING_SOURCE,
            "note": (
                "USD estimate covers only the token-priced gemini-3.7-flash "
                "calls (rubric scorer + debrief). The witness runs on a "
                "Live/bidi audio model Google prices per minute of audio, "
                "not per token — its token counts are real but are not "
                "converted to a dollar figure here."
            ),
        }
