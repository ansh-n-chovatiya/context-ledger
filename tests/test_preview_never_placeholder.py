"""The literal claim this whole plan exists to make true."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, preview, preview_page, spec as spec_mod
from support import OK, Fixture

PLACEHOLDER = "Nobody has written this down yet."


class TestPreviewNeverPlaceholder(Fixture):
    SLUG = "fix-the-export-bug"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        spec_mod.create(
            self.layout, self.SLUG,
            intent="Customers hit a 500 exporting more than 10k rows; cap the "
                   "export at 10k with a clear message instead."
        )
        for category in spec_mod.INTAKE_CATEGORIES:
            spec_mod.record_inferred(
                self.layout, self.SLUG, category,
                f"Answer for {category}, drawn from the intent above.",
                "the intent describes this directly",
            )
        self.assertEqual(self.cli("plan", self.SLUG)[0], 0)  # no --no-spec:
                                                              # this must link
                                                              # to the real spec
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-cap-export", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/export.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            # No `## Background`. `plan.UNIT_TEMPLATE` has no such section, so
            # no unit this tool scaffolds has ever carried one — a fixture
            # that wrote one by hand was proving the claim for a plan more
            # complete than anything `ctx plan-unit` produces, which is how
            # "why it matters" reached the placeholder on the default path
            # while every per-task test stayed green.
            "## Objective\nCap CSV export at 10,000 rows and return a clear "
            "error above that instead of a 500.\n",
        ).write(directory / "01-cap-export.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_the_rendered_page_never_shows_the_placeholder(self):
        vm = preview.view_model(self.layout, self.SLUG)
        html = preview_page.render(vm)
        self.assertNotIn(PLACEHOLDER, html)

    def test_no_field_is_left_to_the_last_resort_tier(self):
        """Stronger than the string check, and the reason it holds: every
        plain-language field on every step resolves above tier 5.

        `assertNotIn(PLACEHOLDER, html)` passes if a field is empty as
        happily as if it is filled; this says which tier answered.
        """
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertEqual(step["plain"]["provenance"]["why"], "inferred")
        for key, text in step["plain"].items():
            if key in ("generated", "provenance"):
                continue
            self.assertTrue(str(text).strip(), key)
            self.assertNotIn(PLACEHOLDER, str(text), key)

    def test_why_it_matters_borrows_the_plans_own_reason(self):
        """Tier 3 has no source for a unit's "why it matters" on the ordinary
        path, so it falls across to the plan's recorded `why now`."""
        vm = preview.view_model(self.layout, self.SLUG)
        why = vm["steps"][0]["plain"]["why"]
        self.assertIn("why now", why.lower())
        # Which is the same text the plan-level section shows — one fact, two
        # places, not two answers.
        self.assertIn("why now", vm["plain"]["sections"]["why now"].lower())

    def test_the_unit_is_the_one_plan_unit_would_have_scaffolded(self):
        """Calibration for the two tests above: the fixture's contract is the
        real `UNIT_TEMPLATE` shape, not a richer hand-written one."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        body = (directory / "01-cap-export.md").read_text(encoding="utf-8")
        self.assertNotIn("## Background", body)
        self.assertNotIn("## Background", plan_mod.UNIT_TEMPLATE)

    def test_spec_ready_actually_gated_it_before_planning(self):
        # Proves the gate is real, not just that this fixture happens to
        # satisfy it: a sibling spec that never records the intake must
        # still be refused by `ctx plan`.
        spec_mod.create(self.layout, "no-intake-recorded", intent="Do a thing.")
        code, out = self.cli("plan", "no-intake-recorded")
        self.assertNotEqual(code, 0)
        self.assertIn("intake", out.lower())


if __name__ == "__main__":
    unittest.main()
