"""`shell | .` journal noise: Bash targets that name no file are not journalled.

The sign-off still clears for them; only the journal line is suppressed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import hooks  # noqa: E402
from support import Fixture  # noqa: E402

# Recorded before the change; `_bash_targets` must keep returning exactly these.
TARGETS = [
    ("echo x > out/a.txt", ["out/a.txt"]),
    ("echo x >> log.txt", ["log.txt"]),
    ("ls -la", []),
    ("sed -i 's/a/b/' src/x.py", ["src/x.py"]),
    ("git checkout src/a.py", ["src/a.py"]),
    ("git status", []),
    ("rm -rf build/", ["build/"]),
    ("mkdir -p /abs/dir", ["/abs/dir"]),
    ("cp a.txt b.txt", ["a.txt", "b.txt"]),
    ("cat <<EOF > notes.md\nhi\nEOF", ["notes.md"]),
    ("touch ./", ["./"]),
    ("cd x && echo hi > /dev/null", []),
    ("tee out.log < in.log", ["out.log", "in.log"]),
    ("python3 build.py --out=dist", ["build.py"]),
    ("pytest tests/", []),
]


# `_bash_targets` recognises a path by a `/` or a `.`, so a Windows absolute path
# (`C:\Users\...`) is not seen as a target at all and these three cases would
# pass, or fail, for a reason unrelated to the suppression they exist to check.
needs_posix_paths = unittest.skipIf(
    sys.platform == "win32", "shell targets are POSIX-style paths; see above")


class TestShellNoise(Fixture):
    def journal_text(self):
        return "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(self.layout.journal.glob("*.md"))
        )

    def bash(self, command):
        self.run_hook("PostToolUse", tool_name="Bash", tool_input={"command": command})

    @needs_posix_paths
    def test_project_root_target_is_not_journalled(self):
        before = self.journal_text()
        self.bash(f"mkdir -p {self.root}")  # positive control: was `shell | .`
        self.assertNotIn("shell | .", self.journal_text()[len(before):])
        self.assertEqual(self.journal_text(), before)

    @needs_posix_paths
    def test_outside_target_is_not_journalled(self):
        before = self.journal_text()
        self.bash(f"touch {self.untracked}/x.txt")
        self.assertEqual(self.journal_text(), before)

    def test_real_paths_still_journal(self):
        self.bash("echo x > out/a.txt")
        self.bash("git checkout src/b.py")
        self.bash("sed -i 's/a/b/' src/c.py")
        text = self.journal_text()
        for rel in ("out/a.txt", "src/b.py", "src/c.py"):
            self.assertIn(f"shell | {rel}", text)

    @needs_posix_paths
    def test_signoff_still_cleared_when_journal_suppressed(self):
        self.assertEqual(self.cli("task", "new", "t1")[0], 0)
        from ctx import state, work
        item = work.active(self.layout, state.load(self.layout))
        self.assertIsNotNone(item)
        item.doc.meta["verified"] = True
        item.doc.write(item.path)
        self.bash(f"mkdir -p {self.root}")
        item = work.active(self.layout, state.load(self.layout))
        self.assertFalse(item.doc.meta.get("verified"))
        self.assertIn("sign-off cleared", self.journal_text())
        self.assertNotIn("shell | .", self.journal_text())

    def test_bash_targets_table_unchanged(self):
        for command, expected in TARGETS:
            with self.subTest(command=command):
                self.assertEqual(hooks._bash_targets(command), expected)


if __name__ == "__main__":
    unittest.main()
