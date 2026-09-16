"""`ctx start --wave N` must not dispatch a wave whose prerequisites are open.

Automatic selection (`level is None`) already gets this for free:
`plan.next_wave` only ever returns the *lowest* wave still carrying an
unfinished unit, and `plan.waves` only ever places a `depends_on` edge in a
strictly earlier wave — so by the time wave N is auto-selected, every wave
before it, and therefore every prerequisite a wave-N unit could name, is
already done.

An explicit `--wave N` walks straight past that guard (report.md §3.8):
nothing between the CLI flag and `dispatch.prepare`'s seal/snapshot/dispatch
sequence ever asked whether the *named* wave is actually unblocked. Once
dispatched, the unit and its still-open prerequisite are free to run
genuinely concurrently — the read/write-race check that `plan.collisions`
would have caught never runs, because it only runs within a wave that was
*computed*, and this path sidesteps that computation.

This file pins the fix: `dispatch.prepare` runs the same prerequisite check
on an explicit wave that automatic selection gets from `next_wave` alone, and
leaves automatic selection itself untouched.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, contract as contract_mod, dispatch,
    frontmatter, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class PrereqFixture(Fixture):
    """Two units, wave 1 and wave 2, joined by a single `depends_on` edge."""

    slug = "rollout"

    def unit(self, name, *, depends_on=(), status="pending"):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": list(depends_on), "owns": [f"src/{name}.py"],
            "reads": [], "forbid": [], "budget_tokens": 1000,
            "status": status, "verify": list(CHECK),
        }
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, "## Objective\nDo it.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        self.trust(meta["verify"])
        return path

    def build(self, wave1_status="pending"):
        """`01-a` (wave 1) and `02-b` (wave 2, depends on `01-a`)."""
        self.unit("01-a", status=wave1_status)
        self.unit("02-b", depends_on=["01-a"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

    def finish(self, name):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta["status"] = "done"
        doc.write(path)

    def seal_exists(self, name):
        return contract_mod.seal_path(self.layout, self.slug, name).is_file()

    def status_of(self, name):
        return plan_mod.find_unit(self.layout, self.slug, name).status


# --------------------------------------------------------------------------- #
# criterion 1 — an explicit wave with an unfinished prerequisite is refused
# --------------------------------------------------------------------------- #

class TestExplicitWaveIsBlockedByAnOpenPrerequisite(PrereqFixture):
    def test_start_wave_2_is_refused_while_01_a_is_unfinished(self):
        self.build()
        code, out = self.cli("start", "--no-worktree", "--wave", "2")
        self.assertEqual(code, 2, out)
        self.assertIn("01-a", out)
        self.assertIn("wave 2", out)
        self.assertIn("nothing was started", out)
        self.assertNotIn("Dispatch these", out)

    def test_nothing_is_sealed_snapshotted_or_dispatched_by_the_refusal(self):
        self.build()
        self.cli("start", "--no-worktree", "--wave", "2")
        self.assertFalse(self.seal_exists("02-b"), "02-b must not be sealed")
        self.assertEqual(self.status_of("02-b"), "pending")
        self.assertEqual(self.status_of("01-a"), "pending")

    def test_dispatch_prepare_itself_reports_the_block(self):
        """Read straight off `dispatch.prepare`, without the CLI in the way."""
        self.build()
        config = config_mod.load(self.layout)
        level, units, problems, budget = dispatch.prepare(
            self.layout, config, self.slug, 2
        )
        self.assertEqual(level, 2)
        self.assertEqual(units, [])
        self.assertEqual(budget, 0)
        self.assertTrue(
            any("01-a" in problem and "wave 2" in problem for problem in problems),
            problems,
        )

    def test_a_wave_with_more_than_one_open_prerequisite_names_all_of_them(self):
        self.unit("01-a")
        self.unit("02-b")
        self.unit("03-c", depends_on=["01-a", "02-b"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

        code, out = self.cli("start", "--no-worktree", "--wave", "2")
        self.assertEqual(code, 2, out)
        self.assertIn("01-a", out)
        self.assertIn("02-b", out)


# --------------------------------------------------------------------------- #
# criterion 2 — an explicit wave whose prerequisites are all done dispatches
# --------------------------------------------------------------------------- #

class TestExplicitWaveDispatchesOnceThePrerequisiteIsDone(PrereqFixture):
    def test_start_wave_2_dispatches_once_01_a_is_done(self):
        self.build()
        self.finish("01-a")
        code, out = self.cli("start", "--no-worktree", "--wave", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("Dispatch these", out)
        self.assertIn("02-b", out)

    def test_start_wave_1_with_no_prerequisites_of_its_own_is_unaffected(self):
        self.build()
        code, out = self.cli("start", "--no-worktree", "--wave", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("Dispatch these", out)
        self.assertIn("01-a", out)


# --------------------------------------------------------------------------- #
# criterion 3 — automatic selection (no --wave) is unchanged
# --------------------------------------------------------------------------- #

class TestAutomaticSelectionIsUnchanged(PrereqFixture):
    def test_start_with_no_wave_still_picks_the_lowest_unfinished_wave(self):
        self.build()
        code, out = self.cli("start", "--no-worktree")
        self.assertEqual(code, 0, out)
        self.assertIn("Wave 1", out)
        self.assertIn("01-a", out)
        self.assertNotIn("02-b", out)

    def test_prepare_with_level_none_is_unaffected_by_the_new_check(self):
        self.build()
        config = config_mod.load(self.layout)
        level, units, problems, _budget = dispatch.prepare(
            self.layout, config, self.slug, None
        )
        self.assertEqual(level, 1)
        self.assertEqual(problems, [])
        self.assertEqual({u.name for u in units}, {"01-a"})

    def test_automatic_selection_advances_once_01_a_is_done(self):
        self.build()
        self.finish("01-a")
        code, out = self.cli("start", "--no-worktree")
        self.assertEqual(code, 0, out)
        self.assertIn("Wave 2", out)
        self.assertIn("02-b", out)


class TestTheManualStatusRunningPathIsGuardedToo(PrereqFixture):
    """`ctx unit <name> --status running` never goes through `dispatch.prepare`
    — it writes the status directly (`_set_unit_status`). Guarding only the
    wave-dispatch path left this second door to the exact same state
    transition wide open: a unit could be started by hand with its
    prerequisite still not done, the hazard `--wave N`'s fix claims is closed.
    """

    def test_starting_a_unit_by_hand_before_its_prerequisite_is_done_is_refused(self):
        self.build()
        code, out = self.cli("unit", "02-b", "--plan", self.slug, "--status", "running")
        self.assertEqual(code, 1, out)
        self.assertIn("01-a", out)
        self.assertEqual(self.status_of("02-b"), "pending")

    def test_force_still_allows_starting_it_out_of_order(self):
        self.build()
        code, out = self.cli("unit", "02-b", "--plan", self.slug,
                             "--status", "running", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("02-b"), "running")

    def test_once_the_prerequisite_is_done_the_manual_path_works_unforced(self):
        self.build()
        self.finish("01-a")
        code, out = self.cli("unit", "02-b", "--plan", self.slug, "--status", "running")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("02-b"), "running")

    def test_a_unit_with_no_prerequisites_is_unaffected(self):
        self.build()
        code, out = self.cli("unit", "01-a", "--plan", self.slug, "--status", "running")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-a"), "running")


if __name__ == "__main__":
    unittest.main()
