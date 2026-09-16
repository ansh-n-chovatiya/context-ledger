"""A unit cannot reach `done` having recorded none of the phases it declared.

`phases.can_enter` describes itself as "the only door, and it is closed by
default", and `phases.record` genuinely refuses to write a phase whose
prerequisite is missing. But nothing asked either of them at the end of the
unit's life: `Ledger.add` is public and ungated, and — far simpler — a runner
could just never invoke `ctx phase` at all. A `kind: bug` unit therefore
reached `done` with an empty phase ledger, and the preset's whole claim ("no
fix before a failing reproduction") held only over the runners that chose to
walk through the door.

Never running the door is not the same as the door letting you through. So
`verify.gate_check` now reads the phase ledger for any unit that declares
`phases:` or is `kind: bug`, and refuses by name when a required phase has no
recorded outcome.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, phases as phases_mod, plan as plan_mod,
)
from support import FAILS, OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
BODY = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class PhaseGateFixture(Fixture):
    slug = "phase-gate"

    def unit(self, name, *, kind="", reproduction="", declared=None):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
            "forbid": [], "budget_tokens": 1000, "status": "pending",
            "wave": 1, "verify": list(CHECK),
        }
        if kind:
            meta["kind"] = kind
        if reproduction:
            meta["reproduction"] = reproduction
        if declared is not None:
            meta["phases"] = list(declared)
        frontmatter.Document(meta, BODY).write(directory / f"{name}.md")
        self.trust(list(CHECK))
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)
        return plan_mod.find_unit(self.layout, self.slug, name)

    def phase(self, name, phase, *extra):
        return self.cli("phase", name, phase, "--plan", self.slug, *extra)

    def mark_done(self, name, *extra):
        return self.cli("unit", name, "--status", "done", *extra)

    def status_of(self, name):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        return frontmatter.read(path).meta["status"]


class TestABugUnitMustRecordItsPhases(PhaseGateFixture):

    def test_zero_phases_recorded_refuses_done_and_names_the_missing_one(self):
        unit = self.unit("01-bug", kind="bug", reproduction=OK)
        self.assertEqual(
            phases_mod.load(self.layout, self.slug, unit.name).entries, [],
            "the premise: nothing was ever recorded",
        )
        code, out = self.mark_done("01-bug")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.status_of("01-bug"), "pending", "nothing was written")
        self.assertIn("refusing", out)
        self.assertIn("reproduce", out, "the refusal names the missing phase")
        self.assertIn("bug", out, "and where the requirement comes from")

    def test_stopping_half_way_through_still_refuses_and_names_what_is_left(self):
        self.unit("01-bug", kind="bug", reproduction=OK)
        self.assertEqual(self.phase("01-bug", "reproduce", "--command", FAILS,
                                    "--exit-code", "1", "--evidence", "boom")[0], 0)
        self.assertEqual(self.phase("01-bug", "locate",
                                    "--evidence", "src/x.py:42 stale compare")[0], 0)
        code, out = self.mark_done("01-bug")
        self.assertEqual(code, 1, out)
        self.assertIn("fix", out)
        self.assertIn("guard", out)
        self.assertNotIn("missing: reproduce", out, "what was recorded is not missing")

    def test_the_full_bug_flow_reaches_done(self):
        """Positive control. The refusal is about phases nobody recorded, not
        about `kind: bug` — a preset that could never be completed would just
        teach people to stop setting it."""
        self.unit("01-bug", kind="bug", reproduction=OK)
        self.assertEqual(self.phase("01-bug", "reproduce", "--command", FAILS,
                                    "--exit-code", "1", "--evidence", "boom")[0], 0)
        self.assertEqual(self.phase("01-bug", "locate",
                                    "--evidence", "src/x.py:42 stale compare")[0], 0)
        self.assertEqual(self.phase("01-bug", "fix", "--command", OK,
                                    "--exit-code", "0")[0], 0)
        self.assertEqual(self.phase("01-bug", "guard", "--exit-code", "0",
                                    "--evidence", "verifier: pass")[0], 0)
        code, out = self.mark_done("01-bug")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-bug"), "done")

    def test_force_is_the_way_past_and_the_refusal_says_so(self):
        self.unit("01-bug", kind="bug", reproduction=OK)
        _code, out = self.mark_done("01-bug")
        self.assertIn("--force", out)
        code, out = self.mark_done("01-bug", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-bug"), "done")


class TestADeclaredPhaseListIsEnforcedToo(PhaseGateFixture):

    def test_a_declared_list_with_nothing_recorded_refuses(self):
        self.unit("01-feature", declared=["design", "build"])
        code, out = self.mark_done("01-feature")
        self.assertEqual(code, 1, out)
        self.assertIn("design", out)
        self.assertIn("build", out)
        self.assertIn("phases:", out, "and says the list is the unit's own")

    def test_recording_only_the_first_still_refuses_and_names_the_second(self):
        self.unit("01-feature", declared=["design", "build"])
        self.assertEqual(self.phase("01-feature", "design", "--command", OK,
                                    "--exit-code", "0")[0], 0)
        code, out = self.mark_done("01-feature")
        self.assertEqual(code, 1, out)
        self.assertIn("missing: build", out)
        self.assertNotIn("missing: design", out)

    def test_recording_both_reaches_done(self):
        self.unit("01-feature", declared=["design", "build"])
        for phase in ("design", "build"):
            self.assertEqual(self.phase("01-feature", phase, "--command", OK,
                                        "--exit-code", "0")[0], 0)
        code, out = self.mark_done("01-feature")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-feature"), "done")


class TestAUnitWithNoPhasesIsUntouched(PhaseGateFixture):

    def test_a_plain_unit_still_reaches_done_with_an_empty_phase_ledger(self):
        """The gate may not grow a requirement for units that never opted in.
        `phases.for_unit` returns `[]` for them, and `[]` is not a debt."""
        unit = self.unit("01-plain")
        self.assertEqual(phases_mod.for_unit(unit), [])
        code, out = self.mark_done("01-plain")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-plain"), "done")

    def test_an_empty_declared_list_is_not_a_requirement_either(self):
        self.unit("01-plain", declared=[])
        code, out = self.mark_done("01-plain")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
