"""`plan.apply_waves` must not rewrite a dispatched unit's `wave:`.

`wave` became a sealed contract field so a runner could not relabel a scope
violation as a sibling's declared work (see `test_contract_wave_field.py`).
But `wave` is also the one sealed field `ctx plan-check` recomputes and
writes back on every run (`apply_waves`), because it is derived from the
dependency graph rather than authored by hand. If an *unrelated* unit's
`depends_on` grows deeper upstream of a unit that was already dispatched,
`waves()` recomputes a new, larger depth for every downstream unit — and
`apply_waves` used to write that straight to disk regardless of dispatch
state, manufacturing a "contract changed after dispatch" refusal for a unit
whose own work never changed. `apply_waves(grouped, is_sealed=...)` is the
fix: a unit already sealed is left exactly as it is on disk, even when the
freshly computed level disagrees.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod  # noqa: E402
from support import Fixture  # noqa: E402


class TestApplyWavesRespectsASeal(Fixture):

    def _unit(self, name, wave):
        directory = plan_mod.units_dir(self.layout, "p")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": "p", "tier": "subagent",
             "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
             "forbid": [], "budget_tokens": 1000, "status": "running",
             "wave": wave, "verify": []},
            "## Objective\nDo it.\n",
        ).write(path)
        return plan_mod.find_unit(self.layout, "p", name)

    def test_a_sealed_unit_s_wave_on_disk_is_untouched_even_when_recomputed_differs(self):
        unit = self._unit("01-a", wave=1)
        grouped = {2: [unit]}  # freshly computed depth disagrees with the "1" on disk

        plan_mod.apply_waves(grouped, is_sealed=lambda u: True)

        reread = plan_mod.find_unit(self.layout, "p", "01-a")
        self.assertEqual(reread.wave, 1, "a sealed unit's wave must not move")

    def test_an_unsealed_unit_still_gets_the_recomputed_wave(self):
        """The control: nothing about the fix should stop a plan that has
        never been dispatched from getting a correct, up-to-date graph."""
        unit = self._unit("01-a", wave=1)
        grouped = {2: [unit]}

        plan_mod.apply_waves(grouped, is_sealed=lambda u: False)

        reread = plan_mod.find_unit(self.layout, "p", "01-a")
        self.assertEqual(reread.wave, 2)

    def test_no_is_sealed_argument_keeps_the_old_unconditional_behavior(self):
        """Backward compatible default: a caller that never passes
        `is_sealed` (there is only one today, but the parameter is optional)
        gets exactly the old rewrite-everything behavior."""
        unit = self._unit("01-a", wave=1)
        grouped = {2: [unit]}

        plan_mod.apply_waves(grouped)

        reread = plan_mod.find_unit(self.layout, "p", "01-a")
        self.assertEqual(reread.wave, 2)


if __name__ == "__main__":
    unittest.main()
