import builtins
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from ctx import footprint as fp


def _w(path, text="", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(text)


def _snap(root):
    out = {}
    for d, _, fs in os.walk(root):
        out[d] = os.lstat(d).st_mtime_ns
        for f in fs:
            p = os.path.join(d, f)
            out[p] = (os.lstat(p).st_mtime_ns, os.lstat(p).st_size)
    return out


class T(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def p(self, *a):
        return os.path.join(self.root, *a)

    def test_measure_sorted_and_kinds(self):
        _w(self.p("CLAUDE.md"), "x" * 4001)
        _w(self.p(".claude", "rules", "sub", "r.md"), "y" * 40)
        _w(self.p("CLAUDE.local.md"), "z" * 8)
        items = fp.measure(self.root)
        self.assertEqual(items[0].path, "CLAUDE.md")
        self.assertEqual(items[0].tokens, 1001)
        kinds = {i.path: i.kind for i in items}
        self.assertEqual(kinds[".claude/rules/sub/r.md"], "rule")
        self.assertEqual(kinds["CLAUDE.local.md"], "local")
        self.assertEqual([i.tokens for i in items], sorted(
            [i.tokens for i in items], reverse=True))

    def test_home_none_touches_no_home(self):
        _w(self.p("CLAUDE.md"), "hi")
        home = os.path.expanduser("~")
        real = builtins.open
        seen = []

        def spy(f, *a, **k):
            seen.append(str(f))
            return real(f, *a, **k)
        with mock.patch.object(builtins, "open", spy):
            fp.check(self.root)
        self.assertFalse([s for s in seen if s.startswith(home)
                          and not s.startswith(self.root)])
        self.assertIn(os.path.join(self.root, "CLAUDE.md"), seen)

    def test_home_given_lists_user_file(self):
        h = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, h, True)
        _w(os.path.join(h, ".claude", "CLAUDE.md"), "abcd")
        items = fp.measure(self.root, home=h)
        self.assertEqual([(i.path, i.kind) for i in items],
                         [("~/.claude/CLAUDE.md", "user")])

    @unittest.skipIf(sys.platform == "win32", "symlinks")
    def test_symlink_outside(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        _w(os.path.join(out, "x.md"), "secret" * 100)
        os.symlink(os.path.join(out, "x.md"), self.p("CLAUDE.md"))
        (i,) = fp.measure(self.root)
        self.assertEqual((i.chars, i.note), (0, "symlink outside project"))

    def test_binary_and_unreadable(self):
        _w(self.p("CLAUDE.md"), b"\xff\xfe\x00\x80", "wb")
        _w(self.p("CLAUDE.local.md"), "secret")
        os.chmod(self.p("CLAUDE.local.md"), 0)
        self.addCleanup(os.chmod, self.p("CLAUDE.local.md"), 0o644)
        items = {i.path: i for i in fp.measure(self.root)}
        self.assertEqual(items["CLAUDE.md"].note, "not valid utf-8")
        if sys.platform != "win32" and os.geteuid() != 0:
            self.assertEqual(items["CLAUDE.local.md"].note, "unreadable")

    def test_over_boundary(self):
        _w(self.p("CLAUDE.md"), "x" * 400)  # 100 tokens
        self.assertFalse(fp.check(self.root, threshold_tokens=100).over)
        self.assertTrue(fp.check(self.root, threshold_tokens=99).over)

    def test_positive_control_default(self):
        _w(self.p("CLAUDE.md"), "x" * 30000)
        r = fp.check(self.root)
        self.assertTrue(r.over)
        self.assertEqual(r.threshold_tokens, 6000)

    def test_big_dirs(self):
        os.makedirs(self.p("node_modules"))
        self.assertEqual(fp.check(self.root).big_dirs, ["node_modules"])
        _w(self.p(".claude", "settings.json"),
           '{"permissions":{"deny":["Read(./node_modules/**)"]}}')
        self.assertEqual(fp.check(self.root).big_dirs, [])
        _w(self.p(".claude", "settings.json"), "{not json")
        r = fp.check(self.root)
        self.assertEqual(r.big_dirs, ["node_modules"])
        self.assertTrue(any("settings.json" in n for n in r.notes))
        self.assertTrue(any("chars/4" in n for n in r.notes))
        self.assertTrue(any(".claudeignore" in n for n in r.notes))

    def test_python3_and_rtk(self):
        self.assertEqual(fp.python3_status("linux", lambda n: "/x"), (True, ""))
        ok, win = fp.python3_status("win32", lambda n: None)
        ok2, posix = fp.python3_status("linux", lambda n: None)
        self.assertFalse(ok or ok2)
        self.assertNotEqual(win, posix)
        self.assertIn("Microsoft Store", win)
        self.assertTrue(fp.rtk_on_path(lambda n: "/rtk"))
        self.assertFalse(fp.rtk_on_path(lambda n: None))

    def test_read_only(self):
        _w(self.p("CLAUDE.md"), "x" * 10)
        _w(self.p(".claude", "rules", "a.md"), "y")
        os.makedirs(self.p("dist"))
        chmod = sys.platform != "win32" and (
            not hasattr(os, "geteuid") or os.geteuid() != 0)
        if chmod:
            def restore():
                for d, _, fs in os.walk(self.root):
                    os.chmod(d, 0o755)
                    for f in fs:
                        os.chmod(os.path.join(d, f), 0o644)
            self.addCleanup(restore)
            for d, _, fs in os.walk(self.root):
                for f in fs:
                    os.chmod(os.path.join(d, f), 0o444)
            for d, _, _ in list(os.walk(self.root)):
                os.chmod(d, 0o555)
        before = _snap(self.root)
        fp.measure(self.root)
        fp.check(self.root)
        self.assertEqual(before, _snap(self.root))

    @unittest.skipIf(sys.platform == "win32", "symlinks")
    def test_symlinked_claude_dir_outside(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        _w(os.path.join(out, "CLAUDE.md"), "secret" * 100)
        _w(os.path.join(out, "rules", "r.md"), "secret" * 100)
        try:
            os.symlink(out, self.p(".claude"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks")
        items = fp.measure(self.root)
        self.assertTrue(items)
        for i in items:
            self.assertEqual((i.chars, i.note), (0, "symlink outside project"))

    def test_deny_matches_directory_name_only(self):
        for d in ("venv", "build", "dist", "node_modules"):
            os.makedirs(self.p(d))
        def big(*rules):
            import json
            _w(self.p(".claude", "settings.json"),
               json.dumps({"permissions": {"deny": list(rules)}}))
            return fp.check(self.root).big_dirs
        self.assertIn("venv", big("Read(./.venv/**)"))
        self.assertIn("build", big("Bash(npm run build)"))
        self.assertIn("dist", big("Read(./distribution/**)"))
        self.assertNotIn("node_modules", big("Read(./node_modules/**)"))
        self.assertNotIn("dist", big("Read(dist/**)"))
        self.assertNotIn("build", big("Read(./build)"))

    def test_wrong_shaped_settings_noted(self):
        for body in ("[]", '{"permissions": []}', '{"permissions": "x"}'):
            _w(self.p(".claude", "settings.json"), body)
            r = fp.check(self.root)
            self.assertTrue(any("settings.json" in n for n in r.notes), body)

    def test_dot_claude_claude_md_is_project(self):
        _w(self.p(".claude", "CLAUDE.md"), "abcd")
        self.assertEqual([(i.path, i.kind) for i in fp.measure(self.root)],
                         [(".claude/CLAUDE.md", "project")])

    def test_tie_break_by_path(self):
        _w(self.p("CLAUDE.md"), "abcd")
        _w(self.p("CLAUDE.local.md"), "abcd")
        _w(self.p(".claude", "rules", "a.md"), "abcd")
        paths = [i.path for i in fp.measure(self.root)]
        self.assertEqual(paths, sorted(paths))


if __name__ == "__main__":
    unittest.main()
