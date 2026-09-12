"""`publishes_iface`: a heading is not a consumer.

The term used to fire on the existence of a non-empty `## Interfaces` section.
Sixteen of one session's seventeen units had one — a unit template encourages
it — so sixteen units scored two free points and landed in the `deep` band,
including one whose entire job was moving a file. The band picks the model, so
the bug was not cosmetic: it bought opus for work that was budgeted for haiku.

What the weight is *for* is spelled out in `config.DEFAULTS`: "a published
interface is read by a sibling before this unit is done, so getting it wrong
costs someone else's work too". That sentence names two conditions, and the
old trigger checked neither. The new one checks both — a sibling that declares
`depends_on` this unit **and** reads a path this unit owns.

The weight itself is untouched at 2.0. The tests below therefore fall in two
groups: when the term fires (and, just as load-bearing, when it does not), and
the measured control — the same units, scored under the old rule and the new
one, showing the tier actually moves.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    complexity, config as config_mod, dispatch, frontmatter, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
INTERFACE = "`widget.do(x) -> y` — stable for the wave."


class ScoringFixture(Fixture):
    """A plan whose units are written by hand, then scored as a plan.

    Every unit here lives in a real plan directory, because the question the
    term now asks — "does anything else in this plan consume me?" — is a
    question about the plan, not about the file.
    """

    slug = "widgets"

    def setUp(self):
        super().setUp()
        self.trust(CHECK)
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)

    def unit(self, name, *, owns=(), reads=(), depends_on=(), budget=0,
             interfaces=""):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        body = "## Objective\nDo it.\n\n## Interfaces\n"
        body += interfaces or "<!-- none -->"
        body += "\n\n## Acceptance criteria\n1. it works\n"
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": self.slug,
             "tier": "subagent", "depends_on": list(depends_on),
             "owns": list(owns), "reads": list(reads), "forbid": [],
             "budget_tokens": budget, "status": "pending",
             "verify": list(CHECK)},
            body,
        ).write(directory / f"{name}.md")

    def units(self):
        return plan_mod.load_units(self.layout, self.slug)

    def named(self, name):
        return next(unit for unit in self.units() if unit.name == name)

    def points(self, name, *, siblings=None):
        """The `interface` term's contribution to one unit's score, or 0."""
        unit = self.named(name)
        _value, breakdown = complexity.score(
            self.config, unit,
            self.units() if siblings is None else siblings,
        )
        return sum(value for label, value in breakdown
                   if label.startswith("interface"))


# --------------------------------------------------------------------------- #
# when it fires
# --------------------------------------------------------------------------- #

class TestTheTermNeedsAConsumer(ScoringFixture):

    def test_a_sibling_that_depends_and_reads_is_a_consumer(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["src/api.py"],
                  depends_on=["01-api"])
        self.assertEqual(self.points("01-api"), 2.0)

    def test_an_interfaces_section_with_nobody_downstream_scores_nothing(self):
        """The bug, stated as a test: the heading on its own is not a cost."""
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-other", owns=["src/other.py"])
        self.assertEqual(self.points("01-api"), 0)

    def test_depends_on_without_reading_the_owned_path_is_only_sequencing(self):
        """A unit that waits for a migration to finish reads none of its code."""
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["docs/notes.md"],
                  depends_on=["01-api"])
        self.assertEqual(self.points("01-api"), 0)

    def test_reading_the_path_without_depending_is_the_race_check_not_this(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["src/api.py"])
        self.assertEqual(self.points("01-api"), 0)

    def test_a_consumer_of_an_unwritten_interface_still_scores_nothing(self):
        """Both halves are required in the other direction too: a unit with a
        real consumer but an empty `## Interfaces` published nothing."""
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-user", owns=["src/user.py"], reads=["src/api.py"],
                  depends_on=["01-api"])
        self.assertEqual(self.points("01-api"), 0)

    def test_a_consumer_reading_by_glob_still_counts(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["src/*.py"],
                  depends_on=["01-api"])
        self.assertEqual(self.points("01-api"), 2.0)

    def test_a_unit_alone_in_its_plan_has_no_consumer(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.assertEqual(self.points("01-api"), 0)

    def test_a_unit_does_not_consume_itself(self):
        self.unit("01-api", owns=["src/api.py"], reads=["src/api.py"],
                  interfaces=INTERFACE)
        self.assertEqual(self.points("01-api"), 0)


class TestConsumersOf(ScoringFixture):
    """The predicate on its own, named, so the rule is readable in one place."""

    def test_it_names_every_consumer_in_plan_order(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.unit("02-b", owns=["src/b.py"], reads=["src/api.py"],
                  depends_on=["01-api"])
        self.unit("03-c", owns=["src/c.py"], reads=["src/api.py"],
                  depends_on=["01-api"])
        self.assertEqual(
            plan_mod.consumers_of(self.named("01-api"), self.units()),
            ["02-b", "03-c"],
        )

    def test_no_siblings_at_all_is_an_empty_list_not_an_error(self):
        self.unit("01-api", owns=["src/api.py"], interfaces=INTERFACE)
        self.assertEqual(plan_mod.consumers_of(self.named("01-api"), None), [])


# --------------------------------------------------------------------------- #
# the breakdown, and the weight
# --------------------------------------------------------------------------- #

class TestTheBreakdownStaysAnAuditTrail(ScoringFixture):

    def setUp(self):
        super().setUp()
        self.unit("01-api", owns=["src/api.py"], budget=15000,
                  interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["src/api.py"],
                  depends_on=["01-api"])

    def test_the_label_names_the_consumer_that_made_it_fire(self):
        _value, breakdown = complexity.score(
            self.config, self.named("01-api"), self.units()
        )
        self.assertIn(("interface for 02-user", 2.0), breakdown)

    def test_two_consumers_are_counted_rather_than_listed(self):
        self.unit("03-also", owns=["src/also.py"], reads=["src/api.py"],
                  depends_on=["01-api"])
        _value, breakdown = complexity.score(
            self.config, self.named("01-api"), self.units()
        )
        self.assertIn(("interface for 2 units", 2.0), breakdown)

    def test_the_breakdown_still_sums_to_the_score(self):
        value, breakdown = complexity.score(
            self.config, self.named("01-api"), self.units()
        )
        self.assertEqual(value, sum(points for _, points in breakdown))

    def test_the_weight_is_still_two_and_still_comes_from_config(self):
        """The `ctx-0-8` decision was the *weight*. Only the trigger moved, so
        halving the weight in `ctx.yaml` must still halve this term."""
        self.assertEqual(
            config_mod.DEFAULTS["complexity"]["weights"]["publishes_iface"], 2.0
        )
        halved = config_mod.load(self.layout)
        halved["complexity"]["weights"]["publishes_iface"] = 1.0
        _value, breakdown = complexity.score(
            halved, self.named("01-api"), self.units()
        )
        self.assertIn(("interface for 02-user", 1.0), breakdown)


# --------------------------------------------------------------------------- #
# where the siblings come from
# --------------------------------------------------------------------------- #

class TestTheSiblingsArgument(ScoringFixture):
    """`score` grew an optional parameter. A caller that omits it must get the
    same answer, not the old behaviour — a default that silently restores the
    wide trigger is the bug wearing a parameter."""

    def setUp(self):
        super().setUp()
        self.unit("01-api", owns=["src/api.py"], budget=15000,
                  interfaces=INTERFACE)
        self.unit("02-user", owns=["src/user.py"], reads=["src/api.py"],
                  depends_on=["01-api"])

    def test_omitting_it_reads_the_plan_off_disk_and_agrees(self):
        unit = self.named("01-api")
        with_list, _ = complexity.score(self.config, unit, self.units())
        without, _ = complexity.score(self.config, unit)
        self.assertEqual(without, with_list)

    def test_a_unit_that_is_not_in_a_plan_directory_scores_no_interface(self):
        """`dispatch` tests build units from frontmatter with a bare filename.
        There is no plan to read, so there is no consumer to find — and the
        term must be absent rather than assumed."""
        doc = frontmatter.Document(
            {"unit": "01-loose", "tier": "subagent", "owns": ["a.py"],
             "verify": list(CHECK)},
            "## Objective\nx\n\n## Interfaces\n" + INTERFACE + "\n",
        )
        loose = plan_mod.Unit(Path("01-loose.md"), doc)
        self.assertTrue(loose.publishes_interface)
        value, breakdown = complexity.score(self.config, loose)
        self.assertEqual([label for label, _ in breakdown], ["owns 1 path"])
        self.assertEqual(value, 0.5)

    def test_siblings_on_disk_only_reads_a_units_directory(self):
        self.assertEqual(
            [unit.name for unit in plan_mod.siblings_on_disk(self.named("01-api"))],
            ["01-api", "02-user"],
        )


# --------------------------------------------------------------------------- #
# the measured control
# --------------------------------------------------------------------------- #

def _old_score(config, unit):
    """The scoring rule as it stood before this change, for comparison only.

    Reimplemented here rather than kept behind a flag in `complexity`: a
    second live code path is a second thing to keep correct, and what this
    needs is a yardstick, not an option. The one difference is the trigger —
    the term fired on the section existing — so the old total is the new total
    plus the weight, for any unit that published an interface and now has no
    consumer.
    """
    value, breakdown = complexity.score(config, unit, [])
    if unit.publishes_interface:
        weight = config_mod.DEFAULTS["complexity"]["weights"]["publishes_iface"]
        value += weight
        breakdown = breakdown + [("interface", weight)]
    return value, breakdown


class TestTheTierActuallyMoves(ScoringFixture):
    """Criterion 6, in the small: the same units, before and after.

    The shape is this session's own wave 5 — units with a real budget and a
    handful of owned paths, each carrying an `## Interfaces` section the
    template asked for, none of them actually consumed by a sibling.
    """

    def setUp(self):
        super().setUp()
        for name in ("01-move", "02-rename", "03-split"):
            self.unit(name, owns=[f"src/{name}.py", f"tests/test_{name}.py"],
                      budget=45000, interfaces=INTERFACE)

    def test_before_the_change_every_one_of_them_was_deep(self):
        for unit in self.units():
            value, _ = _old_score(self.config, unit)
            self.assertEqual(complexity.tier_for(self.config, value), "deep",
                             f"{unit.name} scored {value}")

    def test_after_the_change_none_of_them_is(self):
        for unit in self.units():
            value, _ = complexity.score(self.config, unit, self.units())
            self.assertEqual(complexity.tier_for(self.config, value), "standard",
                             f"{unit.name} scored {value}")

    def test_the_unit_with_a_real_consumer_keeps_its_deep_tier(self):
        """The control on the control: the change must not simply switch the
        term off. A unit something genuinely depends on still pays for it."""
        self.unit("04-user", owns=["src/user.py"], reads=["src/01-move.py"],
                  depends_on=["01-move"], budget=15000)
        moved = self.named("01-move")
        value, _ = complexity.score(self.config, moved, self.units())
        self.assertEqual(complexity.tier_for(self.config, value), "deep")

    def test_the_model_picked_for_the_dispatch_moves_with_it(self):
        """The band is only worth anything because it chooses a model."""
        unit = self.named("01-move")
        before, _ = _old_score(config_mod.DEFAULTS, unit)
        tiers = config_mod.DEFAULTS["models"]["tiers"]
        self.assertEqual(
            tiers[{"light": 0, "standard": 1, "deep": 2}[
                complexity.tier_for(config_mod.DEFAULTS, before)]],
            tiers[2],
        )
        self.assertEqual(
            dispatch.model_for(config_mod.DEFAULTS, unit, siblings=self.units()),
            tiers[1],
        )

    def test_the_brief_dispatches_the_wave_on_the_cheaper_seat(self):
        """End to end, through the command that actually spends the money."""
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        line = next(line for line in out.splitlines() if line.startswith("- `01-move`"))
        self.assertIn("sonnet", line)
        self.assertNotIn("opus", line)
        self.assertNotIn("interface", line)


if __name__ == "__main__":
    unittest.main()
