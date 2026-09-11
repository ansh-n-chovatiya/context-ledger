"""`plan.max_wave_units` — a hard cap on how wide a wave may be.

`plan.wave_budget_tokens` already refuses a wave that costs too much. It does
not refuse a wave that is merely too *wide*: twelve 5k units clear a 250k
budget comfortably and still ask the orchestrating session to hold twelve
concurrent Task calls, twelve reports and twelve review packages in one
context. Width and cost are different failure modes, so they need different
caps, and this one is expressed in the same vocabulary as the budget refusal
so a reader who has seen one recognises the other.

The positive control for these tests is recorded in the unit report: before
`dispatch.prepare` learned the cap, the over-cap wave below dispatched
cleanly at exit 0 with a "Dispatch these" brief.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, dispatch, frontmatter, miniyaml, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]

#: The widest wave this project has itself dispatched. A cap that would have
#: refused the tool's own history is the wrong cap, so the shipped default is
#: held at or above this and the assertion below says so out loud.
WIDEST_WAVE_DISPATCHED = 4


class CapFixture(Fixture):
    slug = "auth-rotation"

    def setUp(self):
        super().setUp()
        self.cli("spec", self.slug, "--intent", "Rotate keys without downtime.")

    def unit(self, name, *, owns=(), depends_on=(), budget=10000, status="pending"):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": list(depends_on), "owns": list(owns or [f"src/{name}.py"]),
            "reads": [], "forbid": [], "budget_tokens": budget,
            "status": status, "verify": list(CHECK),
        }
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        self.trust(meta["verify"])
        return path

    def set_plan_config(self, **keys):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        section = dict(data.get("plan") or {})
        section.update(keys)
        data["plan"] = section
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")

    def wave_of(self, count, **kwargs):
        for index in range(1, count + 1):
            self.unit(f"{index:02d}-unit-{index}", **kwargs)
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)


class TestTheCapRefuses(CapFixture):
    def test_a_wave_one_unit_over_the_cap_is_refused(self):
        self.set_plan_config(max_wave_units=3)
        self.wave_of(4)

        code, out = self.cli("start")
        self.assertEqual(code, 2, "nothing was dispatched, so this is a refusal")
        self.assertIn("4 unit(s) against a cap of 3", out)
        self.assertIn("split the wave or raise plan.max_wave_units in ctx.yaml", out)
        self.assertIn("nothing was started", out)
        self.assertNotIn("Dispatch these", out)

    def test_a_wave_exactly_at_the_cap_dispatches(self):
        """The boundary is the cap itself, not one below it: `max_wave_units:
        3` means three units are allowed, the way `wave_budget_tokens` allows a
        wave that spends exactly its budget."""
        self.set_plan_config(max_wave_units=3)
        self.wave_of(3)

        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIn("Dispatch these", out)
        self.assertNotIn("max_wave_units", out)

    def test_the_refusal_names_the_wave_and_both_numbers(self):
        """Read straight off `dispatch.prepare`, without the CLI in the way:
        the problem is a dispatch-layer decision, not a rendering one."""
        self.set_plan_config(max_wave_units=2)
        self.wave_of(5)
        config = config_mod.load(self.layout)

        level, units, problems, _budget = dispatch.prepare(
            self.layout, config, self.slug)
        self.assertEqual(level, 1)
        self.assertEqual(len(units), 5)
        self.assertTrue(
            any("cap of 2" in problem and "wave 1" in problem
                for problem in problems), problems)

    def test_a_cap_of_zero_is_no_cap(self):
        """Same convention as `wave_budget_tokens`: 0 disables the check
        rather than refusing every wave."""
        self.set_plan_config(max_wave_units=0)
        self.wave_of(6)

        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIn("Dispatch these", out)

    def test_units_already_done_do_not_count_towards_the_cap(self):
        """The cap governs what is about to be dispatched. A wave of four
        where two are finished asks the orchestrator to hold two, and
        refusing it would make the cap impossible to work off incrementally
        — the same list `budget` is summed from."""
        self.set_plan_config(max_wave_units=2)
        self.wave_of(4)
        for name in ("01-unit-1", "02-unit-2"):
            self.cli("unit", name, "--status", "done")

        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIn("Dispatch these", out)


class TestTheShippedDefault(CapFixture):
    def test_the_default_exists_and_is_a_positive_integer(self):
        cap = config_mod.DEFAULTS["plan"]["max_wave_units"]
        self.assertIsInstance(cap, int)
        self.assertGreater(cap, 0)

    def test_the_default_would_not_have_refused_this_project_history(self):
        """The widest wave context-ledger has itself dispatched is four units.
        A shipped cap below that would have refused the tool's own history,
        which is the definition of a cap set too low."""
        self.assertGreaterEqual(
            config_mod.DEFAULTS["plan"]["max_wave_units"], WIDEST_WAVE_DISPATCHED,
            "a default cap under the project's own widest wave is the wrong cap",
        )

    def test_the_default_reaches_a_generated_ctx_yaml(self):
        """A default that only lives in Python is a default nobody can find."""
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        self.assertEqual(
            int((data.get("plan") or {})["max_wave_units"]),
            config_mod.DEFAULTS["plan"]["max_wave_units"],
        )

    def test_the_default_actually_applies_with_nothing_configured(self):
        """Not just present in `DEFAULTS` — enforced. A wave one past the
        shipped cap is refused by a project that never touched `ctx.yaml`."""
        shipped = config_mod.DEFAULTS["plan"]["max_wave_units"]
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        section = dict(data.get("plan") or {})
        section.pop("max_wave_units", None)
        # Budgets stay small so this refusal can only be about width.
        section["wave_budget_tokens"] = 10_000_000
        data["plan"] = section
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")

        self.wave_of(shipped + 1, budget=1000)
        code, out = self.cli("start")
        self.assertEqual(code, 2, out)
        self.assertIn("plan.max_wave_units", out)


class TestTheCapReadsLikeTheBudgetCap(CapFixture):
    def test_both_refusals_share_their_vocabulary(self):
        """Two caps that refuse in two different dialects are two things to
        learn. `split the wave or raise <key> in ctx.yaml` is the one phrasing
        both use."""
        self.set_plan_config(max_wave_units=1, wave_budget_tokens=1000)
        self.wave_of(2, budget=9000)
        config = config_mod.load(self.layout)

        _level, _units, problems, _budget = dispatch.prepare(
            self.layout, config, self.slug)
        joined = "\n".join(problems)
        self.assertIn("split the wave or raise plan.wave_budget_tokens in ctx.yaml",
                      joined)
        self.assertIn("split the wave or raise plan.max_wave_units in ctx.yaml",
                      joined)


if __name__ == "__main__":
    unittest.main()
