"""Regression test for the P1 found via real production logs (2026-08-29,
Cloud Run 18:20-18:25Z): a case dict with an explicit `"language": None`
(rather than an absent `language` key) crashed `case_language_code()` with
`AttributeError: 'NoneType' object has no attribute 'get'` inside
`server/app.py`'s `websocket_endpoint`, before the Live connection ever
opened — an ASGI crash at handshake, not a Live API error, and it produced
no audio in either direction. A second symptom (immediate `1008 Policy
Violation` from the Live API on other sessions) came from the same root
cause: an invalid/empty language code reaching `SpeechConfig`.

This never showed up on the original case files because they simply omit
the `language` key when unset. `witness_agent/case_generator.py`'s
`build_case_dict` was the one place that turned "no language" into an
explicit `None` value instead of an absent key — exactly the shape that
broke `case.get("language", {})` (that default only applies when the key
is *missing*, not when it's present-and-falsy).

Runs every shipped case file, plus a case built the same way
`build_case_dict` builds an uploaded one (no network call — a synthetic
`GeneratedCaseContent`, the real merge function, the real templates), through
the exact objects `server/app.py`'s `websocket_endpoint` builds at session
start: `case_language_code()` and (if a code is present) a real
`types.SpeechConfig`. No case may raise, and no case may produce an empty
string language code (empty-but-truthy-looking values are just as invalid
to the Live API as None).

Run: python -m pytest tests/test_case_language_handshake.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.genai import types

from witness_agent.agent import CASE_FILES, case_language_code, load_case
from witness_agent.case_generator import GeneratedCaseContent, build_case_dict


def _assert_handshake_safe(case_id: str, case: dict) -> None:
    code = case_language_code(case)  # must never raise
    assert code is None or (isinstance(code, str) and code.strip()), (
        f"case '{case_id}' produced an invalid language code: {code!r}"
    )
    speech_config = types.SpeechConfig(language_code=code) if code else None
    if code:
        assert speech_config.language_code == code


def test_every_shipped_case_file_survives_the_handshake():
    assert CASE_FILES, "no case files found — CASE_DIR glob is broken"
    for case_id in CASE_FILES:
        case = load_case(case_id)
        _assert_handshake_safe(case_id, case)


def test_generated_case_from_a_template_with_no_language_block_survives():
    # sales_discovery_call.yaml has no `language` key (English default) —
    # this is exactly the template shape that produced the crash before the
    # fix, via build_case_dict's old `template.get("language")` (= None,
    # explicitly set) instead of falling back to an explicit default.
    template = load_case("sales_discovery_call")
    content = GeneratedCaseContent(
        title="Regression Test Upload",
        card_summary="synthetic content for the language-handshake regression test",
        summary="synthetic content for the language-handshake regression test",
        affidavit="synthetic content for the language-handshake regression test",
        goals=["Ask an open-ended discovery question (p. 1)."],
    )
    generated = build_case_dict("sales", content, template, case_id="regression_test_upload")
    assert generated.get("language") is not None, (
        "a generated case must never carry an explicit None language — "
        "that exact shape is what crashed the handshake"
    )
    _assert_handshake_safe("regression_test_upload", generated)


def test_generated_case_from_a_template_with_a_language_block_keeps_it():
    # dissertation_defense.yaml now declares an explicit en-US block —
    # confirms build_case_dict passes a real language through unchanged
    # rather than only working by accident when the template lacks one.
    template = load_case("dissertation_defense")
    content = GeneratedCaseContent(
        title="Regression Test Defense Upload",
        card_summary="synthetic content for the language-handshake regression test",
        summary="synthetic content for the language-handshake regression test",
        affidavit="synthetic content for the language-handshake regression test",
        goals=["Explain why this methodology was appropriate (p. 2)."],
    )
    generated = build_case_dict("defense", content, template, case_id="regression_test_defense_upload")
    assert generated["language"]["code"] == "en-US"
    _assert_handshake_safe("regression_test_defense_upload", generated)


if __name__ == "__main__":
    test_every_shipped_case_file_survives_the_handshake()
    test_generated_case_from_a_template_with_no_language_block_survives()
    test_generated_case_from_a_template_with_a_language_block_keeps_it()
    print("PASS: all cases (shipped + generated) survive the language handshake")
