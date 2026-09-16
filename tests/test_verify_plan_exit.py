"""`ctx verify --plan` tells "nothing ran" apart from "everything passed".

`cmd_verify`'s own contract for a single item has been explicit for a while:
0 pass, 1 a criterion failed, 2 nothing could be run. `verify_plan` — the
headless path, the one `docs/operations.md` tells people to put in their
pipeline — ended `return 1 if failed else 0`, folding the third case into the
first. A CI job on a runner that has never accepted the project's commands
ERRORs every check of every unit and then exits 0: a green build produced by a
run that verified nothing at all.

The fix is to reuse `cmd_verify`'s three codes rather than invent a second
rule, so the tests below pin both halves: the "nothing ran" case now exits 2,
and every other case exits exactly what it exited before.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, plan as plan_mod, trust as trust_mod, verify,
)
from support import OK, Fixture  # noqa: E402

BODY = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class PlanVerifyFixture(Fixture):
    slug = "ci-plan"

    def unit(self, name, checks, *, accept=True, wave=1):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": "pending", "wave": wave, "verify": list(checks),
            },
            BODY,
        ).write(directory / f"{name}.md")
        if accept:
            self.trust(list(checks))

    def build(self):
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)

    def forget_trust(self):
        """Empty the trust store — a fresh clone, or a CI runner.

        `ctx init` accepts the checks it proposes, so the fixture starts
        trusted. Neither of those two does.
        """
        path = trust_mod.path_for(self.layout)
        if path.is_file():
            path.unlink()

    def run_plan(self):
        return self.cli("verify", "--plan", self.slug)


class TestNothingRanIsNotAPass(PlanVerifyFixture):

    def test_an_all_error_plan_exits_two(self):
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.unit("02-b", [{"kind": "cmd", "run": OK}])
        self.build()
        self.forget_trust()
        self.assertFalse(trust_mod.load(self.layout),
                         "the premise: this machine has accepted nothing")
        code, out = self.run_plan()
        self.assertEqual(code, 2, out)
        self.assertIn("2 could not run", out)
        self.assertIn("0 passed", out)
        self.assertIn("not a green build", out)

    def test_the_module_level_call_returns_two_as_well(self):
        """Not only the CLI: `verify_plan` is imported directly by anything
        that wants a headless run, and the code is its contract."""
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.build()
        self.forget_trust()
        self.assertEqual(verify.verify_plan(self.layout, self.config, self.slug), 2)

    def test_the_same_plan_exits_zero_once_the_commands_are_accepted(self):
        """Positive control: `ctx trust` is the missing step, not the plan."""
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.unit("02-b", [{"kind": "cmd", "run": OK}])
        self.build()
        self.forget_trust()
        self.assertEqual(self.run_plan()[0], 2)
        self.trust([{"kind": "cmd", "run": OK}])
        code, out = self.run_plan()
        self.assertEqual(code, 0, out)
        self.assertIn("2 passed", out)


class TestTheOtherCodesAreUnchanged(PlanVerifyFixture):

    def test_all_passing_still_exits_zero(self):
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.unit("02-b", [{"kind": "cmd", "run": OK}])
        self.build()
        code, out = self.run_plan()
        self.assertEqual(code, 0, out)

    def test_one_failing_unit_still_exits_one(self):
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.unit("02-b", [{"kind": "cmd", "run": self.py("import sys; sys.exit(1)")}])
        self.build()
        code, out = self.run_plan()
        self.assertEqual(code, 1, out)
        self.assertIn("1 failed", out)

    def test_a_failure_beats_an_unrunnable_check(self):
        """Both conditions at once. "The work is wrong" is the more useful
        answer, and it is the one that must survive the new branch."""
        self.unit("01-a", [{"kind": "cmd", "run": OK}], accept=False)
        self.unit("02-b", [{"kind": "cmd", "run": self.py("import sys; sys.exit(1)")}])
        self.build()
        code, out = self.run_plan()
        self.assertEqual(code, 1, out)

    def test_one_error_beside_one_pass_still_exits_zero(self):
        """Only the *nothing ran* case changed. A plan where one unit's check
        could not run and another's passed is not a plan that verified
        nothing."""
        self.unit("01-a", [{"kind": "cmd", "run": OK}])
        self.build()
        self.unit("02-b", [{"kind": "cmd", "run": self.py("pass  # never accepted")}],
                  accept=False)
        self.build()
        code, out = self.run_plan()
        self.assertEqual(code, 0, out)
        self.assertIn("1 passed", out)
        self.assertIn("1 could not run", out)

    def test_pending_judged_checks_still_exit_zero(self):
        """A `rubric` awaiting sign-off is a check that ran and said "a person
        has to look at this" — unlike an ERROR, which could not speak at all."""
        self.unit("01-a", [{"kind": "rubric", "about": "is it good"}])
        self.build()
        code, out = self.run_plan()
        self.assertEqual(code, 0, out)
        self.assertIn("awaiting sign-off", out)

    def test_a_broken_plan_is_still_one_not_two(self):
        """Validation problems are reported before anything runs, and that is
        a plan that is wrong, not a plan that could not be run."""
        for name in ("01-a", "02-b"):
            directory = plan_mod.units_dir(self.layout, self.slug)
            directory.mkdir(parents=True, exist_ok=True)
            frontmatter.Document(
                {
                    "ctx_schema": 1, "unit": name, "plan": self.slug,
                    "tier": "subagent", "depends_on": [], "owns": ["src/same.py"],
                    "reads": [], "forbid": [], "budget_tokens": 1000,
                    "status": "pending", "wave": 1,
                    "verify": [{"kind": "cmd", "run": OK}],
                },
                BODY,
            ).write(directory / f"{name}.md")
        self.trust([{"kind": "cmd", "run": OK}])
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.run_plan()
        self.assertEqual(code, 1, out)
        self.assertIn("not verifying units", out)


if __name__ == "__main__":
    unittest.main()
