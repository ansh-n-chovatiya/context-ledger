"""Complexity scoring — `complexity.score` and `complexity.tier_for`.

The score exists to be printed on a dispatch line as its own justification,
so the load-bearing assertion in this file is the reconciliation check: the
breakdown `score` returns must sum to the number it returns, exactly, or the
line lies about what it charged for. Everything else here is one test per
signal — budget, owns, reads, depends_on, a judged verify check, a published
interface, a bug fix — plus the config round-trip that keeps the weights out
of this module entirely.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import complexity, config as config_mod, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CMD_CHECK = [{"kind": "cmd", "run": OK}]


class ComplexityFixture(Fixture):
    """A plan directory whose units are built directly from frontmatter — these
    tests score units, they never dispatch or run a check."""

    slug = "widget-repair"
    _counter = 0

    def unit(self, *, budget=0, owns=(), reads=(), depends_on=(), checks=CMD_CHECK,
              kind="", interfaces=""):
        ComplexityFixture._counter += 1
        name = f"{ComplexityFixture._counter:02d}-unit"
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": list(depends_on), "owns": list(owns), "reads": list(reads),
            "forbid": [], "budget_tokens": budget, "status": "pending",
            "verify": list(checks), "kind": kind,
        }
        body = "## Objective\nDo it.\n\n## Interfaces\n"
        body += interfaces if interfaces else "<!-- none -->"
        body += "\n"
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(meta, body).write(path)
        return plan_mod.find_unit(self.layout, self.slug, name)


class TestFloor(ComplexityFixture):
    def test_every_signal_at_its_floor_scores_zero_and_is_the_cheapest_tier(self):
        unit = self.unit()
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(value, 0.0)
        self.assertEqual(breakdown, [])
        self.assertEqual(complexity.tier_for(self.config, value), "light")

    def test_score_is_never_negative(self):
        value, _ = complexity.score(self.config, self.unit())
        self.assertGreaterEqual(value, 0.0)


class TestOneSignalAtATime(ComplexityFixture):
    """Two units differing in exactly one field must score differently."""

    def test_budget_alone_changes_the_score(self):
        light, _ = complexity.score(self.config, self.unit(budget=0))
        heavy, _ = complexity.score(self.config, self.unit(budget=45000))
        self.assertNotEqual(light, heavy)
        self.assertGreater(heavy, light)

    def test_owns_breadth_alone_changes_the_score(self):
        one, _ = complexity.score(self.config, self.unit(owns=["a.py"]))
        four, _ = complexity.score(
            self.config, self.unit(owns=["a.py", "b.py", "c.py", "d.py"])
        )
        self.assertNotEqual(one, four)
        self.assertGreater(four, one)

    def test_judged_verify_alone_changes_the_score(self):
        mechanical, _ = complexity.score(self.config, self.unit(checks=CMD_CHECK))
        judged, _ = complexity.score(
            self.config,
            self.unit(checks=CMD_CHECK + [{"kind": "rubric", "prompt": "check it"}]),
        )
        self.assertNotEqual(mechanical, judged)
        self.assertGreater(judged, mechanical)

    def test_reads_breadth_alone_changes_the_score(self):
        none, _ = complexity.score(self.config, self.unit(reads=[]))
        many, _ = complexity.score(
            self.config, self.unit(reads=["a.py", "b.py", "c.py", "d.py"])
        )
        self.assertGreater(many, none)

    def test_depends_on_alone_changes_the_score(self):
        none, _ = complexity.score(self.config, self.unit(depends_on=[]))
        some, _ = complexity.score(self.config, self.unit(depends_on=["00-a", "00-b"]))
        self.assertGreater(some, none)

    def test_published_interface_alone_changes_the_score(self):
        """The term needs a consumer, not just a section.

        This used to pass with an `## Interfaces` heading and nothing reading
        it, which put 41 of 49 units across this repo's plans into the term
        and most of them into the `deep` tier. The weight is unchanged at 2.0;
        what changed is that a sibling must actually `depends_on` this unit
        and read a path it owns.
        """
        empty, _ = complexity.score(
            self.config, self.unit(owns=["api.py"], interfaces="")
        )
        publisher = self.unit(owns=["api.py"], interfaces="`thing.do(x) -> y`")
        self.unit(depends_on=[publisher.name], reads=["api.py"])
        published, _ = complexity.score(self.config, publisher)
        self.assertGreater(published, empty)

    def test_an_interfaces_section_nobody_consumes_does_not_fire(self):
        """The other half, and the reason the narrowing is worth having."""
        empty, _ = complexity.score(
            self.config, self.unit(owns=["api.py"], interfaces="")
        )
        unread, _ = complexity.score(
            self.config, self.unit(owns=["api.py"], interfaces="`thing.do(x)`")
        )
        self.assertEqual(unread, empty)

    def test_bug_kind_alone_changes_the_score(self):
        feature, _ = complexity.score(self.config, self.unit(kind="feature"))
        bug, _ = complexity.score(self.config, self.unit(kind="bug"))
        self.assertGreater(bug, feature)


class TestBreakdownLabels(ComplexityFixture):
    """Labels are what a dispatch line prints; they must name the input."""

    def test_budget_label_matches_the_documented_example(self):
        _, breakdown = complexity.score(self.config, self.unit(budget=45000))
        self.assertIn(("budget 45k", 3.0), breakdown)

    def test_owns_label_matches_the_documented_example(self):
        _, breakdown = complexity.score(
            self.config, self.unit(owns=["a.py", "b.py", "c.py", "d.py"])
        )
        self.assertIn(("owns 4 paths", 2.0), breakdown)

    def test_rubric_label_matches_the_documented_example(self):
        _, breakdown = complexity.score(
            self.config,
            self.unit(checks=CMD_CHECK + [{"kind": "rubric", "prompt": "check it"}]),
        )
        self.assertIn(("rubric", 2.0), breakdown)

    def test_both_judged_kinds_are_named_together(self):
        _, breakdown = complexity.score(
            self.config,
            self.unit(checks=CMD_CHECK + [
                {"kind": "rubric", "prompt": "p"}, {"kind": "human", "prompt": "h"},
            ]),
        )
        self.assertIn(("human+rubric", 2.0), breakdown)


class TestReconciliation(ComplexityFixture):
    """The breakdown must sum to the score, exactly — not approximately."""

    def test_a_single_signal_reconciles(self):
        value, breakdown = complexity.score(self.config, self.unit(budget=45000))
        self.assertEqual(value, sum(points for _, points in breakdown))

    def test_every_signal_at_once_reconciles(self):
        unit = self.unit(
            budget=45000,
            owns=["a.py", "b.py", "c.py", "d.py"],
            reads=["e.py", "f.py", "g.py"],
            depends_on=["00-a", "00-b"],
            checks=CMD_CHECK + [{"kind": "human", "prompt": "h"}],
            kind="bug",
            interfaces="`thing.do(x) -> y`",
        )
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(value, sum(points for _, points in breakdown))
        self.assertTrue(breakdown)

    def test_a_budget_that_does_not_divide_evenly_still_reconciles(self):
        """40,000 / 15,000 is not an exact binary fraction — the one signal
        this module rounds deliberately, because this is exactly the input
        that would otherwise leave float noise the breakdown could not
        account for."""
        unit = self.unit(budget=40000, owns=["a.py"], reads=["b.py", "c.py"])
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(value, sum(points for _, points in breakdown))

    def test_many_units_reconcile_regardless_of_which_signals_fire(self):
        cases = [
            self.unit(budget=b, owns=o, reads=r, depends_on=d, kind=k)
            for b, o, r, d, k in [
                (0, [], [], [], ""),
                (15000, ["a.py"], [], [], ""),
                (7500, [], ["a.py"], [], "feature"),
                (100000, ["a.py", "b.py"], ["c.py"], ["00-a"], "bug"),
                (1, ["a.py"], [], [], ""),
            ]
        ]
        for unit in cases:
            value, breakdown = complexity.score(self.config, unit)
            self.assertEqual(
                value, sum(points for _, points in breakdown),
                f"breakdown does not reconcile for {unit.name}",
            )


class TestConfigDrivesTheScore(ComplexityFixture):
    """A weight or threshold change in `ctx.yaml` must change the outcome with
    no edit to this module — that is the whole point of keeping the numbers in
    config.DEFAULTS rather than hard-coded here."""

    def test_halving_a_weight_halves_that_terms_contribution(self):
        unit = self.unit(owns=["a.py", "b.py", "c.py", "d.py"])
        before, _ = complexity.score(self.config, unit)

        halved = config_mod.load(self.layout)
        halved["complexity"]["weights"]["owns_per_path"] = 0.25
        after, breakdown = complexity.score(halved, unit)

        self.assertEqual(after, before / 2)
        self.assertIn(("owns 4 paths", 1.0), breakdown)

    def test_a_partial_weight_override_leaves_siblings_alone(self):
        unit = self.unit(budget=45000, owns=["a.py"])
        patched = config_mod.load(self.layout)
        patched["complexity"]["weights"] = {"budget_per_15k": 2.0}
        value, breakdown = complexity.score(patched, unit)
        self.assertIn(("budget 45k", 6.0), breakdown)
        # `owns_per_path` fell back to the built-in default rather than
        # vanishing when the override only named one key.
        self.assertIn(("owns 1 path", 0.5), breakdown)

    def test_lowering_the_threshold_promotes_the_same_score_to_a_costlier_tier(self):
        self.assertEqual(complexity.tier_for(self.config, 2.5), "light")
        lowered = config_mod.load(self.layout)
        lowered["complexity"]["thresholds"]["standard"] = 2.0
        self.assertEqual(complexity.tier_for(lowered, 2.5), "standard")


class TestTierBoundaries(ComplexityFixture):
    def test_boundaries_are_inclusive_on_the_low_side(self):
        self.assertEqual(complexity.tier_for(self.config, 2.99), "light")
        self.assertEqual(complexity.tier_for(self.config, 3.0), "standard")
        self.assertEqual(complexity.tier_for(self.config, 5.99), "standard")
        self.assertEqual(complexity.tier_for(self.config, 6.0), "deep")


if __name__ == "__main__":
    unittest.main()
