"""Shared test fixture: a throwaway project with an initialised ledger."""

import builtins
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


# --------------------------------------------------------------------------- #
# The isolation guard
# --------------------------------------------------------------------------- #
#
# A test once escaped its fixture and overwrote this repository's own
# `.ctx/.gitignore` with a single `*` and no trailing newline. Committed, that
# one character would have gitignored the entire `.ctx/` tree — plans, specs,
# journal, decisions — and the ledger would have silently stopped being
# tracked, with nothing failing anywhere. A human noticed the file looked
# wrong; the suite had no way to say so.
#
# Every test is supposed to work inside its own `TemporaryDirectory` (see
# `Fixture.setUp` below). This patches the filesystem calls the ledger's own
# write path actually uses — `atomic.write_text` and `frontmatter.Document`
# both funnel through a temp file next to the destination and finish with
# `os.replace`; direct callers reach for `Path.write_text`/`write_bytes`,
# plain `open`, or `os.mkdir`/`os.makedirs` — and fails loudly, by path, the
# moment one of them targets this checkout's own `.ctx/` rather than a
# fixture's. It is installed at import time, below, so importing this module
# is enough to arm it: `unittest discover` imports every `test_*.py` file
# before running any test, and most of them import this one, so by the time
# the first test runs the guard is already live for the whole process —
# including the handful of files that never import `support` at all.


class RealLedgerWriteError(AssertionError):
    """A test (or the code it drove) wrote inside this checkout's own `.ctx/`
    instead of a fixture's `TemporaryDirectory`."""


# This checkout's own ledger — never a fixture's, which lives under the OS
# temp root and so can never resolve inside this path.
_REAL_CTX = (Path(__file__).resolve().parent.parent / paths.CTX_DIRNAME).resolve()


def _inside_real_ctx(candidate):
    try:
        resolved = Path(os.fspath(candidate)).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return resolved == _REAL_CTX or _REAL_CTX in resolved.parents


def _guard(path, verb):
    if path is None or isinstance(path, int):  # an fd: nothing to resolve
        return
    if _inside_real_ctx(path):
        raise RealLedgerWriteError(
            f"a test tried to {verb} {os.fspath(path)!r}, inside this "
            f"checkout's own ledger at {_REAL_CTX} — every test must write "
            f"through its own fixture (support.Fixture.setUp), never here"
        )


def _install_isolation_guard():
    real_replace = os.replace
    real_rename = os.rename
    real_mkdir = os.mkdir
    real_makedirs = os.makedirs
    real_open = builtins.open
    real_io_open = io.open

    def guarded_replace(src, dst, *a, **kw):
        _guard(dst, "replace")
        return real_replace(src, dst, *a, **kw)

    def guarded_rename(src, dst, *a, **kw):
        _guard(dst, "rename")
        return real_rename(src, dst, *a, **kw)

    def guarded_mkdir(path, *a, **kw):
        _guard(path, "mkdir")
        return real_mkdir(path, *a, **kw)

    def guarded_makedirs(path, *a, **kw):
        _guard(path, "makedirs")
        return real_makedirs(path, *a, **kw)

    write_flags = ("w", "a", "x", "+")

    def guarded_open(file, mode="r", *a, **kw):
        if any(flag in mode for flag in write_flags):
            _guard(file, "open(mode=%r)" % mode)
        return real_open(file, mode, *a, **kw)

    def guarded_io_open(file, mode="r", *a, **kw):
        if any(flag in mode for flag in write_flags):
            _guard(file, "open(mode=%r)" % mode)
        return real_io_open(file, mode, *a, **kw)

    # `pathlib.Path.write_text`/`write_bytes`/`open` call `io.open` directly —
    # not the `open` builtin, which is a separate attribute that merely
    # started out equal to it — so both have to be patched, or every write
    # made through a `Path` would sail past a guard on `builtins.open` alone.
    # `Path.mkdir` has to be wrapped in its own right, not reached through
    # `os.mkdir`. On Python 3.8 and 3.9 pathlib routes it through an accessor
    # object that binds `os.mkdir` at *import* time, so patching the `os`
    # attribute afterwards never reaches it — the guard silently protected
    # nothing on exactly the two interpreters this project supports at the
    # floor. Its own test caught that the first time the suite met the 3.9
    # matrix on CI. Wrapping the method covers every version the same way.
    real_path_mkdir = Path.mkdir

    def guarded_path_mkdir(self, *a, **kw):
        _guard(self, "mkdir")
        return real_path_mkdir(self, *a, **kw)

    os.replace = guarded_replace
    os.rename = guarded_rename
    os.mkdir = guarded_mkdir
    os.makedirs = guarded_makedirs
    builtins.open = guarded_open
    io.open = guarded_io_open
    Path.mkdir = guarded_path_mkdir


_install_isolation_guard()


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
