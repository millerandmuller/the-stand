"""Regression test for BUG 2 (2026-08-31 mic-test round 3) — third attempt.

The doubled transcript survived two rounds of fixes because both were built
by reading the reconciliation code rather than by looking at what the Live
API actually sends. This round instrumented it instead
(`THE_STAND_TRANSCRIPT_DIAG=1`, real reverse-mode session against
`sales_discovery_call`, real microphone audio) and captured the shape that
was doubling the screen.

The captured evidence, verbatim, is CAPTURED_ACCUMULATED / CAPTURED_FINAL
below: at turn end the API re-sends the ENTIRE turn as a single chunk, lightly
revised now that it has heard the whole utterance. The 612-char accumulation
and the 615-char final chunk differ by exactly one inserted word — "so".

    ...limited slots open for installations this quarter, I wanted to make...
    ...limited slots open for installations this quarter, so I wanted to make...

That chunk is not a substring of the accumulation (so the round-2
`chunk in accumulated` check misses it) and does not start with it (so the
`startswith` check misses it too), which means it necessarily fell into the
`accumulated + chunk` branch and printed the entire turn twice — matching the
user's screenshots ("comes around 500k" / "comes in around 500k",
"I figured" / "so I figured") exactly.

These tests pin that captured case, the protocol change it forced (a
`replace` flag carrying the full corrected turn, because a delta cannot
express an edit in the middle of already-sent text), and the four ordinary
chunk shapes that must keep working unchanged.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import _is_restatement, _reconcile_transcript_chunk  # noqa: E402

CAPTURED_ACCUMULATED = (
    "Hey, thanks so much for taking the time today. I've been really looking "
    "forward to showing you what we're doing at Vantage Robotics. Honestly, "
    "we're helping operations like yours completely revolutionize their "
    "throughput, often seeing like a 20% jump in efficiency within the first "
    "quarter. We've actually only got limited slots open for installations "
    "this quarter, I wanted to make sure you had the chance to see if this "
    "was a fit. Typically, our systems are going for around half a million, "
    "but we can definitely talk about financing. Where are you seeing the "
    "biggest constraints in your warehousing right now?"
)
CAPTURED_FINAL = CAPTURED_ACCUMULATED.replace(
    "this quarter, I wanted", "this quarter, so I wanted"
)


def test_captured_chunks_are_the_shape_the_old_code_could_not_catch():
    """Guards the premise, so this test can never quietly pass for the wrong
    reason: if either old check would have matched, the bug analysis is wrong."""
    assert CAPTURED_FINAL not in CAPTURED_ACCUMULATED
    assert not CAPTURED_FINAL.startswith(CAPTURED_ACCUMULATED)
    assert CAPTURED_FINAL != CAPTURED_ACCUMULATED
    assert len(CAPTURED_ACCUMULATED) == 612
    assert len(CAPTURED_FINAL) == 615


def test_captured_restatement_replaces_instead_of_doubling():
    acc, text, replace = _reconcile_transcript_chunk(
        CAPTURED_ACCUMULATED, CAPTURED_FINAL
    )
    assert replace is True
    assert acc == CAPTURED_FINAL
    assert text == CAPTURED_FINAL
    # The whole point: the turn is on screen once, in its corrected form.
    assert acc.count("Hey, thanks so much for taking the time today.") == 1
    assert "so I wanted to make sure" in acc


def test_old_behaviour_would_have_doubled_it():
    """Documents the defect this fixes: naive append produces the screenshot."""
    doubled = CAPTURED_ACCUMULATED + CAPTURED_FINAL
    assert doubled.count("Hey, thanks so much for taking the time today.") == 2
    assert len(doubled) > 1200


def test_examiner_side_restatement_too():
    """The user's screenshot shows the YOU line doubled as well, so the input
    transcription takes the identical path — same function, both directions."""
    acc = "Mr. Vantage, you told my team the deployment would come in around 500k."
    revised = "Mr. Vantage, you told my team the deployment would come in at around 500k."
    new_acc, text, replace = _reconcile_transcript_chunk(acc, revised)
    assert replace is True
    assert new_acc == revised
    assert text == revised


def test_normal_incremental_delta_still_appends():
    """The overwhelmingly common case: a few new words against a long turn."""
    base = CAPTURED_ACCUMULATED[:400]
    assert " for financing next" not in base  # guard the fixture
    acc, text, replace = _reconcile_transcript_chunk(base, " for financing next")
    assert replace is False
    assert text == " for financing next"
    assert acc == base + " for financing next"


def test_cumulative_resend_still_emits_only_the_suffix():
    acc, text, replace = _reconcile_transcript_chunk("Hello", "Hello world")
    assert (acc, text, replace) == ("Hello world", " world", False)


def test_exact_and_substring_repeats_still_emit_nothing():
    for accumulated, chunk in (
        ("Hello world", "Hello world"),   # exact repeat
        ("Hello world", "world"),         # suffix repeat
        ("Hello world", "Hello"),         # strict-prefix shrink
        ("foo bar baz", "bar"),           # middle substring
    ):
        acc, text, replace = _reconcile_transcript_chunk(accumulated, chunk)
        assert (acc, text, replace) == (accumulated, "", False), (accumulated, chunk)


def test_empty_inputs():
    assert _reconcile_transcript_chunk("anything", "") == ("anything", "", False)


# --------------------------------------------------------------------------
# The OTHER half of BUG 2, found by the adversarial review: cross-turn
# accumulation on the examiner line.
#
# The server clears pending_examiner_text/current_witness_text at
# turn_complete, but the browser gets no turn_complete signal — so the first
# chunk of every new turn arrived with replace=False and the client appended
# it to the previous turn's text. By question three the "YOU —" line was a
# run-on of every question ever asked, with no separator. That is what the
# user's Rheinwerk screenshot actually shows: "...wo Sie mir dasHaben sie",
# where the missing space between "das" and "Haben" is the concatenation
# seam. Distinct from the restatement bug above, and it survived the first
# fix of this round because that fix only looked inside a single turn.
# --------------------------------------------------------------------------


def test_first_chunk_of_a_turn_replaces_so_the_client_can_drop_the_last_turn():
    acc, text, replace = _reconcile_transcript_chunk("", "Can anyone corroborate that?")
    assert replace is True, "a turn's first chunk must replace, or the client appends it to the previous turn"
    assert acc == text == "Can anyone corroborate that?"


def _simulate_session(turns):
    """Runs whole turns through the real server function and the real client
    accumulator rule, and returns what would be ON SCREEN at the end."""
    server_acc = ""
    screen = ""
    for turn in turns:
        server_acc = ""  # the server clears at turn_complete
        for chunk in turn:
            server_acc, text, replace = _reconcile_transcript_chunk(server_acc, chunk)
            if text:
                screen = text if replace else screen + text
    return screen


def test_three_questions_do_not_pile_up_on_screen():
    screen = _simulate_session([
        ["Where were you", " on the night of the 14th?"],
        ["Can anyone", " corroborate that?"],
        ["What is your", " roommate's name?"],
    ])
    assert screen == "What is your roommate's name?"
    assert "Where were you" not in screen
    assert "corroborate" not in screen


def test_witness_line_does_not_pile_up_either():
    screen = _simulate_session([
        ["I was", " at home all evening."],
        ["No,", " nobody else was there."],
    ])
    assert screen == "No, nobody else was there."


# --------------------------------------------------------------------------
# Restatement detection must never erase text on a SHORT accumulation.
# Replacing is destructive and appending is not, so the tie goes to appending.
# --------------------------------------------------------------------------


def test_short_accumulation_never_triggers_a_destructive_replace():
    for accumulated, chunk in (
        ("the", " then"),           # would have produced " then", losing "the"
        ("no", " I said no"),       # mid-string coincidence, not a restatement
        ("It was", " It was not"),
        ("he", " the"),
    ):
        acc, text, replace = _reconcile_transcript_chunk(accumulated, chunk)
        assert replace is False, (accumulated, chunk, acc)
        assert accumulated in acc, f"{accumulated!r} was erased -> {acc!r}"


def test_short_exact_duplicate_is_still_deduped():
    """The one short case that may still "replace": the same word again,
    differing only in punctuation/case. Nothing can be lost by that."""
    acc, text, replace = _reconcile_transcript_chunk("Yes", " yes")
    assert replace is True
    assert acc == " yes"


def test_restatement_detection_does_not_fire_on_a_genuine_short_delta():
    """A delta must never be mistaken for a restatement — that would erase
    everything said so far and leave only the last few words on screen."""
    assert not _is_restatement(CAPTURED_ACCUMULATED, " right now?")
    assert not _is_restatement(CAPTURED_ACCUMULATED, " Where are you seeing")
    assert not _is_restatement("The witness said he was there.", " Then he left.")


def test_restatement_detection_ignores_punctuation_only_differences():
    a = "So, I told him: we are not going to do that."
    b = "So I told him — we are not going to do that"
    assert _is_restatement(a, b)
    acc, text, replace = _reconcile_transcript_chunk(a, b)
    assert replace is True
    assert acc == b


def test_german_restatement():
    """Both languages take the same path (the demo runs EN and DE)."""
    a = (
        "Also, vielen Dank, dass Sie sich heute Zeit nehmen. Ich freue mich "
        "sehr, Ihnen zu zeigen, woran wir bei Vantage Robotics arbeiten, und "
        "wie wir Ihren Durchsatz deutlich verbessern koennen."
    )
    b = a.replace("Ich freue mich", "Ich freue mich wirklich")
    acc, text, replace = _reconcile_transcript_chunk(a, b)
    assert replace is True
    assert acc == b
    assert acc.count("Also, vielen Dank") == 1
