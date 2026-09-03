"""Cross-platform guards, found by running CI on Windows.

The suite passed on macOS and Linux while nine tests failed on Windows. Most were
POSIX assumptions in the tests themselves, but two were real: the ledger recorded
OS-native path separators into files that are committed and shared, and the state
lock gave up early enough to lose an update under contention.

These assert the product behaviour on every platform, so the next regression does
not have to wait for a Windows runner to surface it.
"""

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import journal, paths, state  # noqa: E402
from support import Fixture  # noqa: E402


class TestPathsAreWrittenPortably(Fixture):
    """`.ctx/` is committed. A Windows session writing `src\\a.py` where a mac
    session writes `src/a.py` is divergence in a tracked file: merge noise, and
    two spellings of one path for scope matching to disagree over."""

    def test_rel_never_emits_a_backslash(self):
        self.write("src/deep/auth.py", "x = 1\n")
        rendered = self.layout.rel(self.root / "src" / "deep" / "auth.py")
        self.assertEqual(rendered, "src/deep/auth.py")
        self.assertNotIn("\\", rendered)

    def test_rel_normalises_a_windows_style_input(self):
        """The separator in the *output* must not depend on the input's spelling
        or on which platform is asking."""
        layout = paths.Layout(self.layout.root)
        with unittest.mock.patch.object(os, "sep", "\\"):
            self.assertNotIn("\\", layout.rel("src\\deep\\auth.py"))

    def test_a_path_outside_the_repo_is_still_normalised(self):
        rendered = self.layout.rel(Path(self._outside.name) / "elsewhere.py")
        self.assertNotIn("\\", rendered)

    def test_journalled_paths_use_forward_slashes(self):
        self.write("src/a.py", "x = 1\n")
        self.run_hook("PostToolUse", tool_name="Write",
                      tool_input={"file_path": str(self.root / "src" / "a.py")})
        entries, _earlier = journal.tail(self.layout, 5)
        joined = "\n".join(entries)
        self.assertIn("src/a.py", joined)
        self.assertNotIn("src\\a.py", joined)

    def test_shell_targets_are_normalised_too(self):
        self.cli("task", "demo", "--objective", "x")
        self.run_hook("PostToolUse", tool_name="Bash",
                      tool_input={"command": "sed -i '' s/a/b/ src/deep/auth.py"})
        entries, _earlier = journal.tail(self.layout, 5)
        self.assertNotIn("\\", "\n".join(entries))


class TestLockIsPatientEnoughToBeWorthHaving(Fixture):
    """Giving up on the lock means taking the lost update it exists to prevent.
    CI lost one increment in eighty on Windows at the original five seconds."""

    def test_the_timeout_leaves_room_for_a_slow_filesystem(self):
        self.assertGreaterEqual(
            state.LOCK_TIMEOUT, 15.0,
            "too short a wait turns contention into silent data loss",
        )

    def test_a_stale_lock_is_still_reclaimed_before_the_timeout(self):
        self.assertGreater(
            state.LOCK_STALE_SECONDS, state.LOCK_TIMEOUT,
            "reclaiming sooner than the wait would steal a live lock",
        )


class TestNoPosixOnlyAssumptionsInVerifyCommands(unittest.TestCase):
    """The gate runs commands through the platform shell, which is cmd.exe on
    Windows. `true`, `sleep`, `test -f` and `[ ... ]` are not portable, and a
    test using one asserts nothing there."""

    ROOT = Path(__file__).resolve().parent

    BANNED = ('"run": "sleep ', '"run": "test -f', '"run": \'[ ', '; exit 1"')

    def test_no_test_declares_a_posix_only_verify_command(self):
        for path in sorted(self.ROOT.glob("test_*.py")):
            if path.name == Path(__file__).name:
                continue  # this file necessarily contains the literals
            text = path.read_text(encoding="utf-8")
            for banned in self.BANNED:
                with self.subTest(path=path.name, snippet=banned):
                    self.assertNotIn(
                        banned, text,
                        "use self.py(...) so the command runs on every platform",
                    )


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main(verbosity=2)
