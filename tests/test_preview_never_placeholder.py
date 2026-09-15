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
            "## Objective\nCap CSV export at 10,000 rows and return a clear "
            "error above that instead of a 500.\n\n## Background\nThe "
            "unbounded query times out past 10k rows and the client sees a "
            "bare 500 with no explanation.\n",
        ).write(directory / "01-cap-export.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_the_rendered_page_never_shows_the_placeholder(self):
        vm = preview.view_model(self.layout, self.SLUG)
        html = preview_page.render(vm)
        self.assertNotIn(PLACEHOLDER, html)

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
