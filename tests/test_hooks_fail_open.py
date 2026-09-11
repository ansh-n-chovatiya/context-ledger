"""The carve-out that made fail-open stop failing open.

`hooks.main` wraps everything in `except BaseException` so that a broken hook
can never brick a session. Directly above it sat `except SystemExit: raise` —
and `config.load` raises `SystemExit` when `.ctx/ctx.yaml` declares a schema
above the one the installed plugin understands. So the single condition excluded
from fail-open was the likeliest one to actually occur: a teammate upgrades the
plugin and commits the ledger it wrote, and every colleague still on the old
version gets a non-zero hook exit and a bare error line on *every tool call*,
for a session that cannot act on it and did not ask.

The fix is narrow on purpose. The carve-out is scoped to the `config.load` call,
where `SystemExit` is a documented "stop the command" signal from a module the
CLI shares — a signal a hook has no command to act on. A `SystemExit` out of a
*handler* means something else entirely (a bug, or a deliberate exit someone
wrote) and still propagates, which `TestAGenuineExitStillPropagates` pins so
nobody widens the catch by accident later.

The events are tested plurally on purpose too: a defect that fires on every tool
call is not characterised by one of them.
"""

import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import briefing, config as config_mod, hooks, miniyaml  # noqa: E402
from support import Fixture  # noqa: E402


# Every event the plugin registers. The bug was in `main`, above the dispatch
# table, so it applied to all of them — including the two that run on literally
# every tool call.
ALL_EVENTS = tuple(hooks.HANDLERS)


class NewerLedgerFixture(Fixture):
    """A ledger whose `schema:` is above the plugin's — the teammate's commit."""

    def setUp(self):
        super().setUp()
        config_mod.reset_policy_warnings()
        self.addCleanup(config_mod.reset_policy_warnings)

    def write_schema(self, schema):
        parsed = miniyaml.loads(self.layout.config.read_text(encoding="utf-8")) or {}
        parsed["schema"] = schema
        self.layout.config.write_text(miniyaml.dumps(parsed) + "\n", encoding="utf-8")
        config_mod.reset_policy_warnings()
        return schema

    def hook(self, event, **extra):
        """Run one hook event, keeping stdout and stderr apart: (code, out, err)."""
        payload = self.payload(**extra)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            code = hooks.main(event, io.StringIO(json.dumps(payload)), out)
        return code, out.getvalue(), err.getvalue()


class TestANewerLedgerDoesNotBreakTheSession(NewerLedgerFixture):
    """Criterion 1 and 2: exit 0, one line of notice, no traceback anywhere."""

    def setUp(self):
        super().setUp()
        self.write_schema(config_mod.SCHEMA + 1)

    def test_the_positive_control_reproduces_without_the_carve_out(self):
        """What the ledger did before this unit, re-created by restoring the old
        control flow: `config.load`'s `SystemExit` reaching `except SystemExit:
        raise` and leaving `main`.

        Stated as a test rather than a paragraph so the fix cannot be quietly
        reverted into a green suite — if `main` ever hands a config refusal back
        to the shim again, the *other* tests in this class fail, and this one
        documents what they would be failing to.
        """
        with self.assertRaises(SystemExit) as caught:
            config_mod.load(self.layout)
        message = str(caught.exception)
        self.assertIn(f"schema {config_mod.SCHEMA + 1}", message)
        self.assertIn(f"understands {config_mod.SCHEMA}", message)

    def test_session_start_exits_zero_and_says_why(self):
        code, out, err = self.hook("SessionStart")
        self.assertEqual(code, 0, "a hook must not fail a session over a config")
        self.assertEqual(out, "", "nothing useful can be injected, so nothing is")
        self.assertIn(f"schema {config_mod.SCHEMA + 1}", err)
        self.assertIn(f"understands {config_mod.SCHEMA}", err)
        self.assertIn("upgrade the plugin", err)

    def test_pre_tool_use_exits_zero_and_says_why(self):
        """The other half of criterion 2. `PreToolUse` is the one that made this
        a defect rather than a nuisance: it fires on every single tool call."""
        code, out, err = self.hook(
            "PreToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.root / "src" / "a.py")},
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn(f"schema {config_mod.SCHEMA + 1}", err)
        self.assertIn("upgrade the plugin", err)

    def test_every_registered_event_exits_zero(self):
        for event in ALL_EVENTS:
            with self.subTest(event=event):
                code, _out, err = self.hook(event)
                self.assertEqual(code, 0, f"{event} must fail open")
                self.assertIn("upgrade the plugin", err)

    def test_no_traceback_reaches_any_stream(self):
        for event in ALL_EVENTS:
            with self.subTest(event=event):
                _code, out, err = self.hook(event)
                for stream, text in (("stdout", out), ("stderr", err)):
                    self.assertNotIn("Traceback", text, stream)
                    self.assertNotIn("File \"", text, stream)
                    self.assertNotIn("hooks.py", text, stream)

    def test_the_notice_is_a_single_line(self):
        """Criterion 4. One process per event means a cross-event cache would be
        empty on arrival, so the bound that can actually be held is per
        invocation: one line, not one per config layer and not a stack."""
        _code, _out, err = self.hook("SessionStart")
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_the_notice_names_the_event_that_stood_down(self):
        for event in ("SessionStart", "PostToolUse"):
            with self.subTest(event=event):
                _code, _out, err = self.hook(event)
                self.assertIn(event, err)

    def test_the_shim_exits_zero_too(self):
        """`main` returning 0 is only half of it — `hooks/*.py` calls
        `sys.exit(main(...))`, and a `SystemExit` raised *out* of `main` never
        reached that line. Asserted against the real script, in a real process,
        because that is the thing the session actually runs."""
        shim = Path(__file__).resolve().parent.parent / "hooks" / "pre_tool_use.py"
        result = subprocess.run(
            [sys.executable, str(shim)],
            input=json.dumps(self.payload(
                tool_name="Edit", tool_input={"file_path": "src/a.py"},
            )),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("upgrade the plugin", result.stderr)

    def test_an_unreadable_policy_file_is_the_same_answer(self):
        """`config.load` raises `SystemExit` for a corrupt *policy* file as well,
        deliberately — an unreadable control plane stops a command rather than
        applying silently. A hook has no command to stop, and a machine-wide
        typo that bricked every session in every project would be the same
        defect wearing a different hat."""
        self.write_schema(config_mod.SCHEMA)
        policy = Path(os.environ["CTX_GLOBAL_ROOT"]) / "policy.yaml"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("- not\n- a mapping\n", encoding="utf-8")
        config_mod.reset_policy_warnings()
        code, _out, err = self.hook("SessionStart")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)


class TestTheRestOfFailOpenIsUnchanged(NewerLedgerFixture):
    """Criterion 5: only the config refusal moved. Every other exception type is
    still logged to `runtime/hook-errors.log` and still exits 0."""

    def test_a_raising_handler_still_lands_in_the_error_log(self):
        with mock.patch.object(
            briefing, "build", side_effect=RuntimeError("boom")
        ):
            code, out, _err = self.hook("SessionStart")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("boom", self.layout.errors.read_text(encoding="utf-8"))

    def test_a_config_refusal_is_not_logged_as_a_hook_error(self):
        """It is a state of the repository, not a fault in this run. Logging it
        per tool call would fill the log `ctx doctor` reads with one permanent
        condition and drown the intermittent faults the log exists for."""
        self.write_schema(config_mod.SCHEMA + 1)
        for _ in range(3):
            self.hook("PostToolUse")
        self.assertFalse(
            self.layout.errors.is_file(),
            "a newer ledger is reported, not recorded as a hook fault",
        )

    def test_a_garbage_payload_is_still_survivable(self):
        out = io.StringIO()
        self.assertEqual(hooks.main("SessionStart", io.StringIO("not json"), out), 0)
        self.assertEqual(hooks.main("SessionStart", io.StringIO(""), out), 0)

    def test_an_untracked_project_is_still_silent(self):
        out = io.StringIO()
        err = io.StringIO()
        payload = json.dumps({"cwd": str(self.untracked), "session_id": "s"})
        with mock.patch.object(sys, "stderr", err):
            code = hooks.main("SessionStart", io.StringIO(payload), out)
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))

    def test_a_readable_ledger_is_untouched(self):
        code, out, err = self.hook("SessionStart")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("[ctx]"), out)
        self.assertEqual(err, "")


class TestAGenuineExitStillPropagates(NewerLedgerFixture):
    """Criterion 3, and the decision behind it.

    The carve-out is scoped to `config.load`, not to the module. A `SystemExit`
    from a handler is not a config refusal — it is a bug or a deliberate exit
    someone wrote — and `SystemExit` subclasses `BaseException` precisely so
    that blanket handlers do not eat it. Swallowing every exit in `hooks` would
    also swallow the next one somebody introduces, in a module whose whole
    contract is that it reports what went wrong instead of hiding it.

    So: it propagates, and here is the test that says so on purpose rather than
    by omission.
    """

    def test_an_exit_from_a_handler_is_not_swallowed(self):
        with mock.patch.object(
            briefing, "build", side_effect=SystemExit("a handler said stop")
        ):
            with self.assertRaises(SystemExit) as caught:
                self.hook("SessionStart")
        self.assertEqual(str(caught.exception), "a handler said stop")

    def test_a_handler_exit_is_not_reported_as_a_config_refusal(self):
        """The two must stay distinguishable: if a handler's exit ever started
        printing the ledger notice, the carve-out would have been widened."""
        err = io.StringIO()
        with mock.patch.object(
            briefing, "build", side_effect=SystemExit("a handler said stop")
        ):
            with mock.patch.object(sys, "stderr", err):
                with self.assertRaises(SystemExit):
                    hooks.main(
                        "SessionStart",
                        io.StringIO(json.dumps(self.payload())),
                        io.StringIO(),
                    )
        self.assertNotIn("did nothing rather than", err.getvalue())

    def test_keyboard_interrupt_is_still_caught_as_it_always_was(self):
        """Not part of the carve-out, and not changed by it: the existing
        `except BaseException` covers it, and a hook that let Ctrl-C out would
        be the session-breaking behaviour this module forbids."""
        with mock.patch.object(
            briefing, "build", side_effect=KeyboardInterrupt()
        ):
            code, out, _err = self.hook("SessionStart")
        self.assertEqual((code, out), (0, ""))


class TestTheNoticeItself(NewerLedgerFixture):
    """`_report_unusable_config` is the whole of the new surface. It is called
    from inside a fail-open handler, so it is not allowed to raise."""

    def test_a_bare_numeric_exit_gets_a_sentence_not_a_status_code(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            hooks._report_unusable_config("Stop", SystemExit(2))
        text = err.getvalue()
        self.assertNotIn("ctx: 2 ", text)
        self.assertIn("could not be read", text)

    def test_a_multi_line_refusal_is_flattened_to_one_line(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            hooks._report_unusable_config("Stop", SystemExit("first\nsecond\n"))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("first second", err.getvalue())

    def test_a_console_that_cannot_take_the_line_is_not_a_failure(self):
        broken = mock.Mock()
        broken.write.side_effect = OSError("stderr is closed")
        with mock.patch.object(sys, "stderr", broken):
            hooks._report_unusable_config("Stop", SystemExit("boom"))


if __name__ == "__main__":
    unittest.main()
