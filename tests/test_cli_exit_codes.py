"""What a script sees when a `ctx` command does not do what was asked.

Every other module here asserts on the text a command printed. A script cannot
read text: it reads an exit code, and it reads stderr when that code is not 0.
Until this landed, 34 of the 41 subcommands answered a refusal — "no .ctx/
found", "ctx.yaml declares a schema this plugin cannot read" — with exit 0 and
the reason on *stdout*, which is indistinguishable from having done the job.
`ctx status > board.txt || echo failed` wrote the refusal into board.txt and
said nothing.

So these tests are about the three guards in `main`, and each is written so
that removing the guard fails it:

  * the catch-all, which turns any unexpected exception into one line;
  * the routing, which puts every refusal on stderr and nothing else there;
  * `--strict`, which escalates the conditions that exit 0 by design.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import cli as cli_mod, frontmatter, plan as plan_mod  # noqa: E402
from support import Fixture  # noqa: E402


class Boom(Exception):
    """An exception `main` has never heard of, which is the point."""


class TestUnexpectedExceptions(Fixture):
    """A traceback is a bug report, not an error message."""

    def raising(self, exception):
        """Make the next `ctx status` raise. `build_parser` binds `cmd_status`
        when `main` calls it, so replacing the module attribute is enough."""
        def explode(_args):
            raise exception
        self.addCleanup(setattr, cli_mod, "cmd_status", cli_mod.cmd_status)
        cli_mod.cmd_status = explode

    def test_an_unexpected_exception_becomes_one_line_on_stderr(self):
        self.raising(Boom("the wheels came off"))
        code, out, err = self.cli_streams("status")
        self.assertEqual(code, 2, f"out={out!r} err={err!r}")
        self.assertIn("ctx status failed:", err)
        self.assertIn("the wheels came off", err)
        self.assertEqual(out, "", "a failure must not be reported on stdout")

    def test_the_exception_type_survives_a_message_that_says_nothing(self):
        """`KeyError: 'wave'` stringifies to `'wave'`, which diagnoses nothing
        on its own."""
        self.raising(KeyError("wave"))
        _code, _out, err = self.cli_streams("status")
        self.assertIn("KeyError", err)

    def test_no_traceback_reaches_the_user_by_default(self):
        self.raising(Boom("the wheels came off"))
        _code, out, err = self.cli_streams("status")
        self.assertNotIn("Traceback", out + err)
        self.assertNotIn("test_cli_exit_codes.py", out + err)

    def test_ctx_debug_prints_the_full_traceback(self):
        os.environ["CTX_DEBUG"] = "1"
        self.raising(Boom("the wheels came off"))
        code, _out, err = self.cli_streams("status")
        self.assertEqual(code, 2, "debugging changes the output, not the code")
        self.assertIn("Traceback", err)
        self.assertIn("Boom", err)
        self.assertIn("ctx status failed:", err, "the one-line summary stays")

    def test_ctx_debug_set_to_zero_is_off(self):
        """A script that exports `CTX_DEBUG=0` meant to turn it off."""
        os.environ["CTX_DEBUG"] = "0"
        self.raising(Boom("the wheels came off"))
        _code, out, err = self.cli_streams("status")
        self.assertNotIn("Traceback", out + err)

    def test_keyboard_interrupt_is_not_swallowed(self):
        """^C is the caller's decision. Reporting it as a ctx failure would
        both lie and hide the interpreter's own exit status."""
        self.raising(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.cli_streams("status")

    def test_an_exit_code_carrying_systemexit_still_propagates(self):
        """`SystemExit(3)` is a command choosing its own exit status, not a
        message. Converting it to 2 would rewrite the answer."""
        self.raising(SystemExit(3))
        with self.assertRaises(SystemExit) as caught:
            self.cli_streams("status")
        self.assertEqual(caught.exception.code, 3)

    def test_a_bare_systemexit_still_propagates(self):
        self.raising(SystemExit())
        with self.assertRaises(SystemExit):
            self.cli_streams("status")


class TestSpecIntentTooLongForTheFilesystem(Fixture):
    """The one unexpected exception this CLI is known to raise in normal use.

    `ctx spec «a very long intent»` slugifies the whole intent into a directory
    name. Past the filesystem's 255-byte limit `mkdir` raises OSError, which
    reached the user as a traceback. Capping the slug is a separate finding;
    what is asserted here is only that the catch-all turns it into an error a
    script can act on.
    """

    def test_an_over_long_intent_is_an_error_not_a_traceback(self):
        code, out, err = self.cli_streams("spec", "x" * 400)
        self.assertEqual(code, 2, f"out={out!r} err={err!r}")
        self.assertIn("ctx spec failed:", err)
        self.assertNotIn("Traceback", out + err)
        self.assertFalse(
            list(self.layout.specs.glob("xxxx*")),
            "a spec that could not be created must not be half-created",
        )


class TestRefusalsAreDetectable(Fixture):
    """A message-carrying `SystemExit` is a refusal, whichever command raised."""

    # Not gates, and not previously in the `HARD_FAIL` allowlist: every one of
    # these exited 0 with the refusal on stdout.
    ORDINARY = ("status", "list", "resume", "briefing", "load", "task", "ask",
                "save", "journal", "level", "start", "unit", "handoff", "next")
    # The seven that already exited 2. Their code must not move.
    GATES = ("verify", "ci", "spec-ready", "plan-check", "doctor", "migrate",
             "trust")

    def refuse(self, *args):
        return self.cli_streams(*args, cwd=self.untracked)

    def test_the_allowlist_is_gone(self):
        """`HARD_FAIL` named the seven commands allowed to fail. Every command
        is allowed to fail now, so the name should not exist to be added to."""
        self.assertFalse(hasattr(cli_mod, "HARD_FAIL"))

    def test_an_ordinary_command_with_no_ledger_exits_two(self):
        for name in self.ORDINARY:
            with self.subTest(command=name):
                args = {"level": ("level", "1"),
                        "journal": ("journal", "note", "thing")}.get(name, (name,))
                code, out, err = self.refuse(*args)
                self.assertEqual(code, 2, f"ctx {name} exited {code}")
                self.assertIn("/ctx:init", err)
                self.assertEqual(out, "", f"ctx {name} refused on stdout")

    def test_the_previously_allowlisted_commands_keep_exit_two(self):
        for name in self.GATES:
            with self.subTest(command=name):
                code, _out, err = self.refuse(name)
                self.assertEqual(code, 2, f"ctx {name} exited {code}")
                self.assertIn("/ctx:init", err)

    def test_a_ledger_from_a_newer_plugin_is_refused_the_same_way(self):
        """The other message-carrying `SystemExit` in the codebase, raised from
        `config.load` rather than from `paths.require_ctx`."""
        self.layout.config.write_text(
            "schema: 99\nlevel: 1\n", encoding="utf-8")
        code, out, err = self.cli_streams("status")
        self.assertEqual(code, 2, f"out={out!r} err={err!r}")
        self.assertIn("upgrade the plugin", err)
        self.assertEqual(out, "", "a refusal must not be reported on stdout")

    def test_the_reason_is_still_the_first_thing_a_person_reads(self):
        """Moving it to stderr must not lose it: an unredirected terminal shows
        stderr, and Claude Code shows it in the slash command's output."""
        _code, _out, err = self.refuse("status")
        self.assertTrue(err.strip(), "the refusal said nothing anywhere")


class TestAdvisoryConditions(Fixture):
    """Exit 0 survives only where it is the designed answer, and `--strict`
    is how a caller says it would rather hear about those too."""

    def test_the_advisory_set_is_short_and_deliberate(self):
        """It is a hand-written list because membership is a judgement about
        design intent. A long one means something was added to silence a
        failure rather than because exiting 0 was intended."""
        self.assertIsInstance(cli_mod.ADVISORY, frozenset)
        self.assertLessEqual(len(cli_mod.ADVISORY), 5, sorted(cli_mod.ADVISORY))

    def test_an_unenumerated_condition_cannot_be_advised(self):
        with self.assertRaises(KeyError):
            cli_mod._advise("something-nobody-listed")

    def test_a_missing_argument_is_advisory(self):
        """`/ctx:task` with nothing after it: the prompt body asks for the name,
        and a non-zero exit would make Claude Code abandon it first."""
        code, out, _err = self.cli_streams("task")
        self.assertEqual(code, 0)
        self.assertIn("no task name given", out)
        self.assertEqual(self.cli_streams("task", "--strict")[0], 1)

    def test_nothing_active_to_act_on_is_advisory(self):
        code, out, _err = self.cli_streams("unit")
        self.assertEqual(code, 0)
        self.assertIn("no active plan", out)
        self.assertEqual(self.cli_streams("unit", "--strict")[0], 1)

    def test_a_truncated_snapshot_is_advisory(self):
        """The snapshot was written and the review can proceed on it — it just
        does not cover every file, because the user set a cap."""
        slug = "billing"
        directory = plan_mod.units_dir(self.layout, slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-api", "plan": slug, "tier": "subagent",
             "depends_on": [], "owns": ["src/a.py"], "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": "pending", "verify": []},
            "## Objective\nRework it.\n\n## Acceptance criteria\n1. a() is 2\n",
        ).write(directory / "01-api.md")
        for name in ("src/a.py", "src/b.py", "src/c.py"):
            self.write(name, "x = 1\n")
        self.layout.config.write_text(
            self.layout.config.read_text(encoding="utf-8")
            + "\nreview:\n  max_files: 1\n",
            encoding="utf-8",
        )

        code, out, _err = self.cli_streams("snapshot", "01-api", "--plan", slug)
        self.assertEqual(code, 0, out)
        self.assertIn("the file cap was reached", out)

        code, _out, err = self.cli_streams(
            "snapshot", "01-api", "--plan", slug, "--strict")
        self.assertEqual(code, 1, "an incomplete snapshot is worth exit 1")
        self.assertIn("snapshot-truncated", err)


class TestStrict(Fixture):
    """`--strict` moves advisory conditions to 1 and touches nothing else."""

    def test_strict_names_the_condition_it_escalated(self):
        _code, _out, err = self.cli_streams("unit", "--strict")
        self.assertIn("no-active-work", err)
        self.assertIn("strict", err)

    def test_strict_leaves_a_success_alone(self):
        self.assertEqual(self.cli_streams("status")[0], 0)
        self.assertEqual(self.cli_streams("status", "--strict")[0], 0)

    def test_strict_leaves_an_error_alone(self):
        """A refusal is 2 with or without it — strict escalates advice, and an
        error was never advice."""
        self.assertEqual(self.cli_streams("status", cwd=self.untracked)[0], 2)
        self.assertEqual(
            self.cli_streams("status", "--strict", cwd=self.untracked)[0], 2)

    def test_strict_leaves_a_gate_failure_at_one(self):
        """`spec-ready` already returns 1 by itself; strict must not restyle a
        real gate verdict as an advisory escalation."""
        code, out, _err = self.cli_streams("spec-ready", "--strict")
        self.assertEqual(code, 1, out)

    def test_the_environment_variable_escalates_too(self):
        os.environ["CTX_STRICT"] = "1"
        self.assertEqual(self.cli_streams("unit")[0], 1)
        self.assertEqual(self.cli_streams("status")[0], 0)

    def test_the_environment_variable_can_be_switched_off(self):
        os.environ["CTX_STRICT"] = "0"
        self.assertEqual(self.cli_streams("unit")[0], 0)

    def test_the_flag_wins_where_the_environment_says_nothing(self):
        """`--strict` can only turn strictness on, so the flag decides when it
        is given and the environment decides otherwise."""
        os.environ.pop("CTX_STRICT", None)
        self.assertEqual(self.cli_streams("unit")[0], 0)
        self.assertEqual(self.cli_streams("unit", "--strict")[0], 1)
        os.environ["CTX_STRICT"] = "0"
        self.assertEqual(
            self.cli_streams("unit", "--strict")[0], 1,
            "an explicit --strict is not overridden by CTX_STRICT=0",
        )

    def test_strict_is_accepted_before_the_subcommand_too(self):
        """`ctx --strict unit` and `ctx unit --strict` are the same request."""
        out, err = self.cli_streams("--strict", "unit")[0], self.cli_streams(
            "unit", "--strict")[0]
        self.assertEqual((out, err), (1, 1))

    def test_every_command_accepts_strict(self):
        """A script must not have to remember which subcommands take it."""
        parser = cli_mod.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertGreater(len(choices), 30)
        for name, subparser in sorted(choices.items()):
            with self.subTest(command=name):
                self.assertIn("--strict", subparser._option_string_actions)

    def test_an_advisory_run_is_still_only_advisory_without_strict(self):
        code, out, err = self.cli_streams("unit")
        self.assertEqual(code, 0)
        self.assertIn("no active plan", out)
        self.assertEqual(err, "", "advice is not an error report")


if __name__ == "__main__":
    unittest.main()
