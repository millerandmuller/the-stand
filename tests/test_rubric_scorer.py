"""Scripted-transcript tests for the RubricScorer + DebriefAgent (F3, F5),
covering test cases T-01 through T-06 from expert_dossier.md.

These use text input only (no audio/mic) — the point is to test the
scoring logic against the published rubric, not the voice pipeline. Each
scripted exchange makes a real gemini-3.7-flash call (text-only, fast/cheap
per the brief), so this needs GOOGLE_API_KEY in .env like the other tests.

Honesty note (read before trusting a PASS): T-01, T-05, and T-06 don't map
1:1 onto what RubricScorer does — see the per-test comments below for why,
and the summary printed at the end says so explicitly rather than papering
over it.

Run: python tests/test_rubric_scorer.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from rubric_scorer.debrief import DebriefAgent
from rubric_scorer.scorer import RubricScorer
from witness_agent.agent import load_case

case = load_case("martinez_v_nordbay")
scorer = RubricScorer(case)

results = []


def record(test_id, note, mapped, passed):
    results.append((test_id, note, mapped, passed))
    status = "PASS" if passed else "FAIL"
    mapped_tag = "" if mapped else " [DOES NOT MAP CLEANLY]"
    print(f"\n{test_id}{mapped_tag}: {status}\n  {note}")


def has_dxx(events, dxx):
    return any(e.dxx == dxx for e in events)


def any_violation(events):
    return any(e.violation for e in events)


def any_triggered(events):
    return any(e.triggered for e in events)


# T-01 (dossier): leading question in a simulated DIRECT exam should be
# flagged as improper (FRE 611(c) restricts leading to cross). Our case
# file's rubric only judges CROSS-examination technique — there is no
# "direct vs. cross" distinction in RubricScorer's inputs, so it cannot
# reproduce the dossier's exact T-01 scenario. What it CAN verify is the
# underlying skill: a leading question is recognized and correctly scored
# as *positive* technique when used in cross (the FRE 611(c) / D-01 half of
# the fact pattern), which is the only half applicable to this product.
print("=== T-01 (partial mapping — see note) ===")
result = scorer.score_exchange_sync(
    "You inspected the pallet before it left the dock that night, didn't you?",
    "Yes, I inspected it per protocol.",
)
mapped_ok = has_dxx(result.events, "D-01") and any_triggered(result.events)
record(
    "T-01",
    "Cross-exam leading question recognized as triggering D-01 (FRE 611(c)). "
    "Does NOT test the dossier's direct-exam framing — RubricScorer has no "
    "concept of direct vs. cross, so the original T-01 doesn't map cleanly.",
    mapped=False,
    passed=mapped_ok,
)

# T-02: open W-question in cross -> violation, control lost.
print("\n=== T-02 ===")
result = scorer.score_exchange_sync(
    "Why did you leave the loading dock early that night?",
    "I had a lot on my mind, there were other things going on, it wasn't just about the dock.",
)
passed = any_violation(result.events) and has_dxx(result.events, "D-01")
record("T-02", f"events={result.events}", mapped=True, passed=passed)

# T-03: compound question, multiple new facts in one question -> violation.
print("\n=== T-03 ===")
result = scorer.score_exchange_sync(
    "You saw the pallet arrive, checked the manifest, and then signed off without opening it, correct?",
    "I mean, that's roughly what happened, yes.",
)
passed = any_violation(result.events)
record("T-03", f"events={result.events}", mapped=True, passed=passed)

# T-04: correct impeachment sequence — confront with inconsistent fact,
# give the witness the chance to explain/deny before pressing further.
print("\n=== T-04 ===")
result = scorer.score_exchange_sync(
    "The loading dock log shows no scan entry for pallet 4471-B between 9 PM and end of shift — "
    "can you explain that gap, or do you deny the log is accurate?",
    "I... I don't have an explanation for that gap right now.",
)
passed = has_dxx(result.events, "D-06") and any_triggered(result.events)
record("T-04", f"events={result.events}", mapped=True, passed=passed)

# T-05 (dossier): witness offers hearsay; failure to object should lower the
# objection score. RubricScorer only sees one Q/A exchange at a time and has
# no persistent "did the examiner ever object" state across exchanges — it
# can recognize an objection WHEN one is made, but cannot infer a *missing*
# objection from a single exchange the way a full-session judge could. We
# test the half it can do: does it recognize a correct hearsay objection as
# triggering D-04 when the examiner actually makes one.
print("=== T-05 (partial mapping — see note) ===")
result = scorer.score_exchange_sync(
    "Objection — hearsay. That statement is being offered for the truth of what Maria said.",
    "(Witness pauses, no further testimony on that point.)",
)
mapped_ok = has_dxx(result.events, "D-04") and any_triggered(result.events)
record(
    "T-05",
    "Recognizes a hearsay objection as triggering D-04 (FRE 802) when the "
    "examiner actually makes one. Does NOT test detection of a *missed* "
    "objection across turns — RubricScorer is stateless per-exchange, so "
    "that half of the dossier's T-05 doesn't map without a session-level judge.",
    mapped=False,
    passed=mapped_ok,
)

# T-06 (dossier): session close shows a 10-point AMTA score citing the
# Ballot criteria. This is DebriefAgent's job, not RubricScorer's — testing
# it here against a small scripted transcript + the scored events collected
# above.
print("\n=== T-06 (tests DebriefAgent, not RubricScorer) ===")
debrief_agent = DebriefAgent()
transcript = (
    "Examiner: You inspected the pallet before it left the dock that night, didn't you?\n"
    "Witness: Yes, I inspected it per protocol.\n"
    "Examiner: The loading dock log shows no scan entry for pallet 4471-B between "
    "9 PM and end of shift — can you explain that gap?\n"
    "Witness: I don't have an explanation for that gap right now."
)
scored_events_payload = [
    {"criterion": e.criterion, "dxx": e.dxx, "triggered": e.triggered, "violation": e.violation, "note": e.note, "score_delta": e.score_delta}
    for e in result.events
]
debrief = debrief_agent.build_sync(transcript, scored_events_payload)
passed = 1 <= debrief.amta_score <= 10 and len(debrief.moments) >= 1
record(
    "T-06",
    f"amta_score={debrief.amta_score}, headline={debrief.headline!r}, "
    f"moments={len(debrief.moments)}, practice_focus={debrief.practice_focus!r}",
    mapped=True,
    passed=passed,
)

print("\n\n=== SUMMARY ===")
for test_id, note, mapped, passed in results:
    tag = "" if mapped else " [DOES NOT MAP CLEANLY — partial coverage only]"
    print(f"{test_id}: {'PASS' if passed else 'FAIL'}{tag}")

all_passed = all(p for _, _, _, p in results)
print("\nOVERALL:", "PASS" if all_passed else "FAIL (see per-test notes above)")

if __name__ == "__main__":
    # Guarded so `pytest tests/` (which imports this module rather than
    # running it as a script) doesn't treat a clean exit(0) as a crash.
    sys.exit(0 if all_passed else 1)
