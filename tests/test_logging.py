"""`ctx/log.py`: the diagnostics behind the paths that may not fail.

The audit finding this file answers was not "there is no logging". It was that
`telemetry.record` and `journal.append` swallow every exception by design, so a
read-only mount, a full disk and a permissions error were indistinguishable
from success — and there was no way to raise verbosity for a support case.

Three properties are asserted here, and they pull against each other, which is
why they are all tested together:

  * the never-raise guarantee is **unchanged** — the swallow points still
    return normally when the disk refuses them;
  * the failure is now **visible** when somebody asks for it; and
  * asking is **opt-in** — with no environment variable set, nothing is
    written, nothing is printed and no file appears.
"""

import ast
import io
import os
import pathlib
import stat
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ctx import config as config_mod, hooks, journal, log, telemetry  # noqa: E402
from support import Fixture  # noqa: E402

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "ctx"


def read_only(path):
    """Make a directory reject new files, or say the platform will not.

    Returns True when the mode took. It does not on Windows, and it does not
    for root, who is exempt from the permission bits — in both cases the test
    that needs it has nothing to assert and skips.
    """
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IXUSR)
    except OSError:
        return False
    probe = path / ".probe"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return True
    try:
        probe.unlink()
    except OSError:
        pass
    return False


class LogFixture(Fixture):
    def setUp(self):
        super().setUp()
        log.reset_notices()
        self.addCleanup(log.reset_notices)
        # Restored in tearDown rather than through addCleanup: cleanups run
        # *after* tearDown, and the fixture's tearDown is what deletes the
        # temporary tree — a read-only directory still in place at that point
        # makes the delete fail and turns every such test into an error.
        self._modes = []
        # A destination that exists for every test, so "nothing was written"
        # is a claim about behaviour rather than about a missing variable.
        self.log_file = self.root / "diagnostics.log"

    def on(self, level="error", to_file=True):
        os.environ[log.ENV_LEVEL] = level
        if to_file:
            os.environ[log.ENV_FILE] = str(self.log_file)
        return self.log_file

    def tearDown(self):
        for path, mode in self._modes:
            try:
                os.chmod(path, mode)
            except OSError:
                pass
        super().tearDown()

    def logged(self):
        if not self.log_file.exists():
            return ""
        return self.log_file.read_text(encoding="utf-8")

    def stderr(self, call):
        """Run `call` with stderr captured, and return what reached it."""
        stream = io.StringIO()
        saved, sys.stderr = sys.stderr, stream
        try:
            call()
        finally:
            sys.stderr = saved
        return stream.getvalue()


# --------------------------------------------------------------------------- #
# off by default, and off means nothing happens
# --------------------------------------------------------------------------- #

class TestOffByDefault(LogFixture):
    def test_the_default_level_is_off(self):
        self.assertNotIn(log.ENV_LEVEL, os.environ)
        self.assertEqual(log.level(), log.OFF)
        self.assertFalse(log.enabled(log.ERROR))
        self.assertFalse(log.enabled(log.DEBUG))

    def test_no_file_appears_even_when_a_destination_is_named(self):
        """`CTX_LOG_FILE` alone must not turn logging on.

        The destination is where lines go, not whether there are any. A tool
        that started writing because a path was exported would be the
        always-on footprint this project sells itself on not having.
        """
        os.environ[log.ENV_FILE] = str(self.log_file)
        self.assertFalse(log.error("test.event", "boom"))
        self.assertFalse(self.log_file.exists())

    def test_a_normal_run_prints_nothing_and_writes_no_log(self):
        before = {p for p in self.root.rglob("*") if p.is_file()}
        noise = self.stderr(lambda: (
            journal.append(self.layout, self.config, "edit", "src/a.py", "x"),
            telemetry.record(self.layout, "PostToolUse", 1.5),
            self.run_hook("SessionStart"),
        ))
        self.assertEqual(noise, "", "a default run must be silent on stderr")
        after = {p for p in self.root.rglob("*") if p.is_file()}
        new = sorted(p.name for p in after - before)
        self.assertNotIn("ctx.log", new)
        self.assertNotIn("diagnostics.log", new)

    def test_explicit_off_spellings_are_off(self):
        for value in ("0", "false", "no", "none", "", "  "):
            os.environ[log.ENV_LEVEL] = value
            self.assertEqual(log.level(), log.OFF, value)


# --------------------------------------------------------------------------- #
# raising the verbosity, which is the point
# --------------------------------------------------------------------------- #

class TestLevels(LogFixture):
    def test_error_is_emitted_and_debug_is_not(self):
        self.on("error")
        self.assertTrue(log.error("test.event", "visible"))
        self.assertFalse(log.debug("test.event", "quiet"))
        text = self.logged()
        self.assertIn("visible", text)
        self.assertNotIn("quiet", text)

    def test_debug_lets_everything_through(self):
        self.on("debug")
        for emit in (log.error, log.warn, log.info, log.debug):
            self.assertTrue(emit("test.event", emit.__name__))
        text = self.logged()
        for name in ("error", "warn", "info", "debug"):
            self.assertIn(name, text)

    def test_a_flag_spelling_means_on(self):
        """`CTX_LOG=1` is what a CI script writes. It must not mean nothing."""
        self.on("1")
        self.assertEqual(log.level(), log.ERROR)
        self.assertTrue(log.error("test.event", "visible"))

    def test_an_unrecognised_value_turns_logging_on_and_says_so(self):
        """Silently demoting a typo to `off` is how a support case fails."""
        self.on("verbose")
        self.assertTrue(log.error("test.event", "visible"))
        text = self.logged()
        self.assertIn("visible", text)
        self.assertIn("is not a level", text)

    def test_that_notice_is_printed_once_per_process(self):
        self.on("verbose")
        for index in range(5):
            log.error("test.event", f"line {index}")
        self.assertEqual(self.logged().count("is not a level"), 1)

    def test_stderr_is_the_default_destination(self):
        os.environ[log.ENV_LEVEL] = "error"
        noise = self.stderr(lambda: log.error("test.event", "on the terminal"))
        self.assertIn("on the terminal", noise)

    def test_a_line_is_one_line(self):
        self.on("error")
        log.error("test.event", "first\nsecond\nthird", path="a\nb")
        self.assertEqual(len(self.logged().strip().splitlines()), 1)

    def test_a_line_names_the_level_and_the_site(self):
        self.on("error")
        log.error("journal.append", "boom")
        self.assertIn("ctx.error", self.logged())
        self.assertIn("journal.append", self.logged())

    def test_fields_are_rendered_as_key_values(self):
        self.on("error")
        log.error("test.event", "boom", path="/tmp/x", event="Stop", absent=None)
        text = self.logged()
        self.assertIn("path=/tmp/x", text)
        self.assertIn("event=Stop", text)
        self.assertNotIn("absent", text)

    def test_an_exception_is_named_and_typed(self):
        self.on("error")
        log.failure("test.event", OSError(30, "Read-only file system"))
        self.assertIn("OSError", self.logged())
        self.assertIn("Read-only file system", self.logged())


# --------------------------------------------------------------------------- #
# the logger cannot break the thing it observes
# --------------------------------------------------------------------------- #

class TestTheLoggerCannotBreakASession(LogFixture):
    def test_an_unwritable_destination_loses_the_line_and_raises_nothing(self):
        os.environ[log.ENV_LEVEL] = "error"
        # A directory where a file is expected: every platform refuses the
        # open, and none of them may let that reach the caller.
        os.environ[log.ENV_FILE] = str(self.root / ".ctx")
        self.assertFalse(log.error("test.event", "boom"))

    def test_a_destination_under_a_missing_directory_is_survivable(self):
        os.environ[log.ENV_LEVEL] = "error"
        os.environ[log.ENV_FILE] = str(self.root / "nope" / "deeper" / "x.log")
        self.assertFalse(log.error("test.event", "boom"))

    def test_a_closed_stderr_is_survivable(self):
        os.environ[log.ENV_LEVEL] = "error"
        stream = io.StringIO()
        stream.close()
        saved, sys.stderr = sys.stderr, stream
        try:
            self.assertFalse(log.error("test.event", "boom"))
        finally:
            sys.stderr = saved

    def test_the_destination_is_bounded(self):
        """A level left on for a week must not become the disk-full condition
        it was turned on to diagnose."""
        self.on("error")
        self.log_file.write_text(("x" * 300 + "\n") * 2000, encoding="utf-8")
        self.assertGreater(self.log_file.stat().st_size, log.MAX_BYTES)
        log.error("test.event", "the newest line")
        text = self.logged()
        self.assertLess(len(text), log.MAX_BYTES)
        self.assertLessEqual(len(text.splitlines()), log.KEEP_LINES + 1)
        self.assertIn("the newest line", text)


# --------------------------------------------------------------------------- #
# the swallow points: still silent to the caller, no longer silent full stop
# --------------------------------------------------------------------------- #

class TestTheSwallowPointsReport(LogFixture):
    def readonly(self, directory):
        self._modes.append((str(directory), 0o700))
        if not read_only(directory):
            self.skipTest("cannot make a directory read-only here")

    def readonly_runtime(self):
        self.readonly(self.layout.runtime)

    def readonly_journal(self):
        self.readonly(self.layout.journal)

    def test_telemetry_returns_normally_on_a_read_only_runtime(self):
        self.readonly_runtime()
        self.assertIs(telemetry.record(self.layout, "PostToolUse", 2.0), False)

    def test_and_the_failure_is_now_visible(self):
        self.on("error")
        self.readonly_runtime()
        telemetry.record(self.layout, "PostToolUse", 2.0)
        text = self.logged()
        self.assertIn("telemetry.record", text)
        self.assertIn("Error", text, "the log names what actually went wrong")

    def test_journal_append_returns_normally_on_a_read_only_journal(self):
        self.readonly_journal()
        self.assertIsNone(
            journal.append(self.layout, self.config, "edit", "src/a.py", "x")
        )

    def test_and_that_failure_is_visible_too(self):
        self.on("error")
        self.readonly_journal()
        journal.append(self.layout, self.config, "edit", "src/a.py", "x")
        self.assertIn("journal.append", self.logged())

    def test_a_disabled_journal_is_not_reported_as_a_failure(self):
        """Off and broken are different things, and the log must not say
        `journal.append` failed when journalling was simply switched off."""
        self.on("debug")
        config = dict(self.config, journal=dict(self.config["journal"],
                                                enabled=False))
        self.assertIsNone(journal.append(config=config, layout=self.layout,
                                         kind="edit", target="src/a.py"))
        self.assertNotIn("journal.append", self.logged())

    def test_a_hook_still_exits_zero_and_the_failure_is_logged(self):
        self.on("error")
        def broken(*_args, **_kwargs):
            raise RuntimeError("kaboom")

        saved = hooks.HANDLERS.get("SessionStart")
        hooks.HANDLERS["SessionStart"] = broken
        self.addCleanup(hooks.HANDLERS.__setitem__, "SessionStart", saved)
        code, _out = self.run_hook("SessionStart")
        self.assertEqual(code, 0, "a hook never breaks a session")
        text = self.logged()
        self.assertIn("hooks.SessionStart", text)
        self.assertIn("kaboom", text)

    def test_an_unreadable_policy_file_is_reported(self):
        """A control plane silently not applied was the worst of the swallows
        — it looks exactly like having no policy at all."""
        self.on("error")
        staged = self.untracked / "policy.yaml"
        staged.mkdir()  # a directory where a file is expected: unreadable
        os.environ["CTX_POLICY_SYSTEM"] = str(staged)
        config_mod.resolve_policy(self.layout)
        self.assertIn("config.policy", self.logged())


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #

class TestTheModuleStaysOutOfTheWay(unittest.TestCase):
    def test_the_package_does_not_use_the_standard_library_logger(self):
        """`logging` is process-global state owned by whoever imports it
        first. Hooks run inside a process this package does not own."""
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "logging" for a in node.names):
                        offenders.append(path.name)
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "logging":
                        offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_it_imports_nothing_from_the_package(self):
        """Anything may import the logger, including modules imported by
        everything else. That only holds while it depends on nobody."""
        tree = ast.parse((PACKAGE / "log.py").read_text(encoding="utf-8"))
        siblings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                siblings.extend(a.name for a in node.names)
            if isinstance(node, ast.Import):
                siblings.extend(a.name for a in node.names
                                if a.name.startswith("ctx."))
        self.assertEqual(siblings, [])

    def test_the_swallow_points_named_in_the_contract_all_route_here(self):
        """Behaviour is asserted above; this catches the site that is deleted
        or refactored away without anyone noticing the report went with it."""
        for module, sites in (("telemetry.py", ("telemetry.record",)),
                              ("journal.py", ("journal.append",
                                              "journal.write_digest")),
                              ("hooks.py", ("hooks.log_error",))):
            text = (PACKAGE / module).read_text(encoding="utf-8")
            self.assertIn("from . import", text)
            for site in sites:
                self.assertIn(f'"{site}"', text,
                              f"{module} no longer reports {site}")


if __name__ == "__main__":
    unittest.main()
