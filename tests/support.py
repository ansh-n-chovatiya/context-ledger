"""Shared test fixture: a throwaway project with an initialised ledger."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import config as config_mod, hooks, paths  # noqa: E402
from ctx.cli import main as cli_main  # noqa: E402


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
        (self.root / ".git").exists() and __import__("shutil").rmtree(self.root / ".git")
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "seed")

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
