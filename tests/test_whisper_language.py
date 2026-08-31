"""Regression test for BUG 2 (round 4, user finding 2026-08-31): a German
Rheinwerk session's "counsel's whisper" came back in English, because
RubricScorer's whisper addendum never told the model which language to write
it in — it just answered in the language it was asked in (English).

The fix reuses the same BCP-47 code server/app.py already feeds
SpeechConfig (case_language_code(), see witness_agent/agent.py) instead of
inventing a second language field. Verified here purely as a prompt-string
check (no live-API call, no network) — the actual model behavior is a live
concern the round's Live-audio eval set / human mic pass covers, not
something a unit test can observe.

Run: python -m pytest tests/test_whisper_language.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rubric_scorer.scorer import RubricScorer
from witness_agent.agent import case_language_code, load_case


def _whisper_addendum(case_id: str, *, reverse: bool = False) -> str:
    case = load_case(case_id)
    scorer = RubricScorer(case, reverse=reverse, language_code=case_language_code(case))
    scorer.set_whisper(True)
    return scorer._system_instruction()


def test_german_case_whisper_addendum_asks_for_german():
    instruction = _whisper_addendum("discovery_call_de")
    assert 'BCP-47 code "de-DE"' in instruction


def test_german_case_reverse_whisper_addendum_asks_for_german():
    instruction = _whisper_addendum("discovery_call_de", reverse=True)
    assert 'BCP-47 code "de-DE"' in instruction


def test_spanish_case_whisper_addendum_asks_for_spanish():
    instruction = _whisper_addendum("lopez_v_meridian_construction")
    assert 'BCP-47 code "es-US"' in instruction


def test_english_case_whisper_addendum_is_unchanged():
    # Rubric criteria stay English regardless (case has no reverse block, so
    # only forward mode applies here) — no language directive should be
    # injected, matching pre-fix behavior for English cases.
    instruction = _whisper_addendum("martinez_v_nordbay")
    assert "BCP-47 code" not in instruction


def test_whisper_disabled_never_gets_a_language_directive():
    case = load_case("discovery_call_de")
    scorer = RubricScorer(case, language_code=case_language_code(case))
    # whisper_enabled defaults to False — no addendum, no language directive.
    assert "BCP-47 code" not in scorer._system_instruction()


def test_rubric_citation_prompt_stays_english_for_a_german_case():
    # Bewusst NICHT in dieser Runde: the technique/rubric column stays
    # English even though the whisper field now speaks German.
    instruction = _whisper_addendum("discovery_call_de")
    assert "You are a cross-examination rubric judge" in instruction
