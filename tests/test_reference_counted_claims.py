"""`docs/reference.md`'s counted claims, pinned against the code they describe.

Two claims in this file have drifted from the code before: the advisory table
said "exactly three" while `ADVISORY` held four, and the `research` profile's
marker column said `(explicit --profile)` while `_PROFILE_MARKERS` gave it
real auto-detection. Both are read from the code — never re-typed here — so
either drifting again fails this suite instead of the doc.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx.commands import ADVISORY  # noqa: E402
from ctx.detect import _PROFILE_MARKERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs" / "reference.md"

_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def read():
    return REFERENCE.read_text(encoding="utf-8")


class AdvisoryCountMatchesCode(unittest.TestCase):
    def test_doc_states_the_actual_count(self):
        # Markdown hard-wraps at arbitrary columns, so "exactly" and its
        # number can be split across lines; collapse whitespace before
        # matching so a wrap doesn't produce a false failure.
        text = " ".join(read().lower().split())
        count = len(ADVISORY)
        word = _NUMBER_WORDS.get(count, str(count))
        self.assertIn(
            f"exactly {word}",
            text,
            f"docs/reference.md should say 'exactly {word}' advisory "
            f"conditions to match len(ADVISORY) == {count}",
        )

    def test_doc_names_every_advisory_member(self):
        text = read()
        for member in ADVISORY:
            self.assertIn(
                f"`{member}`",
                text,
                f"docs/reference.md's advisory table is missing `{member}`",
            )


class ResearchMarkersMatchCode(unittest.TestCase):
    def test_doc_does_not_claim_explicit_profile_only(self):
        text = read()
        self.assertNotIn(
            "*(explicit `--profile`)*",
            text,
            "docs/reference.md still claims research has no auto-detection, "
            "but ctx.detect._PROFILE_MARKERS gives it real markers",
        )

    def test_doc_names_every_research_marker(self):
        text = read()
        research_markers = [
            marker for profile, marker, weight in _PROFILE_MARKERS
            if profile == "research"
        ]
        self.assertTrue(research_markers, "expected at least one research marker")
        for marker in research_markers:
            self.assertIn(
                marker,
                text,
                f"docs/reference.md's profile table is missing research "
                f"marker {marker!r}",
            )


if __name__ == "__main__":
    unittest.main()
