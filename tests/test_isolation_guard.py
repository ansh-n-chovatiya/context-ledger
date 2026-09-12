"""The guard in `support.py` fires on this checkout's own `.ctx/`.

A test once escaped its fixture and overwrote this repository's own
`.ctx/.gitignore` with a single `*` — which, committed, would have gitignored
the entire ledger. Nothing in the suite noticed; a human reading the diff did.

A guard that has never been seen to fire is the fail-green shape this project
keeps finding, so every test here deliberately drives a write at the real
`.ctx/` and asserts it is caught — before anything actually lands on disk —
and a companion test proves the same call is untouched when it targets a
fixture's own ledger instead.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import atomic, frontmatter  # noqa: E402
from support import Fixture, RealLedgerWriteError, _REAL_CTX  # noqa: E402


class TheGuardCatchesAWriteToTheRealLedger(unittest.TestCase):
    """Positive control: each of the write paths the ledger itself uses,
    pointed at this checkout's real `.ctx/`, is caught before it lands."""

    CANARY = _REAL_CTX / ".gitignore"

    def setUp(self):
        # Proof this is the genuine article, not a fixture standing in for
        # it, and the baseline the "untouched" assertions below compare
        # against.
        self.assertTrue(
            _REAL_CTX.is_dir(),
            "this checkout has no .ctx/ — the guard has nothing real to "
            "protect and this test would prove nothing",
        )
        self.before = self.CANARY.read_bytes()

    def assertUntouched(self):
        self.assertEqual(
            self.CANARY.read_bytes(), self.before,
            "the guard raised but the real .gitignore changed anyway — it "
            "caught the write too late to matter",
        )

    def test_atomic_write_text_is_caught(self):
        # Caught at the leading `path.parent.mkdir(exist_ok=True)` — before
        # the temp file that would carry the payload is even created — which
        # is an earlier, stricter catch than the final `os.replace`, not a
        # different one: both name this checkout's real `.ctx/`.
        with self.assertRaises(RealLedgerWriteError) as caught:
            atomic.write_text(self.CANARY, "*")
        self.assertIn(str(_REAL_CTX), str(caught.exception))
        self.assertUntouched()

    def test_frontmatter_document_write_is_caught(self):
        doc = frontmatter.Document({"ctx_schema": 1}, "body\n")
        with self.assertRaises(RealLedgerWriteError) as caught:
            doc.write(self.CANARY)
        self.assertIn(str(_REAL_CTX), str(caught.exception))
        self.assertUntouched()

    def test_a_plain_path_write_text_is_caught(self):
        with self.assertRaises(RealLedgerWriteError):
            self.CANARY.write_text("*", encoding="utf-8")
        self.assertUntouched()

    def test_a_plain_open_in_write_mode_is_caught(self):
        with self.assertRaises(RealLedgerWriteError):
            open(self.CANARY, "w", encoding="utf-8")
        self.assertUntouched()

    def test_a_new_directory_under_the_real_ledger_is_caught(self):
        target = _REAL_CTX / "isolation-guard-canary-dir"
        self.assertFalse(target.exists(), "leftover from a previous run")
        with self.assertRaises(RealLedgerWriteError):
            target.mkdir()
        self.assertFalse(target.exists())

    def test_reading_the_real_ledger_is_still_free(self):
        """Criterion 4's other half: the guard is a write guard. A test that
        only reads this checkout's `.ctx/` — as several in this suite
        legitimately do, to corroborate a check against the real thing —
        must not trip it."""
        self.assertIn(b"runtime/", self.before)


class TheGuardLeavesAFixturesLedgerFree(Fixture):
    """Negative control: the same calls, aimed at a fixture's own ledger
    instead, must be completely unaffected — get this backwards and every
    other test in the suite breaks with it."""

    def test_atomic_write_text_reaches_a_fixtures_ledger(self):
        target = self.layout.root / "isolation-guard-canary"
        atomic.write_text(target, "fixtures stay free")
        self.assertEqual(target.read_text(encoding="utf-8"), "fixtures stay free")

    def test_a_plain_path_write_text_reaches_a_fixtures_ledger(self):
        target = self.layout.root / "isolation-guard-canary-2"
        target.write_text("fixtures stay free", encoding="utf-8")
        self.assertEqual(target.read_text(encoding="utf-8"), "fixtures stay free")

    def test_mkdir_reaches_a_fixtures_ledger(self):
        target = self.layout.root / "isolation-guard-canary-dir"
        target.mkdir()
        self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
