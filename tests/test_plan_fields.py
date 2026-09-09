"""Unit signal fields — `kind`, `reproduction`, `phases`, `publishes_interface`.

These feed the complexity score and the phase gate, so each one has to degrade
to a safe empty value on absent or malformed frontmatter rather than raising —
a hand-edited unit file must never crash the planner. `test_plan.py` covers wave
computation and collisions; this file is scoped to the four new properties and
the one new `validate()` problem they enable.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class FieldsFixture(Fixture):
    """A plan directory with no spec gate needed — these tests read units
    directly, they never go through `ctx start`."""

    slug = "widget-repair"

    def unit(self, name, body, *, extra_meta=None, owns=("src/x.py",)):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
            "budget_tokens": 45000, "status": "pending", "verify": list(CHECK),
        }
        meta.update(extra_meta or {})
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(meta, body).write(path)
        self.trust(CHECK)
        return plan_mod.find_unit(self.layout, self.slug, name)


class TestKindAndReproduction(FieldsFixture):
    def test_kind_defaults_to_empty(self):
        unit = self.unit("01-plain", "## Objective\nDo it.\n")
        self.assertEqual(unit.kind, "")

    def test_kind_reads_frontmatter(self):
        unit = self.unit("01-bug", "## Objective\nFix it.\n", extra_meta={"kind": "bug"})
        self.assertEqual(unit.kind, "bug")

    def test_kind_malformed_degrades_to_empty(self):
        unit = self.unit(
            "01-weird", "## Objective\nFix it.\n",
            extra_meta={"kind": {"not": "a string"}},
        )
        self.assertEqual(unit.kind, "")

    def test_reproduction_defaults_to_empty(self):
        unit = self.unit("01-plain", "## Objective\nDo it.\n")
        self.assertEqual(unit.reproduction, "")

    def test_reproduction_reads_frontmatter(self):
        unit = self.unit(
            "01-bug", "## Objective\nFix it.\n",
            extra_meta={"kind": "bug", "reproduction": "1. run x\n2. see crash"},
        )
        self.assertEqual(unit.reproduction, "1. run x\n2. see crash")

    def test_reproduction_malformed_degrades_to_empty(self):
        unit = self.unit(
            "01-weird", "## Objective\nFix it.\n",
            extra_meta={"reproduction": [1, 2, 3]},
        )
        self.assertEqual(unit.reproduction, "")


class TestPhases(FieldsFixture):
    def test_phases_defaults_to_empty_list(self):
        unit = self.unit("01-plain", "## Objective\nDo it.\n")
        self.assertEqual(unit.phases, [])

    def test_phases_reads_a_list(self):
        unit = self.unit(
            "01-staged", "## Objective\nDo it.\n",
            extra_meta={"phases": ["design", "build"]},
        )
        self.assertEqual(unit.phases, ["design", "build"])

    def test_phases_accepts_a_bare_string(self):
        unit = self.unit(
            "01-staged", "## Objective\nDo it.\n", extra_meta={"phases": "build"}
        )
        self.assertEqual(unit.phases, ["build"])

    def test_phases_malformed_degrades_to_empty_list(self):
        unit = self.unit("01-weird", "## Objective\nDo it.\n", extra_meta={"phases": 4})
        self.assertEqual(unit.phases, [])


class TestPublishesInterface(FieldsFixture):
    def test_scaffolded_template_does_not_publish(self):
        """`scaffold_unit` writes `UNIT_TEMPLATE`, whose `## Interfaces` section
        holds only the authoring-guidance HTML comment. That must read as
        having published nothing."""
        path, created = plan_mod.scaffold_unit(
            self.layout, self.slug, "01-fresh", objective="Do the thing.",
            owns=["src/x.py"],
        )
        self.assertTrue(created)
        unit = plan_mod.find_unit(self.layout, self.slug, "01-fresh")
        self.assertFalse(unit.publishes_interface)

    def test_real_content_publishes(self):
        body = (
            "## Objective\nDo it.\n\n"
            "## Interfaces\n"
            "`widget.repair(id: str) -> bool`, consumed by `dispatch-selection`.\n"
        )
        unit = self.unit("01-real", body)
        self.assertTrue(unit.publishes_interface)

    def test_missing_interfaces_section_does_not_publish(self):
        unit = self.unit("01-none", "## Objective\nDo it.\n")
        self.assertFalse(unit.publishes_interface)

    def test_comment_alongside_real_content_still_publishes(self):
        body = (
            "## Objective\nDo it.\n\n"
            "## Interfaces\n"
            "<!-- guidance -->\n"
            "`widget.repair(id: str) -> bool`\n"
        )
        unit = self.unit("01-mixed", body)
        self.assertTrue(unit.publishes_interface)


class TestBugWithoutReproductionIsAProblem(FieldsFixture):
    def test_validate_names_the_unit(self):
        self.unit(
            "01-crash", "## Objective\nFix the crash.\n",
            extra_meta={"kind": "bug"},
        )
        units = plan_mod.load_units(self.layout, self.slug)
        problems = plan_mod.validate(units)
        matches = [p for p in problems if "01-crash" in p and "reproduction" in p]
        self.assertEqual(len(matches), 1, problems)

    def test_bug_with_reproduction_is_not_a_problem(self):
        self.unit(
            "01-crash", "## Objective\nFix the crash.\n",
            extra_meta={"kind": "bug", "reproduction": "1. run x\n2. see crash"},
        )
        units = plan_mod.load_units(self.layout, self.slug)
        problems = plan_mod.validate(units)
        self.assertFalse(any("reproduction" in p for p in problems), problems)

    def test_non_bug_kind_without_reproduction_is_not_a_problem(self):
        self.unit(
            "01-feature", "## Objective\nAdd the thing.\n",
            extra_meta={"kind": "feature"},
        )
        units = plan_mod.load_units(self.layout, self.slug)
        problems = plan_mod.validate(units)
        self.assertFalse(any("reproduction" in p for p in problems), problems)

    def test_plan_check_reports_it_through_the_cli(self):
        self.cli("plan", self.slug, "--no-spec")
        self.unit(
            "01-crash", "## Objective\nFix the crash.\n",
            extra_meta={"kind": "bug"},
        )
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("01-crash", out)
        self.assertIn("reproduction", out)


if __name__ == "__main__":
    unittest.main()
