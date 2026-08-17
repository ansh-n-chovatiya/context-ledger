"""Plan tests — phase 5.

The load-bearing assertions are the two collision checks, because they are what
makes "run these concurrently" a guarantee rather than a hope. Both are scoped to
a wave: the same overlap across different waves is correct and must not be
flagged, or the checker becomes noise people learn to ignore.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    bundle, dispatch, frontmatter, plan as plan_mod, spec as spec_mod, state,
)
from support import Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": "true"}]


class PlanFixture(Fixture):
    """A plan with a ready spec, so Gate 1 is satisfied by default."""

    slug = "auth-rotation"

    def ready_spec(self):
        self.cli("spec", self.slug, "--intent", "Rotate keys without downtime.")
        return self.slug

    def unit(self, name, *, tier="subagent", owns=(), reads=(), depends_on=(),
             forbid=(), checks=CHECK, budget=45000, status="pending"):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
            "depends_on": list(depends_on), "owns": list(owns),
            "reads": list(reads), "forbid": list(forbid),
            "budget_tokens": budget, "status": status, "verify": list(checks),
        }
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        return path


class TestWaveComputation(PlanFixture):
    def test_waves_come_from_depends_on(self):
        self.unit("01-keys", owns=["src/keys.py"])
        self.unit("02-clock", owns=["src/clock.py"])
        self.unit("03-refresh", owns=["src/refresh.py"], depends_on=["01-keys", "02-clock"])
        self.unit("04-wire", owns=["src/wire.py"], depends_on=["03-refresh"])

        grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(problems, [])
        self.assertEqual([u.name for u in grouped[1]], ["01-keys", "02-clock"])
        self.assertEqual([u.name for u in grouped[2]], ["03-refresh"])
        self.assertEqual([u.name for u in grouped[3]], ["04-wire"])

    def test_wave_is_one_past_the_deepest_dependency(self):
        self.unit("01-a", owns=["a"])
        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        # Depends on both a shallow and a deep unit: must land after the deeper one.
        self.unit("03-c", owns=["c"], depends_on=["01-a", "02-b"])
        grouped, _ = plan_mod.check(self.layout, self.slug)
        self.assertEqual([u.name for u in grouped[3]], ["03-c"])

    def test_cycle_is_reported_not_hung_on(self):
        self.unit("01-a", owns=["a"], depends_on=["02-b"])
        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("cycle" in p for p in problems), problems)

    def test_computed_wave_is_written_back_to_disk(self):
        self.unit("01-a", owns=["a"])
        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)
        doc = frontmatter.read(plan_mod.units_dir(self.layout, self.slug) / "02-b.md")
        self.assertEqual(doc.meta["wave"], 2)


class TestValidation(PlanFixture):
    def test_missing_fields_are_named(self):
        self.unit("01-a", owns=[], checks=[])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        joined = " ".join(problems)
        self.assertIn("`owns` is empty", joined)
        self.assertIn("no usable `verify`", joined)

    def test_unknown_and_self_dependencies(self):
        self.unit("01-a", owns=["a"], depends_on=["99-ghost"])
        self.unit("02-b", owns=["b"], depends_on=["02-b"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        joined = " ".join(problems)
        self.assertIn("unknown unit", joined)
        self.assertIn("depends on itself", joined)

    def test_unit_names_must_be_ordered_and_kebab(self):
        self.unit("BadName", owns=["a"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("NN-kebab-case" in p for p in problems), problems)

    def test_bad_tier_is_rejected(self):
        self.unit("01-a", owns=["a"], tier="magic")
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("not one of" in p for p in problems), problems)

    def test_empty_plan_says_so(self):
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("no unit files" in p for p in problems), problems)


class TestCollisions(PlanFixture):
    def test_overlapping_owns_in_one_wave_is_blocked(self):
        self.unit("01-a", owns=["src/auth.py", "src/x.py"])
        self.unit("02-b", owns=["src/auth.py"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("both own src/auth.py", problems[0])
        self.assertIn("depends_on: [01-a]", problems[0], "must name the fix")

    def test_directory_prefix_counts_as_overlap(self):
        self.unit("01-a", owns=["src/auth/"])
        self.unit("02-b", owns=["src/auth/refresh.py"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("both own" in p for p in problems), problems)

    def test_glob_counts_as_overlap(self):
        self.unit("01-a", owns=["src/*.py"])
        self.unit("02-b", owns=["src/auth.py"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("both own" in p for p in problems), problems)

    def test_same_overlap_in_different_waves_is_fine(self):
        """Sequential units may absolutely edit the same file."""
        self.unit("01-a", owns=["src/auth.py"])
        self.unit("02-b", owns=["src/auth.py"], depends_on=["01-a"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(problems, [], "ordering removes the collision")

    def test_reading_what_a_concurrent_unit_rewrites_is_a_race(self):
        """`owns` sets are disjoint here and it is still wrong."""
        self.unit("01-writer", owns=["src/keys.py"])
        self.unit("02-reader", owns=["src/refresh.py"], reads=["src/keys.py"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("reads src/keys.py while 01-writer rewrites it", problems[0])
        self.assertIn("depends_on: [01-writer]", problems[0])

    def test_race_disappears_once_ordered(self):
        self.unit("01-writer", owns=["src/keys.py"])
        self.unit(
            "02-reader", owns=["src/refresh.py"], reads=["src/keys.py"],
            depends_on=["01-writer"],
        )
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(problems, [])

    def test_reads_may_be_mappings_with_symbols(self):
        self.unit("01-writer", owns=["src/keys.py"])
        path = self.unit("02-reader", owns=["src/refresh.py"])
        doc = frontmatter.read(path)
        doc.meta["reads"] = [{"path": "src/keys.py", "symbols": ["KeyStore"]}]
        doc.write(path)
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertTrue(any("rewrites it" in p for p in problems), problems)

    def test_reading_an_unowned_file_is_fine(self):
        self.unit("01-a", owns=["src/a.py"], reads=["src/shared.py"])
        self.unit("02-b", owns=["src/b.py"], reads=["src/shared.py"])
        _grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(problems, [], "concurrent reads of the same file are safe")


class TestGraph(PlanFixture):
    def test_graph_is_derived_and_prior_revisions_archived(self):
        self.unit("01-a", owns=["a"])
        self.cli("plan", self.slug, "--no-spec")
        self.assertEqual(self.cli("plan-check", self.slug)[0], 0)

        graph = json.loads(plan_mod.graph_path(self.layout, self.slug).read_text())
        self.assertEqual(graph["revision"], 1)
        self.assertEqual(graph["waves"], [["01-a"]])
        self.assertEqual(graph["units"]["01-a"]["tier"], "subagent")

        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        self.cli("plan-check", self.slug)
        graph = json.loads(plan_mod.graph_path(self.layout, self.slug).read_text())
        self.assertEqual(graph["revision"], 2)
        archived = plan_mod.plan_dir(self.layout, self.slug) / "revisions" / "plan.r1.json"
        self.assertTrue(archived.is_file(), "an in-flight wave's graph is not overwritten")

    def test_nothing_is_written_when_the_plan_is_broken(self):
        self.unit("01-a", owns=["src/x.py"])
        self.unit("02-b", owns=["src/x.py"])
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("nothing was written", out)
        self.assertFalse(plan_mod.graph_path(self.layout, self.slug).exists())

    def test_readme_units_section_is_regenerated(self):
        self.unit("01-a", owns=["a"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)
        text = plan_mod.readme_path(self.layout, self.slug).read_text(encoding="utf-8")
        self.assertIn("**Wave 1**", text)
        self.assertIn("`01-a`", text)
        self.assertIn("## Out of scope", text, "other sections survive regeneration")


class TestGateOnePlansOnlyReadySpecs(PlanFixture):
    def test_planning_is_refused_while_questions_are_open(self):
        self.ready_spec()
        self.cli("question", self.slug, "Does the legacy consumer still poll?")
        code, out = self.cli("plan", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("refusing to plan", out)
        self.assertIn("legacy consumer", out)
        self.assertFalse(plan_mod.readme_path(self.layout, self.slug).exists())

    def test_planning_proceeds_once_answered(self):
        self.ready_spec()
        self.cli("question", self.slug, "Does the legacy consumer still poll?")
        self.cli("resolve", self.slug, "--question", "legacy", "--answer", "No")
        code, _out = self.cli("plan", self.slug)
        self.assertEqual(code, 0)
        self.assertTrue(plan_mod.readme_path(self.layout, self.slug).exists())
        self.assertEqual(state.load(self.layout)["plan"], self.slug)

    def test_a_missing_spec_is_refused_unless_waived(self):
        code, out = self.cli("plan", "no-such-plan")
        self.assertEqual(code, 1)
        self.assertIn("run /ctx:spec first", out)
        self.assertEqual(self.cli("plan", "no-such-plan", "--no-spec")[0], 0)


class TestDispatch(PlanFixture):
    def setup_plan(self):
        self.unit("01-keys", owns=["src/keys.py"])
        self.unit("02-clock", owns=["src/clock.py"], tier="inline")
        self.unit("03-refresh", owns=["src/refresh.py"], depends_on=["01-keys"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

    def test_brief_groups_by_tier_and_names_the_agent(self):
        self.setup_plan()
        code, out = self.cli("start")
        self.assertEqual(code, 0)
        self.assertIn("Wave 1", out)
        self.assertIn("concurrently", out)
        self.assertIn("unit-runner", out)
        self.assertIn("01-keys", out)
        self.assertIn("in this session", out, "inline units are separated out")
        self.assertIn("do not read source files", out.lower())
        self.assertNotIn("03-refresh", out, "wave 2 is not dispatched yet")

    def test_dispatch_is_refused_while_the_plan_has_problems(self):
        self.unit("01-a", owns=["src/x.py"])
        self.unit("02-b", owns=["src/x.py"])
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("start")
        self.assertEqual(code, 1)
        self.assertIn("both own", out)

    def test_wave_budget_cap_blocks_dispatch(self):
        from ctx import miniyaml

        self.unit("01-a", owns=["a"], budget=200000)
        self.unit("02-b", owns=["b"], budget=200000)
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data["plan"] = {"wave_budget_tokens": 100000}
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")

        code, out = self.cli("start")
        self.assertEqual(code, 1)
        self.assertIn("budget", out)

    def test_session_tier_is_reported_as_not_yet_dispatchable(self):
        self.unit("01-writer", owns=["src/w.py"], tier="session")
        self.cli("plan", self.slug, "--no-spec")
        _code, check_out = self.cli("plan-check", self.slug)
        self.assertIn("phase 6", check_out, "plan-check warns up front")
        _code, out = self.cli("start")
        self.assertIn("git worktree add", out, "and start gives a manual fallback")

    def test_completing_a_wave_advances_to_the_next(self):
        self.setup_plan()
        self.assertEqual(plan_mod.next_wave(self.layout, self.slug), 1)
        for name in ("01-keys", "02-clock"):
            self.cli("unit", name, "--status", "done")
        self.assertEqual(plan_mod.next_wave(self.layout, self.slug), 2)

        _code, out = self.cli("start")
        self.assertIn("Wave 2", out)
        self.assertIn("03-refresh", out)

        self.cli("unit", "03-refresh", "--status", "done")
        self.assertIsNone(plan_mod.next_wave(self.layout, self.slug))
        _code, out = self.cli("start")
        self.assertIn("complete", out)

    def test_focusing_a_unit_arms_the_gate_against_it(self):
        self.setup_plan()
        code, out = self.cli("unit", "01-keys")
        self.assertEqual(code, 0)
        current = state.load(self.layout)
        self.assertEqual((current["level"], current["unit"]), ("2", "01-keys"))
        self.assertIn("owns", out)

        self.cli("unit", "01-keys", "--status", "done")
        self.assertIsNone(state.load(self.layout)["unit"], "done clears the focus")

    def test_prepare_reports_the_wave_budget(self):
        self.setup_plan()
        level, units, problems, budget = dispatch.prepare(
            self.layout, self.config, self.slug, None
        )
        self.assertEqual((level, problems), (1, []))
        self.assertEqual(budget, 90000)
        self.assertEqual({u.name for u in units}, {"01-keys", "02-clock"})


class TestBoardAndHandoff(PlanFixture):
    def test_status_shows_the_wave_board(self):
        self.unit("01-a", owns=["a"])
        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)
        self.cli("unit", "01-a")

        _code, out = self.cli("status")
        self.assertIn("wave board", out)
        self.assertIn("01-a", out)
        self.assertIn("running", out)
        self.assertIn("→", out, "the focused unit is marked")

    def test_handoff_captures_the_board_and_next_step(self):
        self.unit("01-a", owns=["a"])
        self.unit("02-b", owns=["b"], depends_on=["01-a"])
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

        code, out = self.cli("handoff", "mid-plan")
        self.assertEqual(code, 0)
        text = bundle.resolve(self.layout, "mid-plan").read_text(encoding="utf-8")
        for heading in ("## Situation", "## Established facts", "## Resume here"):
            self.assertIn(heading, text)
        self.assertIn("wave 1 `01-a`", text)
        self.assertIn("dispatch wave 1", text)
        self.assertIn("mechanical only", out)

    def test_handoff_lists_unanswered_questions(self):
        self.ready_spec()
        self.cli("question", self.slug, "Is the legacy path retired?")
        self.cli("handoff", "blocked-handoff")
        text = bundle.resolve(self.layout, "blocked-handoff").read_text(encoding="utf-8")
        self.assertIn("- [ ] Is the legacy path retired?", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
