"""The AI's verdict must survive the next poll.

``check_unsafe_parking`` runs every 30 minutes.  Its classification lived
inside an ``if not ai_analysis:`` branch — i.e. FIRST DETECTION ONLY — so
on every pass after the first the branch was skipped, ``loc_class`` fell
back to address keywords, and the upsert overwrote the AI's answer.

Measured on live data before the fix:

    stored=unknown   AI said=UNSAFE  HIGH      1,932
    stored=unknown   AI said=UNSAFE  MEDIUM       41
    stored=unknown   AI said=SAFE    HIGH         27
    stored=unknown   AI said=(unparseable)        42

1,973 rows carried ``CLASSIFICATION: UNSAFE / CONFIDENCE: HIGH`` in the
same row that called them ``unknown``.  ``alert_level`` derives from the
class, so a truck on a highway shoulder was correctly identified, then
downgraded half an hour later, and never escalated to warning or critical
on duration.  Nothing errored; the reasoning sat right there beside the
wrong verdict.

These tests are on the PURE function, deliberately.  Driving the whole
check loop needs Samsara, an LLM and a scheduler; the defect was never in
that machinery — it was that the parse only ran once.  Making the parse a
pure function is what let the fix be self-healing (every affected row
re-derives its own stored verdict on the next poll, no backfill), and it
is also what makes it testable without any of that.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

from features.parking.classifier import classify_from_ai  # noqa: E402

UNSAFE_HIGH = (
    "CLASSIFICATION: UNSAFE\n"
    "CONFIDENCE: HIGH\n"
    "REASON: The truck is parked on the paved shoulder of an interstate "
    "interchange ramp, with no designated parking."
)
SAFE_HIGH = (
    "CLASSIFICATION: SAFE\nCONFIDENCE: HIGH\n"
    "REASON: A marked truck stop with fuel islands and parking bays."
)
SAFE_LOW = (
    "CLASSIFICATION: SAFE\nCONFIDENCE: LOW\n"
    "REASON: Possibly a lot, imagery is unclear."
)


class TestStoredVerdictIsRecoverable:
    """The re-check path re-derives what the AI already said."""

    def test_unsafe_high_is_recovered(self):
        """THE regression: 1,932 rows were stored 'unknown' from this."""
        assert classify_from_ai(UNSAFE_HIGH) == ("unsafe", "HIGH")

    def test_unsafe_medium_is_still_unsafe(self):
        text = UNSAFE_HIGH.replace("CONFIDENCE: HIGH", "CONFIDENCE: MEDIUM")
        assert classify_from_ai(text) == ("unsafe", "MEDIUM")

    def test_confident_safe_is_recoverable_so_the_caller_can_resolve(self):
        """27 stored rows carry SAFE/HIGH — the path must handle it."""
        assert classify_from_ai(SAFE_HIGH) == ("safe", "HIGH")

    def test_low_confidence_safe_stays_under_observation(self):
        assert classify_from_ai(SAFE_LOW) == ("unknown", "LOW")


class TestTheSubstringTrap:
    '''"unsafe" CONTAINS "safe" — order of the two tests decides everything.'''

    def test_unsafe_is_not_read_as_safe(self):
        cls, _ = classify_from_ai(UNSAFE_HIGH)
        assert cls == "unsafe", (
            "the safe branch matched an UNSAFE verdict — reversing those two "
            "conditions silently marks every unsafe stop as safe and resolves it"
        )

    def test_prose_mentioning_both_prefers_unsafe(self):
        text = (
            "CLASSIFICATION: UNSAFE\nCONFIDENCE: HIGH\n"
            "REASON: There is a safe truck stop two miles away, but the "
            "vehicle is not in it."
        )
        assert classify_from_ai(text)[0] == "unsafe"


class TestInconclusiveIsNotKeywordFallback:
    """An answered-but-unusable analysis is different from no analysis."""

    def test_unparseable_returns_empty_not_a_guess(self):
        # 42 live rows look like this.  The caller turns "" into 'unknown'
        # on the re-check path — NOT into keyword_class, because the AI was
        # called precisely to overrule address keywords.
        assert classify_from_ai("I cannot determine this from the image") == ("", "")

    def test_empty_analysis_returns_empty(self):
        assert classify_from_ai("") == ("", "")
        assert classify_from_ai(None or "") == ("", "")

    def test_confidence_survives_even_without_a_classification(self):
        cls, conf = classify_from_ai("CONFIDENCE: MEDIUM\nREASON: hard to say")
        assert cls == ""
        assert conf == "MEDIUM"


class TestCheckLoopUsesIt:
    """Guard the wiring, not just the function."""

    def test_check_imports_the_shared_parser(self):
        import features.parking.check as check
        assert hasattr(check, "classify_from_ai"), (
            "check.py must classify through the shared function; an inline "
            "copy is how this drifted out of the re-check path in the first place"
        )

    def test_check_no_longer_parses_inline(self):
        """An inline re-implementation would reintroduce the split."""
        import inspect
        import features.parking.check as check
        src = inspect.getsource(check)
        assert '"unsafe" not in ai_lower' not in src, (
            "check.py is parsing the AI text inline again — that is exactly "
            "the shape that only ran on first detection"
        )
