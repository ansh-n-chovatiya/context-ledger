"""What `plan-check` now says about a plan it has just accepted.

The graph already knew all of this. A wave of eight units took ninety-five
minutes of wall clock because five of them owned `cli.py`, and `plan-check`
printed the wave numbers without ever mentioning that fact — so the cost of
the plan's shape was discovered by paying it.

Four things are asserted here, in the order they matter:

1. **The numbers are right.** Parallelism, the contended file, the critical
   path and the ownership gaps are computed from the plan, so each is checked
   against a plan whose answer is known by construction rather than by reading
   the implementation back.
2. **The estimate says it is an estimate.** A critical path derived from
   stated `budget_tokens` is a guess about a guess. Printed as a measurement
   it would be worse than silence, so the label is part of the contract.
3. **It reports, it does not refuse.** A serial plan is sometimes the correct
   plan. `plan-check` still exits 0 and writes the graph; `--strict` is how a
   script asks for the escalation.
4. **The detection's blind spots are named.** The ownership-gap scan finds
   imports and path literals. A test that reaches a module through the CLI is
   invisible to it, and there is a test below that says so out loud rather
   than leaving a future reader to assume the scan is exhaustive.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
BODY = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class PlanFixture(Fixture):
    """A ledger with one plan, whose units this fixture writes by hand."""

    slug = "widgets"

    def setUp(self):
        super().setUp()
        self.trust(CHECK)
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)

    def unit(self, name, *, owns=(), reads=(), depends_on=(), budget=10000,
             tier="subagent"):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
             "depends_on": list(depends_on), "owns": list(owns),
             "reads": list(reads), "forbid": [], "budget_tokens": budget,
             "status": "pending", "verify": list(CHECK)},
            BODY,
        ).write(directory / f"{name}.md")
        return directory / f"{name}.md"

    def check(self, *extra):
        code, out, err = self.cli_streams("plan-check", self.slug, *extra)
        return code, out, err

    def document(self, *extra):
        code, out, _err = self.cli_streams("plan-check", self.slug, "--json",
                                           *extra)
        return code, json.loads(out)

    def grouped(self):
        grouped, problems = plan_mod.check(self.layout, self.slug)
        self.assertEqual(problems, [], problems)
        return grouped

    def units(self):
        return plan_mod.load_units(self.layout, self.slug)


# --------------------------------------------------------------------------- #
# 1. parallelism
# --------------------------------------------------------------------------- #

class TestParallelism(PlanFixture):
    """Units, waves and the ratio between them — the width of the plan."""

    def test_three_independent_units_are_one_wave_three_wide(self):
        for name in ("01-a", "02-b", "03-c"):
            self.unit(name, owns=[f"src/{name}.py"])
        self.assertEqual(plan_mod.parallelism(self.grouped()), (3, 1, 3.0))

    def test_a_chain_of_three_is_three_waves_one_wide(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"], depends_on=["01-a"])
        self.unit("03-c", owns=["src/c.py"], depends_on=["02-b"])
        self.assertEqual(plan_mod.parallelism(self.grouped()), (3, 3, 1.0))

    def test_the_ratio_is_on_the_report_with_the_counts_beside_it(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"], depends_on=["01-a"])
        code, out, _err = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("concurrency: 1.0 unit(s) per wave "
                      "(2 unit(s), 2 wave(s))", out)

    def test_a_fully_serial_plan_is_told_it_is_serial(self):
        """The one-line version of the finding that cost ninety-five minutes."""
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"], depends_on=["01-a"])
        _code, out, _err = self.check()
        self.assertIn("every wave is one unit wide", out)

    def test_a_wide_plan_is_not_told_anything_of_the_sort(self):
        for name in ("01-a", "02-b", "03-c"):
            self.unit(name, owns=[f"src/{name}.py"])
        _code, out, _err = self.check()
        self.assertNotIn("runs serially", out)


# --------------------------------------------------------------------------- #
# 2. the contended file
# --------------------------------------------------------------------------- #

class TestBottlenecks(PlanFixture):
    """One file in three units' `owns` is what serialises a plan."""

    def chain(self, count, shared="src/cli.py"):
        """`count` units, each owning `shared`, each waiting on the last —
        which is the only way `owns` lets them coexist in one plan."""
        previous = []
        for index in range(1, count + 1):
            name = f"{index:02d}-unit"
            self.unit(name, owns=[shared, f"src/{name}.py"],
                      depends_on=list(previous))
            previous = [name]
        return shared

    def test_three_owners_are_named_with_the_file_and_the_two_ways_out(self):
        self.chain(3)
        code, out, _err = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("src/cli.py is owned by 3 units", out)
        self.assertIn("01-unit, 02-unit, 03-unit", out)
        self.assertIn("extract it first, or merge those units", out)

    def test_two_owners_are_below_the_threshold_and_stay_unnamed(self):
        """Two units sharing a file is an ordinary `depends_on` edge."""
        self.chain(2)
        _code, out, _err = self.check()
        self.assertNotIn("is owned by 2 units", out)
        self.assertEqual(plan_mod.bottlenecks(self.units()), [])

    def test_the_threshold_is_a_parameter_not_a_literal_in_the_report(self):
        self.chain(2)
        self.assertEqual(
            [path for path, _owners in
             plan_mod.bottlenecks(self.units(), minimum=2)],
            ["src/cli.py"],
        )

    def test_a_glob_owner_counts_as_an_owner_of_what_it_covers(self):
        """`bottlenecks` asks `_OwnsIndex` the same question a collision does:
        a unit owning `src/*.py` is an owner of `src/cli.py`, or the two
        checks disagree about the same pair of units."""
        self.unit("01-a", owns=["src/cli.py"])
        self.unit("02-b", owns=["src/cli.py"], depends_on=["01-a"])
        self.unit("03-c", owns=["src/*.py"], depends_on=["02-b"])
        found = dict(plan_mod.bottlenecks(self.units()))
        self.assertIn("src/cli.py", found)
        self.assertEqual(found["src/cli.py"], ["01-a", "02-b", "03-c"])

    def test_the_most_contended_path_is_reported_first(self):
        self.unit("01-a", owns=["src/cli.py", "src/util.py"])
        self.unit("02-b", owns=["src/cli.py", "src/util.py"],
                  depends_on=["01-a"])
        self.unit("03-c", owns=["src/cli.py"], depends_on=["02-b"])
        self.unit("04-d", owns=["src/cli.py"], depends_on=["03-c"])
        order = [path for path, _ in plan_mod.bottlenecks(self.units(), minimum=2)]
        self.assertEqual(order[0], "src/cli.py")
        self.assertIn("src/util.py", order)


# --------------------------------------------------------------------------- #
# 3. the estimate
# --------------------------------------------------------------------------- #

class TestCriticalPath(PlanFixture):
    """Widest unit per wave, summed — and labelled a guess, because it is."""

    def test_the_estimate_is_the_widest_unit_of_each_wave_summed(self):
        self.unit("01-a", owns=["src/a.py"], budget=30000)
        self.unit("02-b", owns=["src/b.py"], budget=50000)
        self.unit("03-c", owns=["src/c.py"], budget=20000,
                  depends_on=["01-a", "02-b"])
        estimate, total, rows = plan_mod.critical_path(self.grouped())
        self.assertEqual(rows, [(1, 50000), (2, 20000)])
        self.assertEqual(estimate, 70000)
        self.assertEqual(total, 100000)

    def test_a_serial_plan_has_no_saving_and_the_two_numbers_agree(self):
        self.unit("01-a", owns=["src/a.py"], budget=30000)
        self.unit("02-b", owns=["src/b.py"], budget=50000,
                  depends_on=["01-a"])
        estimate, total, _rows = plan_mod.critical_path(self.grouped())
        self.assertEqual((estimate, total), (80000, 80000))

    def test_the_report_calls_it_an_estimate_and_says_what_it_is_made_of(self):
        self.unit("01-a", owns=["src/a.py"], budget=30000)
        self.unit("02-b", owns=["src/b.py"], budget=50000)
        _code, out, _err = self.check()
        self.assertIn("estimated critical path ~50,000 tokens of 80,000 stated",
                      out)
        self.assertIn("an estimate, not a measurement", out)
        self.assertIn("`budget_tokens`", out)

    def test_a_plan_that_states_no_budgets_estimates_zero_rather_than_guessing(self):
        self.unit("01-a", owns=["src/a.py"], budget=0)
        estimate, total, _rows = plan_mod.critical_path(self.grouped())
        self.assertEqual((estimate, total), (0, 0))


# --------------------------------------------------------------------------- #
# 4. ownership gaps
# --------------------------------------------------------------------------- #

class TestOwnershipGaps(PlanFixture):
    """A test that nobody owns, exercising a file somebody does."""

    def test_a_test_importing_an_owned_module_and_owned_by_nobody_is_named(self):
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py",
                   "from pkg import widget\n\n\ndef test_it():\n    assert widget\n")
        gaps, truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertFalse(truncated)
        self.assertEqual(gaps, [("pkg/widget.py", ["tests/test_widget.py"])])

    def test_the_same_test_owned_by_a_unit_is_not_a_gap(self):
        self.unit("01-a", owns=["pkg/widget.py", "tests/test_widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py", "from pkg import widget\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [])

    def test_a_unit_owning_the_test_directory_by_glob_closes_the_gap_too(self):
        self.unit("01-a", owns=["pkg/widget.py", "tests/*.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py", "from pkg import widget\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [])

    def test_the_parenthesised_multi_line_import_is_found(self):
        """The form `tests/test_core.py` actually uses — a regex that stopped
        at the newline would have found nothing and reported all clear."""
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_many.py",
                   "from pkg import (\n    gadget,\n    widget as widget_mod,\n)\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [("pkg/widget.py", ["tests/test_many.py"])])

    def test_a_path_named_in_a_string_counts_as_a_reference(self):
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_paths.py", 'SOURCE = "pkg/widget.py"\n')
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [("pkg/widget.py", ["tests/test_paths.py"])])

    def test_an_unrelated_test_is_not_dragged_in(self):
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_other.py", "from pkg import gadget\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [])

    def test_an_owned_file_that_is_not_a_module_is_skipped_silently(self):
        """`README.md` and a workflow file have no import for this to find,
        and a scan that reported them as clean would be claiming a check it
        never ran."""
        self.unit("01-a", owns=["README.md", ".github/workflows/ci.yml"])
        self.write("tests/test_docs.py", 'DOC = "README.md"\n')
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [])

    def test_a_test_that_only_reaches_the_module_through_the_cli_is_missed(self):
        """The scan's blind spot, asserted rather than assumed.

        This test drives the module through a subprocess, names it nowhere,
        and is invisible to the detection. A clean ownership-gap report means
        "no test names this file", not "no test can break".
        """
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_cli_ish.py",
                   "import subprocess\n\n\n"
                   "def test_it():\n"
                   "    subprocess.run(['mytool', 'run'], check=True)\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [])

    def test_a_file_that_cannot_be_parsed_does_not_take_the_command_down(self):
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_broken.py", "from pkg import widget\ndef (\n")
        gaps, _truncated = plan_mod.ownership_gaps(self.layout, self.units())
        self.assertEqual(gaps, [("pkg/widget.py", ["tests/test_broken.py"])])

    def test_the_report_names_the_source_the_test_and_what_to_do(self):
        self.unit("01-a", owns=["pkg/widget.py"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py", "from pkg import widget\n")
        code, out, _err = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("ownership gaps", out)
        self.assertIn("pkg/widget.py → tests/test_widget.py", out)
        self.assertIn("add each to the `owns` of the unit that will change it",
                      out)


# --------------------------------------------------------------------------- #
# 5. advisory, not a refusal
# --------------------------------------------------------------------------- #

class TestAdvisory(PlanFixture):
    """A slow plan is still a plan: reported, written, exit 0."""

    def serial_with_a_bottleneck(self):
        """Three units chained, all three owning one file — which is both why
        they are chained and why the chain is worth mentioning."""
        previous = []
        for name in ("01-a", "02-b", "03-c"):
            self.unit(name, owns=["src/cli.py", f"src/{name}.py"],
                      depends_on=previous)
            previous = [name]

    def test_a_contended_file_reports_and_the_graph_is_still_written(self):
        self.serial_with_a_bottleneck()
        code, out, _err = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("src/cli.py is owned by 3 units", out)
        self.assertIn("wrote .ctx/plans/widgets/plan.json", out)
        self.assertTrue(plan_mod.graph_path(self.layout, self.slug).is_file())

    def test_strict_escalates_the_same_run_to_exit_1(self):
        self.serial_with_a_bottleneck()
        code, _out, err = self.check("--strict")
        self.assertEqual(code, 1)
        self.assertIn("strict: plan-slow", err)

    def test_a_clean_plan_advises_nothing_even_under_strict(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"])
        self.assertEqual(self.check()[0], 0)
        code, _out, err = self.check("--strict")
        self.assertEqual(code, 0, err)
        self.assertNotIn("plan-slow", err)

    def test_the_advisory_is_the_enumerated_one_and_is_in_the_document(self):
        self.serial_with_a_bottleneck()
        code, document = self.document()
        self.assertEqual(code, 0)
        self.assertEqual(document["advisory"], ["plan-slow"])


# --------------------------------------------------------------------------- #
# 6. --json carries all three
# --------------------------------------------------------------------------- #

class TestTheDocumentCarriesIt(PlanFixture):
    """A pipeline must be able to read this without parsing the prose."""

    def build(self):
        self.unit("01-a", owns=["pkg/widget.py", "src/cli.py"], budget=30000)
        self.unit("02-b", owns=["src/cli.py", "src/b.py"], budget=50000,
                  depends_on=["01-a"])
        self.unit("03-c", owns=["src/cli.py", "src/c.py"], budget=20000,
                  depends_on=["02-b"])
        self.write("pkg/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py", "from pkg import widget\n")

    def test_parallelism_is_in_the_document(self):
        self.build()
        _code, document = self.document()
        self.assertEqual(document["data"]["parallelism"],
                         {"units": 3, "waves": 3, "ratio": 1.0})

    def test_the_bottleneck_is_in_the_document_with_its_owners(self):
        self.build()
        _code, document = self.document()
        self.assertEqual(document["data"]["bottlenecks"],
                         [{"path": "src/cli.py",
                           "units": ["01-a", "02-b", "03-c"]}])

    def test_the_critical_path_is_in_the_document_and_says_it_is_an_estimate(self):
        self.build()
        _code, document = self.document()
        critical = document["data"]["critical_path"]
        self.assertEqual(critical["estimate_tokens"], 100000)
        self.assertEqual(critical["total_tokens"], 100000)
        self.assertEqual(critical["waves"],
                         [{"wave": 1, "tokens": 30000},
                          {"wave": 2, "tokens": 50000},
                          {"wave": 3, "tokens": 20000}])
        self.assertIn("estimate", critical["basis"])
        self.assertIn("not a measurement", critical["basis"])

    def test_the_ownership_gaps_are_in_the_document(self):
        self.build()
        _code, document = self.document()
        self.assertEqual(
            document["data"]["ownership_gaps"],
            {"files": [{"path": "pkg/widget.py",
                        "tests": ["tests/test_widget.py"]}],
             "truncated": False},
        )

    def test_a_refused_plan_still_carries_the_keys_empty(self):
        """A consumer reading `parallelism.units` must not have to branch on
        whether the plan validated."""
        self.unit("01-a", owns=["src/a.py"], depends_on=["99-nobody"])
        code, document = self.document()
        self.assertEqual(code, 1)
        for key in ("parallelism", "bottlenecks", "critical_path",
                    "ownership_gaps"):
            self.assertIn(key, document["data"], key)


if __name__ == "__main__":
    unittest.main()
