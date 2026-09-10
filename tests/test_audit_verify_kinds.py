"""Two audit findings against the done-gate's kinds, both about reach.

The first is the one that matters most, because it points the wrong way: the
gate classified a failure as *infrastructure* by reading the failing command's
own output, so a unit that deleted a module — `No module named 'ctx.foo'`,
printed by pytest, in the middle of the report — was filed as "your toolchain
is missing" and waved through. The precise regression the gate exists to catch
was the one it laundered.

The second is that `exists` and `symbol` would read any file on the machine. A
committed `ctx.yaml` asking `{kind: exists, path: /home/you/.ssh/id_rsa,
matches: "BEGIN OPENSSH"}` got a truthful PASS or FAIL back, one bit per gate
run, and `symbol` echoed the probe string into the message on the way out.

Every refusal below is paired with a positive control: the same mechanism, shown
producing a real verdict when it should. A test that only asserts "this does not
happen" cannot tell a fix from a mechanism that never fires at all, which is the
shape this audit exists to remove.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import verify  # noqa: E402
from support import Fixture  # noqa: E402


def stderr_then_exit(text, code):
    """A command that prints `text` on stderr and exits `code`.

    Single quotes only: the gate runs commands through the platform shell, and
    cmd.exe groups on double quotes.
    """
    return '"%s" -c "import sys; sys.stderr.write(%s); sys.exit(%d)"' % (
        sys.executable, "'" + text + "'", code
    )


class GateFixture(Fixture):
    def run_checks(self, checks, **kwargs):
        self.trust(checks)
        return verify.run(
            self.layout, self.config, checks, cwd=self.root, key="k", **kwargs
        )

    def one(self, check, **kwargs):
        results, verdict = self.run_checks([check], **kwargs)
        return results[0], verdict


# --------------------------------------------------------------------------- #
# (a) infrastructure is decided by the launcher, not by the child's output
# --------------------------------------------------------------------------- #

class TestARegressionIsNotLaunderedAsInfrastructure(GateFixture):
    """Criteria 1-4."""

    def test_a_deleted_module_is_a_work_failure_not_a_missing_toolchain(self):
        """The P0, stated as the audit found it.

        A unit deletes `ctx/foo.py`; the suite it was gated on reports
        `ModuleNotFoundError: No module named 'ctx.foo'` and exits 1. That is
        the regression, not a broken machine.
        """
        text = "ModuleNotFoundError: No module named ' + chr(39) + 'ctx.foo"
        command = stderr_then_exit(text, 1)
        result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.FAIL, result.message)
        self.assertEqual(verdict, verify.FAIL)

    def test_the_laundering_pattern_still_matches_that_output(self):
        """The control for the test above: it is not passing by accident.

        `_missing_tool` is still there and still recognises this text — what
        changed is that nothing consults it for an ordinary check. Without this
        assertion, the test above would keep passing if the whole heuristic were
        deleted, and would say nothing about output no longer being consulted.
        """
        self.assertEqual(
            verify._missing_tool("ModuleNotFoundError: No module named 'ctx.foo'"),
            "'ctx.foo'",
        )

    def test_a_log_quoting_a_shell_error_is_a_work_failure(self):
        """The second pattern's false positive: a test asserting on the string
        `bash: frobnicate: command not found` anywhere in a long log."""
        command = stderr_then_exit(
            "1 failed: expected bash: frobnicate: command not found", 1
        )
        result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.FAIL, result.message)
        self.assertEqual(verdict, verify.FAIL)
        self.assertTrue(
            verify._missing_tool("bash: frobnicate: command not found"),
            "control: this is exactly the text the old heuristic laundered",
        )

    def test_a_launcher_that_never_started_is_still_an_error(self):
        """Criterion 2, first half: the shell's own 127, naming the tool."""
        result, verdict = self.one(
            {"kind": "cmd", "run": "ctx-definitely-absent-tool-xyz"}
        )
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertIn("ctx-definitely-absent-tool-xyz", result.message)

    def test_a_launcher_that_raises_oserror_is_still_an_error(self):
        """Criterion 2, second half: no shell, no fork, no such directory."""
        command = '"%s" -c "pass"' % sys.executable
        with mock.patch.object(verify.subprocess, "run",
                               side_effect=OSError("cannot fork")):
            result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertIn("cannot fork", result.message)
        self.assertIn(sys.executable, result.message)

    def test_an_absent_python_module_is_an_error_before_it_runs(self):
        """Criterion 3. `python -m pytest` with pytest absent exits 1 from an
        interpreter that is very much present, so the exit code cannot answer
        it — but "is the module here" is a question about the machine, and the
        machine can be asked directly before anything runs."""
        command = '"%s" -m ctx_absent_module_xyz' % sys.executable
        result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertIn("ctx_absent_module_xyz", result.message)

    def test_a_module_that_is_present_still_gets_a_real_verdict(self):
        """The control for the pre-flight: it refuses one command, not all of
        them, and a module the project actually ships is found where the check
        runs rather than where the gate happens to live."""
        self.write("ctx_present_module_xyz.py", "import sys\nsys.exit(1)\n")
        command = '"%s" -m ctx_present_module_xyz' % sys.executable
        result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.FAIL, result.message)
        self.assertEqual(verdict, verify.FAIL)

        passing = '"%s" -m ctx_present_module_ok_xyz' % sys.executable
        self.write("ctx_present_module_ok_xyz.py", "pass\n")
        result, verdict = self.one({"kind": "cmd", "run": passing})
        self.assertEqual(result.status, verify.PASS, result.message)

    def test_optional_buys_the_old_leniency_and_only_for_that_check(self):
        """Criterion 4. A project that genuinely wants a check skipped when its
        tool is absent asks for it by name."""
        command = stderr_then_exit("sh: mycli: command not found", 2)
        result, verdict = self.one(
            {"kind": "cmd", "run": command, "optional": True}
        )
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertEqual(verdict, verify.ERROR, "an optional check is not work")
        self.assertIn("mycli", result.message)

    def test_without_optional_the_same_command_fails(self):
        """The control: the leniency is the key, not the output."""
        command = stderr_then_exit("sh: mycli: command not found", 2)
        result, verdict = self.one({"kind": "cmd", "run": command})
        self.assertEqual(result.status, verify.FAIL, result.message)
        self.assertEqual(verdict, verify.FAIL)

    def test_optional_is_not_a_blanket_exemption(self):
        """And the other control: `optional` excuses an absent tool, not a
        failing test. Otherwise it would be `CTX_GATE=off` spelled differently."""
        command = stderr_then_exit("AssertionError: expected token, got None", 1)
        result, verdict = self.one(
            {"kind": "cmd", "run": command, "optional": True}
        )
        self.assertEqual(result.status, verify.FAIL, result.message)
        self.assertEqual(verdict, verify.FAIL)

    def test_optional_must_be_asked_for_exactly(self):
        """`optional: "no"` is not a request for leniency; nothing but `true` is."""
        command = stderr_then_exit("sh: mycli: command not found", 2)
        for value in ("no", "", 0, None, "true"):
            result, _ = self.one({"kind": "cmd", "run": command, "optional": value})
            self.assertEqual(result.status, verify.FAIL, repr(value))


# --------------------------------------------------------------------------- #
# (b) `exists` and `symbol` cannot read outside the project
# --------------------------------------------------------------------------- #

class TestTheContentOracleIsClosed(GateFixture):
    """Criteria 5-7."""

    SECRET = "-----BEGIN OPENSSH PRIVATE KEY-----\nabcdef\n"

    def setUp(self):
        super().setUp()
        self.secret = Path(self.untracked) / "id_rsa"
        self.secret.write_text(self.SECRET, encoding="utf-8")

    def assertRefused(self, result):
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertNotIn(result.status, (verify.PASS, verify.FAIL))
        self.assertIn("outside it", result.message)
        self.assertNotIn("BEGIN OPENSSH", result.message)

    # --- exists ---------------------------------------------------------- #

    def test_an_absolute_path_is_refused_rather_than_answered(self):
        result, verdict = self.one({
            "kind": "exists", "path": str(self.secret), "matches": "BEGIN OPENSSH",
        })
        self.assertRefused(result)
        self.assertEqual(verdict, verify.ERROR)

    def test_the_refusal_does_not_depend_on_the_answer(self):
        """The oracle was the *difference* between the two verdicts. Both a
        probe that would have matched and one that would not must come back
        identical, or the refusal still leaks the bit."""
        matching, _ = self.one({
            "kind": "exists", "path": str(self.secret), "matches": "BEGIN OPENSSH",
        })
        missing, _ = self.one({
            "kind": "exists", "path": str(self.secret), "matches": "BEGIN RSA",
        })
        self.assertEqual(matching.status, missing.status)
        self.assertEqual(matching.message, missing.message)

    def test_a_path_inside_the_project_still_gets_a_real_verdict(self):
        """The control for every refusal above: `exists` still works."""
        self.write("docs/guide.md", self.SECRET)
        good, _ = self.one({
            "kind": "exists", "path": "docs/guide.md", "matches": "BEGIN OPENSSH",
        })
        self.assertEqual(good.status, verify.PASS, good.message)
        bad, _ = self.one({
            "kind": "exists", "path": "docs/guide.md", "matches": "BEGIN RSA",
        })
        self.assertEqual(bad.status, verify.FAIL, bad.message)

    def test_a_dotdot_escape_is_refused(self):
        relative = os.path.relpath(str(self.secret), str(self.root))
        self.assertIn("..", relative)
        result, _ = self.one({"kind": "exists", "path": relative,
                              "matches": "BEGIN OPENSSH"})
        self.assertRefused(result)

    def test_a_dotdot_escape_is_refused_after_cwd_is_applied(self):
        """`cwd` moves the base the path resolves against, so confinement has to
        be judged on the resolved pair, not on the string in `path`."""
        (self.root / "apps" / "web").mkdir(parents=True)
        relative = os.path.relpath(str(self.secret), str(self.root / "apps" / "web"))
        result, _ = self.one({"kind": "exists", "path": relative,
                              "cwd": "apps/web", "matches": "BEGIN OPENSSH"})
        self.assertRefused(result)

    def test_a_cwd_that_leaves_the_project_is_refused_too(self):
        result, _ = self.one({"kind": "exists", "path": "id_rsa",
                              "cwd": str(self.untracked),
                              "matches": "BEGIN OPENSSH"})
        self.assertRefused(result)

    def test_a_symlink_out_of_the_project_is_refused(self):
        link = self.root / "innocent.txt"
        try:
            os.symlink(str(self.secret), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not create a symlink here")
        result, _ = self.one({"kind": "exists", "path": "innocent.txt",
                              "matches": "BEGIN OPENSSH"})
        self.assertRefused(result)

    def test_a_symlink_inside_the_project_still_resolves(self):
        """The control for the symlink refusal: it is the target's location that
        is refused, not the fact of a symlink."""
        self.write("docs/guide.md", self.SECRET)
        link = self.root / "guide-link.md"
        try:
            os.symlink(str(self.root / "docs" / "guide.md"), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not create a symlink here")
        result, _ = self.one({"kind": "exists", "path": "guide-link.md",
                              "matches": "BEGIN OPENSSH"})
        self.assertEqual(result.status, verify.PASS, result.message)

    # --- symbol ---------------------------------------------------------- #

    def test_symbol_refuses_an_absolute_path_without_echoing_the_probe(self):
        result, verdict = self.one({
            "kind": "symbol", "path": str(self.secret),
            "contains": ["BEGIN OPENSSH PRIVATE KEY"],
        })
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertNotIn("BEGIN OPENSSH", result.message)

    def test_symbol_refuses_a_dotdot_escape(self):
        relative = os.path.relpath(str(self.secret), str(self.root))
        result, _ = self.one({"kind": "symbol", "path": relative,
                              "contains": ["BEGIN OPENSSH PRIVATE KEY"]})
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertIn("outside it", result.message)

    def test_symbol_inside_the_project_still_freezes_an_interface(self):
        """The control: the interface freeze is untouched by the confinement."""
        self.write("src/api.py", "def rotate(key):\n    return key\n")
        intact, _ = self.one({"kind": "symbol", "path": "src/api.py",
                              "contains": ["def rotate(key)"]})
        self.assertEqual(intact.status, verify.PASS, intact.message)
        self.write("src/api.py", "def spin(key):\n    return key\n")
        renamed, _ = self.one({"kind": "symbol", "path": "src/api.py",
                               "contains": ["def rotate(key)"]})
        self.assertEqual(renamed.status, verify.FAIL, renamed.message)

    # --- the `matches` regex is on the gate's clock ----------------------- #

    def budgeted(self, seconds):
        return dict(self.config, gate=dict(self.config["gate"],
                                           timeout_seconds=seconds))

    def large_file(self, tail):
        self.write("build.log", ("a" * 40000) + tail)

    def test_a_backtracking_pattern_stops_at_the_gate_deadline(self):
        """Criterion 7. `(a+)+$` against a file of `a`s does not terminate in
        any time a human will wait for, and it used to run inside the Stop hook
        with nothing to stop it — the one place where hanging is worse than
        failing, because a killed hook returns no decision at all."""
        self.large_file("!")
        checks = [{"kind": "exists", "path": "build.log", "matches": r"(a+)+$"}]
        self.trust(checks)
        started = time.monotonic()
        results, verdict = verify.run(
            self.layout, self.budgeted(1), checks, cwd=self.root, key="k",
        )
        elapsed = time.monotonic() - started
        self.assertEqual(results[0].status, verify.ERROR, results[0].message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertLess(elapsed, 20, "the gate's budget did not bound the regex")

    def test_the_same_budget_still_answers_an_ordinary_pattern(self):
        """The control: the bound is a deadline, not a refusal to evaluate. The
        same one-second budget over the same large file returns both verdicts."""
        self.large_file("needle\n")
        found, _ = verify.run(
            self.layout, self.budgeted(1),
            self.trust([{"kind": "exists", "path": "build.log",
                         "matches": "needle"}]),
            cwd=self.root, key="k",
        )
        self.assertEqual(found[0].status, verify.PASS, found[0].message)
        absent, _ = verify.run(
            self.layout, self.budgeted(1),
            self.trust([{"kind": "exists", "path": "build.log",
                         "matches": "haystack"}]),
            cwd=self.root, key="k",
        )
        self.assertEqual(absent[0].status, verify.FAIL, absent[0].message)

    def test_an_unparseable_pattern_is_a_configuration_error(self):
        self.write("docs/guide.md", "text")
        result, _ = self.one({"kind": "exists", "path": "docs/guide.md",
                              "matches": "("})
        self.assertEqual(result.status, verify.ERROR, result.message)
        self.assertIn("bad pattern", result.message)


class TestTheDeadlineIsRealNotMocked(unittest.TestCase):
    """`_match_within` in isolation, so the bound is asserted on the mechanism
    and not only on the kind that happens to call it."""

    def test_it_returns_at_the_deadline_rather_than_running_on(self):
        started = time.monotonic()
        found, problem = verify._match_within(r"(a+)+$", "a" * 40000 + "!", 1)
        elapsed = time.monotonic() - started
        self.assertFalse(found)
        self.assertIn("did not finish", problem)
        self.assertLess(elapsed, 20)

    def test_a_spent_budget_does_not_start_a_search_at_all(self):
        found, problem = verify._match_within("needle", "needle", 0)
        self.assertFalse(found)
        self.assertIn("budget was spent", problem)

    def test_it_still_reports_a_match(self):
        self.assertEqual(verify._match_within("needle", "a needle here", 30),
                         (True, ""))
        self.assertEqual(verify._match_within("needle", "no such thing", 30),
                         (False, ""))

    def test_the_pattern_is_data_not_code(self):
        """The search runs in a child process; the pattern must reach it as a
        value. If it were interpolated into the probe's source, a committed
        `ctx.yaml` would be running code on this machine."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "pwned"
        pattern = "'); import pathlib; pathlib.Path(%r).write_text('x'); ('" % str(target)
        found, problem = verify._match_within(pattern, "body", 30)
        self.assertFalse(target.exists(), "the pattern executed")
        self.assertFalse(found)


if __name__ == "__main__":
    unittest.main()
