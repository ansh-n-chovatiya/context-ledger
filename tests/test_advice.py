"""`ctx/advice.py` — the decision tree behind `ctx next` — and the plan-
resolution preamble the commands that act on its advice all open with.

Two things that were hard to test, for the same reason: both lived inside a
three-thousand-line command layer, so reaching a branch meant standing up an
argparse namespace, a command function and whatever ledger state that command
happened to read on the way past. `next_action` is the product's entire
"what should I do now" answer and had four tests, all of them about one L2
branch, all of them going through a dispatched plan on disk.

It is now a plain function of `(layout, config, state)`, and `state` is an
argument, so a branch is selected by naming the state that selects it instead
of by writing a ledger that implies it. Every branch the tree decides between
is walked below.

`_plan_or_report` is here for the opposite reason: it is *one* function now,
and the six preambles it replaced did not agree on where the plan name came
from. `TestThePreambleReadsTheSameArgument` is the guard against the
deduplication having silently swapped one for another.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    advice, cli, frontmatter, journal, plan as plan_mod, state as state_mod,
)
from support import OK, Fixture  # noqa: E402


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class AdviceFixture(Fixture):
    """A ledger, plus the ability to ask `next_action` about a state that is
    not the one on disk."""

    def advise(self, config=None, **current):
        return advice.next_action(self.layout, config or self.config, dict(current))

    def unit(self, name, *, status="pending", wave=1, owns=None, plan="auth"):
        directory = plan_mod.units_dir(self.layout, plan)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": plan, "tier": "subagent",
                "depends_on": [], "owns": list(owns or [f"src/{name}.py"]),
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": status, "wave": wave,
                "verify": [{"kind": "cmd", "run": OK}],
            },
            CRITERIA,
        ).write(path)
        self.trust([{"kind": "cmd", "run": OK}])
        return path

    def make_plan(self, slug="auth"):
        code, out = self.cli("plan", slug, "--no-spec")
        self.assertEqual(code, 0, out)
        return dict(state_mod.load(self.layout))


# --------------------------------------------------------------------------- #
# it is callable without argparse at all
# --------------------------------------------------------------------------- #

class TestItNeedsNoParser(unittest.TestCase):

    def test_the_module_does_not_know_about_the_command_layer(self):
        source = (Path(__file__).resolve().parent.parent
                  / "ctx" / "advice.py").read_text(encoding="utf-8")
        for forbidden in ("import argparse", "from . import cli", "args."):
            self.assertNotIn(forbidden, source,
                             f"advice.py mentions {forbidden!r}")

    def test_it_prints_nothing_and_exits_nothing(self):
        """A decision function that printed could not be asked a hypothetical."""
        source = (Path(__file__).resolve().parent.parent
                  / "ctx" / "advice.py").read_text(encoding="utf-8")
        for forbidden in ("print(", "SystemExit", "sys.exit"):
            self.assertNotIn(forbidden, source)


class TestTheStateArgumentIsHonoured(AdviceFixture):
    """The argument that makes the rest of this file possible."""

    def test_two_states_against_one_ledger_give_two_answers(self):
        at_l2, _why = self.advise(level="2")
        at_l1, _why = self.advise(level="1")
        self.assertEqual((at_l2, at_l1), ("/ctx:spec", "/ctx:task"))

    def test_omitting_it_reads_the_state_on_disk(self):
        """The default must stay the old behaviour, or every existing caller
        silently changes meaning."""
        on_disk = state_mod.load(self.layout)
        self.assertEqual(
            advice.next_action(self.layout, self.config),
            advice.next_action(self.layout, self.config, dict(on_disk)),
        )

    def test_the_cli_still_reaches_it(self):
        code, out = self.cli("next")
        self.assertEqual(code, 0, out)
        self.assertIn("next:", out)


# --------------------------------------------------------------------------- #
# the branches, in the order the tree decides them
# --------------------------------------------------------------------------- #

class TestSchemaComesFirst(AdviceFixture):
    """Nothing else is trustworthy if the files are the wrong shape."""

    def stamp(self, version):
        path = self.layout.root / "tasks" / "old.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter.Document({"ctx_schema": version, "task": "old"},
                             "## Objective\nx\n").write(path)

    def test_a_file_behind_the_schema_asks_for_a_migration(self):
        self.stamp(0)
        command, why = self.advise(level="2")
        self.assertEqual(command, "ctx migrate")
        self.assertIn("older schema", why)

    def test_a_file_ahead_of_the_schema_asks_for_a_plugin_upgrade(self):
        self.stamp(99)
        command, why = self.advise(level="2")
        self.assertEqual(command, "ctx migrate")
        self.assertIn("upgrade it", why)

    def test_and_outranks_the_level(self):
        """Positive control: the same state without the stray file answers the
        level's question instead."""
        self.assertEqual(self.advise(level="2")[0], "/ctx:spec")


class TestTrustComesSecond(AdviceFixture):

    def test_an_unaccepted_verify_command_outranks_the_level(self):
        config = dict(self.config, verify=[{"kind": "cmd", "run": OK}])
        command, why = self.advise(config=config, level="2")
        self.assertEqual(command, "ctx trust")
        self.assertIn("will not run until", why)

    def test_accepting_it_lets_the_level_answer(self):
        self.trust([{"kind": "cmd", "run": OK}])
        config = dict(self.config, verify=[{"kind": "cmd", "run": OK}])
        self.assertEqual(self.advise(config=config, level="2")[0], "/ctx:spec")


class TestLevel0(AdviceFixture):

    def test_nothing_recorded_proposes_a_task(self):
        command, why = self.advise(level="0")
        self.assertEqual(command, "/ctx:task «goal»")
        self.assertIn("nothing recorded", why)

    def test_recent_work_proposes_resume(self):
        journal.append(self.layout, self.config, "edit", "src/a.py", "a thing")
        command, why = self.advise(level="0")
        self.assertEqual(command, "/ctx:resume")
        self.assertIn("recent work", why)


class TestLevel1(AdviceFixture):

    def task(self, name="fix-thing"):
        code, out = self.cli("task", name, "--objective", "do it")
        self.assertEqual(code, 0, out)
        return name

    def test_no_task_file_proposes_one(self):
        command, why = self.advise(level="1")
        self.assertEqual(command, "/ctx:task")
        self.assertIn("no task file", why)

    def test_a_pointer_with_no_file_behind_it_says_so(self):
        """Distinct from having no pointer at all: the state is inconsistent,
        and saying "no task file" would send the user looking in the wrong
        place."""
        command, why = self.advise(level="1", task="ghost")
        self.assertEqual(command, "/ctx:task")
        self.assertIn("no file on disk", why)

    def test_an_active_task_proposes_its_gate(self):
        name = self.task()
        command, why = self.advise(level="1", task=name)
        self.assertEqual(command, "/ctx:verify")
        self.assertIn("run its gate", why)

    def test_a_blocked_task_says_how_many_times(self):
        name = self.task()
        command, why = self.advise(level="1", task=name, attempts={name: 3})
        self.assertEqual(command, "/ctx:verify")
        self.assertIn("3 time(s)", why)


class TestLevel2(AdviceFixture):

    def test_nothing_active_proposes_a_spec(self):
        command, why = self.advise(level="2")
        self.assertEqual(command, "/ctx:spec")
        self.assertIn("nothing active", why)

    def test_a_spec_with_open_blocking_questions_proposes_ask(self):
        self.assertEqual(self.cli("spec", "auth", "--intent", "log in")[0], 0)
        self.assertEqual(self.cli("question", "auth", "which provider?")[0], 0)
        command, why = self.advise(level="2", spec="auth")
        self.assertEqual(command, "/ctx:ask")
        self.assertIn("blocking question", why)

    def test_a_ready_spec_proposes_planning_it(self):
        self.assertEqual(self.cli("spec", "auth", "--intent", "log in")[0], 0)
        command, why = self.advise(level="2", spec="auth")
        self.assertEqual(command, "/ctx:plan auth")
        self.assertIn("ready to decompose", why)

    def test_a_plan_with_problems_proposes_doctor(self):
        current = self.make_plan()
        self.unit("01-api", owns=["src/x.py"])
        self.unit("02-store", owns=["src/x.py"])
        command, why = self.advise(**current)
        self.assertEqual(command, "/ctx:doctor")
        self.assertIn("nothing dispatches", why)

    def test_a_focused_unit_proposes_its_gate(self):
        current = self.make_plan()
        self.unit("01-api")
        command, why = self.advise(**dict(current, unit="01-api"))
        self.assertEqual(command, "/ctx:verify")
        self.assertIn("01-api", why)

    def test_an_undispatched_wave_proposes_start(self):
        current = self.make_plan()
        self.unit("01-api")
        command, why = self.advise(**current)
        self.assertEqual(command, "/ctx:start")
        self.assertIn("wave 1", why)

    def test_a_wave_already_in_flight_proposes_review_instead(self):
        current = self.make_plan()
        self.unit("01-api", status="running")
        command, why = self.advise(**current)
        self.assertEqual(command, "/ctx:review 01-api")
        self.assertIn("in flight", why)

    def test_a_finished_plan_proposes_the_handoff(self):
        current = self.make_plan()
        self.unit("01-api", status="done")
        command, why = self.advise(**current)
        self.assertEqual(command, "/ctx:handoff")
        self.assertIn("complete", why)


class TestWaveInFlight(AdviceFixture):
    """The half of the tree that is its own function, asked directly."""

    def test_it_separates_dispatched_from_waiting(self):
        self.make_plan()
        self.unit("01-api", status="running")
        self.unit("02-store", status="pending")
        self.assertEqual(advice.wave_in_flight(self.layout, "auth", 1),
                         (["01-api"], ["02-store"]))

    def test_a_wave_that_does_not_exist_is_empty_rather_than_an_error(self):
        self.make_plan()
        self.unit("01-api")
        self.assertEqual(advice.wave_in_flight(self.layout, "auth", 9), ([], []))
        self.assertEqual(advice.wave_in_flight(self.layout, "auth", None), ([], []))


# --------------------------------------------------------------------------- #
# `_plan_or_report` — the trap in deduplicating the six preambles
# --------------------------------------------------------------------------- #

# What each call site read *before* the deduplication, taken from the six
# copies of the preamble. `plan-check` and `start` take the plan as their
# positional `name`; every other command takes a *unit* as `name` and the plan
# as `--plan`, so reading the wrong one would have silently made
# `ctx merge 01-api` look for a plan called `01-api`.
READ_BEFORE = {
    "plan-unit": "plan",    # cmd_plan_unit:    _active_slug(layout, args.plan, ...)
    "plan-check": "name",   # cmd_plan_check:   _active_slug(layout, args.name, ...)
    "start": "name",        # cmd_start:        _active_slug(layout, args.name, ...)
    "merge": "plan",        # cmd_merge:        _active_slug(layout, args.plan, ...)
    "unit": "plan",         # cmd_unit:         _active_slug(layout, args.plan, ...)
    # `_unit_or_report` read `getattr(args, "plan", None)` for all four of the
    # commands routed through it, and passes its own verb straight down.
    "snapshot": "plan",
    "review": "plan",
    "findings": "plan",
    "phase": "plan",
}


class TestThePreambleReadsTheSameArgument(unittest.TestCase):

    def test_every_call_site_still_reads_what_it_read_before(self):
        self.assertEqual(cli._PLAN_ARGUMENT, READ_BEFORE)

    def test_the_table_matches_what_argparse_defines(self):
        """The mapping is not arbitrary: a command that defines `--plan` uses
        its positional `name` for something else. If a command gains or loses
        `--plan`, this fails rather than the table quietly going stale."""
        parser = cli.build_parser()
        commands = parser._subparsers._group_actions[0].choices
        for verb, source in READ_BEFORE.items():
            with self.subTest(command=verb):
                dests = {action.dest for action in commands[verb]._actions}
                self.assertEqual(source, "plan" if "plan" in dests else "name")

    def test_an_unregistered_verb_refuses_rather_than_guessing(self):
        """Silently defaulting is how the original divergence would come back."""
        with self.assertRaises(KeyError) as caught:
            cli._plan_or_report(None, object(), "not-a-command")
        self.assertIn("_PLAN_ARGUMENT", str(caught.exception))


class TestThePreambleBehaves(AdviceFixture):

    class Args:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def resolve(self, verb, **kw):
        """`_plan_or_report`, with its notice captured.

        It prints on the way to reporting nothing, and an uncaptured `_echo`
        lands in the middle of the test runner's own output."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            slug, code = cli._plan_or_report(self.layout, self.Args(**kw), verb)
        return slug, code, out.getvalue()

    def test_it_returns_the_named_plan(self):
        self.make_plan("auth")
        slug, code, out = self.resolve("merge", plan="auth", name="01-api")
        self.assertEqual((slug, code, out), ("auth", None, ""))

    def test_it_falls_back_to_the_active_plan(self):
        self.make_plan("auth")
        slug, code, _out = self.resolve("merge", plan=None, name="01-api")
        self.assertEqual((slug, code), ("auth", None))

    def test_a_unit_name_is_never_mistaken_for_a_plan(self):
        """The trap, directly: `merge` reads `--plan`, so a `name` of
        `01-api` with no plan anywhere must resolve to nothing rather than to
        a plan called `01-api`."""
        slug, code, out = self.resolve("merge", plan=None, name="01-api")
        self.assertIsNone(slug)
        self.assertEqual(code, 0)
        self.assertIn("no active plan", out)

    def test_a_positional_plan_name_is_read_for_the_commands_that_take_one(self):
        slug, _code, _out = self.resolve("plan-check", name="auth")
        self.assertEqual(slug, "auth")

    def test_no_plan_is_advisory_rather_than_a_failure(self):
        for verb in ("plan-unit", "plan-check", "start", "merge", "unit"):
            with self.subTest(command=verb):
                code, out = self.cli(verb, *(["x"] if verb == "plan-unit" else []))
                self.assertEqual(code, 0, out)
                self.assertIn("no active plan", out)


if __name__ == "__main__":
    unittest.main()
