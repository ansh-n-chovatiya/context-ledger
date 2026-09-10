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
import time
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


def _cleanup(tmp, attempts=5):
    """Remove a temp directory, tolerating a racing writer inside `.git`.

    The worktree tests run real `git`, and git leaves work running after the
    command returns — an auto-gc, an fsmonitor, an index rewrite. On macOS that
    raced `rmtree`: the walk listed `.git`, git wrote into it, and `os.rmdir`
    raised `ENOTEMPTY`. It surfaced as an ERROR in whichever test happened to
    finish at the wrong moment, which made a green suite look broken and, worse,
    made a real failure in that test indistinguishable from noise.

    Retry, then give up quietly. The directory is a `TemporaryDirectory` under
    the OS temp root either way, so the cost of losing the race for good is a
    few kilobytes the OS reclaims — not a failed test run.

    `TemporaryDirectory(ignore_cleanup_errors=True)` would say this in one
    argument, but it arrived in 3.10 and this project supports 3.8.
    """
    for remaining in range(attempts - 1, -1, -1):
        try:
            tmp.cleanup()
            return
        except OSError:
            if not remaining:
                return
            time.sleep(0.05)


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
        # Outside the project, as `~/.claude/ctx` is in real use. It used to
        # sit inside `self.root`, which put machine-local state — the trust
        # store among it — inside the repository under test, where it showed
        # up in `git status` and could not be told apart from the user's work.
        os.environ["CTX_GLOBAL_ROOT"] = str(self.untracked / "global")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.assertEqual(self.cli("init")[0], 0)
        self.layout = paths.Layout(self.root / ".ctx")
        self.config = config_mod.load(self.layout)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        _cleanup(self._tmp)
        _cleanup(self._outside)

    def cli(self, *args):
        """Run a subcommand against the fixture, capturing its output.

        The second value is stdout and stderr together, in that order. Refusals
        moved to stderr when the CLI learned to exit non-zero on them, and this
        helper is called from 40-odd test files that assert on the text of a
        message without caring which stream carried it. Use `cli_streams` where
        the split is the thing under test.
        """
        code, out, err = self.cli_streams(*args)
        return code, out + err

    def cli_streams(self, *args, cwd=None):
        """Run a subcommand, keeping stdout and stderr apart: (code, out, err).

        `cwd` runs it somewhere other than the fixture — `self.untracked`, for
        a project with no ledger at all.
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["--cwd", str(cwd or self.root), *args])
        return code, out.getvalue(), err.getvalue()

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
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
            except FileNotFoundError:
                # Git runs background maintenance on a repository it has just
                # been used in, and its lock file can appear and vanish inside
                # `.git/objects` while this walk is in progress. A file that is
                # already gone is the outcome this function wanted.
                pass

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
