"""Shared test fixture: a throwaway project with an initialised ledger."""

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import config as config_mod, hooks, paths, trust  # noqa: E402
from ctx.cli import main as cli_main  # noqa: E402


# A verify command that exits 0 anywhere. `true` is a POSIX builtin: on Windows
# it resolves only if Git's usr/bin happens to be on PATH, so a test using it
# asserts nothing on a plain Windows box — it just happened to work on CI.
OK = '"%s" -c pass' % sys.executable
FAILS = '"%s" -c "import sys; sys.exit(1)"' % sys.executable


class Fixture(unittest.TestCase):
    """A throwaway project with an initialised ledger."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # A directory with no `.ctx/` above it anywhere — a genuinely untracked
        # project. Nesting it under self.root would not be untracked, because
        # discovery walks up and would find the fixture's own ledger.
        self.untracked = Path(self._outside.name)
        (self.root / ".git").mkdir()
        self._env = dict(os.environ)
        os.environ["CTX_GLOBAL_ROOT"] = str(self.root / "global")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.assertEqual(self.cli("init")[0], 0)
        self.layout = paths.Layout(self.root / ".ctx")
        self.config = config_mod.load(self.layout)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()
        self._outside.cleanup()

    def cli(self, *args):
        """Run a subcommand against the fixture, capturing its output."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_main(["--cwd", str(self.root), *args])
        return code, buffer.getvalue()

    def payload(self, **extra):
        base = {"session_id": "sess1234", "cwd": str(self.root)}
        base.update(extra)
        return base

    def run_hook(self, event, **extra):
        out = io.StringIO()
        code = hooks.main(event, io.StringIO(json.dumps(self.payload(**extra))), out)
        return code, out.getvalue()

    def git_init(self):
        """Turn the fixture into a real git repo, for `diff`-kind checks."""
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        run = lambda *a: subprocess.run(
            a, cwd=str(self.root), capture_output=True, text=True, env=env, check=True
        )
        (self.root / ".git").exists() and self.rmtree(self.root / ".git")
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "seed")

    def rmtree(self, path):
        """Delete a tree that may contain read-only files.

        Git marks objects in `.git/objects` read-only, and Windows refuses to
        unlink a read-only file — so a plain `shutil.rmtree` on a repository
        raises PermissionError there and nowhere else.
        """
        def clear_readonly(func, target, _exc):
            os.chmod(target, stat.S_IWRITE)
            func(target)

        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=clear_readonly)
        else:
            shutil.rmtree(path, onerror=lambda f, t, e: clear_readonly(f, t, e))

    def py(self, code):
        """A verify command that runs `code` in this interpreter.

        Portable in a way `true`, `sleep`, `test -f` and `[ ... ]` are not: the
        gate runs commands through the platform shell, which is cmd.exe on
        Windows. Keep `code` free of double quotes — cmd groups on them.
        """
        return f'"{sys.executable}" -c "{code}"'

    def trust(self, checks):
        """Accept hand-written verify commands, as `ctx init` does for the ones
        it proposes. Tests that write a `verify` block directly are standing in
        for a developer authoring it locally, not for a cloned ledger."""
        trust.accept(self.layout, checks or [])
        return checks

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
