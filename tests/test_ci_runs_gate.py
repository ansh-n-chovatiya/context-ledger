"""`ctx ci` runs the done-gate, instead of describing a plan it never gated.

`verify.verify_plan`'s docstring says, in as many words, "this is what CI
runs". It was not: its only callers were inside `cmd_verify`'s `--plan`
branch, and `cmd_ci` called neither it nor `gate_check`. Everything `ctx ci`
checked about a plan was `plan.check` — the graph parses, the waves are
collision-free — which is a statement about the plan *file*, not about the
work. So a unit that had rewritten its own contract after dispatch, a unit
carrying an open blocking review finding, and a `kind: bug` unit that recorded
no phases at all were each refused outright by `ctx unit --status done` on a
developer's machine and reported as "all checks passed" by the pipeline.

The two halves this file pins:

  * **The refusals reach the exit code.** Each case below is proved twice —
    once through `ctx unit --status done`, which is the gate everyone agrees
    is the gate, and once through `ctx ci`, which must now agree with it and
    name the same reason. A test that only asserted ci exits 1 would pass
    against a ci that failed for any reason at all.
  * **The units it may not gate.** A `pending` unit has no dispatch seal, and
    `gate_check` refuses a sealless unit whose siblings are sealed — so gating
    the pending half of a plan mid-wave would paint every pipeline red for the
    normal state of a wave in flight. A `done` unit was gated when it was
    marked done. Both are excluded, and both exclusions have a test, because a
    fail-red ci gets switched off and then guards nothing at all.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import commands, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class CiGateFixture(Fixture):
    """A real repository, a plan whose units are dispatched through `ctx start`
    so that there is a seal for the gate to compare anything against."""

    slug = "routes"

    def setUp(self):
        super().setUp()
        self.git_init()

    # -- building a plan --------------------------------------------------- #

    def unit(self, name="01-api", *, owns=None, checks=None, wave=1,
             depends_on=(), **extra):
        checks = list(checks if checks is not None else [{"kind": "cmd", "run": OK}])
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": list(depends_on), "owns": list(owns or [f"src/{name}.py"]),
            "reads": [], "forbid": [], "budget_tokens": 1000, "status": "pending",
            "wave": wave, "verify": checks,
        }
        meta.update(extra)
        frontmatter.Document(meta, CRITERIA).write(directory / f"{name}.md")
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return directory / f"{name}.md"

    def wave_of_two(self):
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"])
        self.dispatch()

    def dispatch(self, *extra):
        code, out = self.cli("start", *extra)
        self.assertEqual(code, 0, out)
        return out

    def edit(self, name="01-api", **changes):
        """Edit a unit file the way a runner holding `Write` would."""
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        doc.write(path)
        return path

    def forge(self, name="01-api"):
        """Rewrite a unit's sealed promise — and accept the command it now
        names first, so that the only thing wrong with this tree is the
        forgery. `ctx ci` already fails on a `verify` command this machine has
        not accepted, and a test that let that stand would pass against a ci
        that never ran a gate at all."""
        checks = [{"kind": "cmd", "run": self.py("print(0)")}]
        self.trust(checks)
        self.edit(name, verify=checks)
        return checks

    # -- driving it -------------------------------------------------------- #

    def ci(self, *extra):
        return self.cli("ci", "--plan", self.slug, *extra)

    def done(self, name="01-api"):
        return self.cli("unit", name, "--status", "done", "--plan", self.slug)

    def rows(self):
        """`ctx ci --json`'s check rows, as a pipeline reads them."""
        code, out, err = self.cli_streams("ci", "--plan", self.slug, "--json")
        self.assertEqual(err, "")
        return code, json.loads(out)["data"]

    def gate_rows(self, data):
        return [row for row in data["checks"] if "done-gate" in row["name"]]


# --------------------------------------------------------------------------- #
# the refusals that used to stop at the developer's machine
# --------------------------------------------------------------------------- #

class TestCiRefusesWhatTheDoneGateRefuses(CiGateFixture):

    def test_a_clean_wave_in_flight_is_still_green(self):
        """The control, and the first thing to check: the gate ci now runs must
        not simply refuse everything. Two dispatched units, work in the tree,
        nothing wrong — green, and silent about the gate."""
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        code, out = self.ci()
        self.assertEqual(code, 0, out)
        self.assertIn("all checks passed", out)
        self.assertNotIn("done-gate", out)

    def test_a_contract_rewritten_after_dispatch_fails_ci(self):
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.forge("01-api")

        code, out = self.ci()
        self.assertEqual(code, 1, out)
        self.assertIn("01-api", out)
        self.assertIn("done-gate", out)
        self.assertIn("contract changed after it was dispatched", out)
        self.assertIn("changed: verify", out)

    def test_an_open_blocking_finding_fails_ci(self):
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        self.assertEqual(
            self.cli("findings", "01-api", "--plan", self.slug, "--add",
                     "important", "--summary", "the retry loop never exits")[0],
            0,
        )

        code, out = self.ci()
        self.assertEqual(code, 1, out)
        self.assertIn("01-api", out)
        self.assertIn("blocking review finding", out)
        self.assertIn("the retry loop never exits", out)

    def test_a_bug_unit_that_recorded_no_phases_fails_ci(self):
        """The third thing `gate_check` gained, and the one a plan file cannot
        show: `kind: bug` requires reproduce/locate/fix/guard, and a unit that
        recorded none of them never ran the door."""
        self.unit("01-api", owns=["src/api.py"], kind="bug",
                  reproduction="call /login twice; the second call hangs")
        self.dispatch()
        self.write("src/api.py", "a = 1\n")

        code, out = self.ci()
        self.assertEqual(code, 1, out)
        self.assertIn("01-api", out)
        self.assertIn("reproduce", out)
        self.assertIn("no recorded outcome", out)

    def test_ci_and_the_done_gate_give_the_same_verdict_and_reason(self):
        """Not two gates that happen to agree today: the same refusal, named
        the same way, from the command a person runs and from the pipeline."""
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.edit("01-api", owns=["src/api.py", "src/extra.py"])

        ci_code, ci_out = self.ci()
        done_code, done_out = self.done("01-api")
        self.assertEqual((ci_code, done_code), (1, 1), ci_out + done_out)
        for out in (ci_out, done_out):
            self.assertIn("contract changed after it was dispatched", out)
            self.assertIn("changed: owns", out)
        self.assertEqual(
            plan_mod.find_unit(self.layout, self.slug, "01-api").status, "running",
            "ci observes the gate; it must not move the unit",
        )

    def test_the_failing_unit_is_named_in_the_failure_summary(self):
        self.wave_of_two()
        self.forge("02-store")
        code, out = self.ci()
        self.assertEqual(code, 1, out)
        summary = [line for line in out.splitlines() if "check(s) failed" in line]
        self.assertTrue(summary, out)
        self.assertIn("02-store", summary[0])


# --------------------------------------------------------------------------- #
# the units ci may not gate
# --------------------------------------------------------------------------- #

class TestCiGatesOnlyTheUnitsInFlight(CiGateFixture):

    def test_a_pending_unit_in_a_later_wave_is_not_gated(self):
        """`gate_check` refuses a unit with no dispatch seal once a sibling has
        one. Wave 2 is pending by definition while wave 1 runs, so gating it
        would make a wave in flight a permanently red pipeline."""
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"], wave=2, depends_on=["01-api"])
        self.dispatch()
        self.write("src/api.py", "a = 1\n")

        code, out = self.ci()
        self.assertEqual(code, 0, out)
        self.assertNotIn("no dispatch seal", out)
        _code, data = self.rows()
        self.assertEqual([row["name"] for row in self.gate_rows(data)],
                         ["01-api would pass the done-gate"])

    def test_a_done_unit_is_not_re_gated(self):
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        self.assertEqual(self.done("01-api")[0], 0)

        code, data = self.rows()
        self.assertEqual(code, 0, data)
        self.assertEqual([row["name"] for row in self.gate_rows(data)],
                         ["02-store would pass the done-gate"])

    def test_an_invalid_graph_is_reported_once_and_not_gated(self):
        """`plan.check` has already said this plan does not describe a runnable
        wave. Gating its units restates the same problem in a worse voice.

        Asserted against the same plan before the graph is broken, so that "no
        gate rows" means the gate was skipped here and not that this command
        never gates anything."""
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        _code, before = self.rows()
        self.assertEqual(len(self.gate_rows(before)), 2, before)

        self.edit("02-store", owns=["src/api.py"])  # now both own src/api.py
        code, data = self.rows()
        self.assertEqual(code, 1, data)
        self.assertIn("graph is valid and collision-free", data["failures"])
        self.assertEqual(self.gate_rows(data), [])


# --------------------------------------------------------------------------- #
# what a pipeline reads
# --------------------------------------------------------------------------- #

class TestTheEnvelopeIsUnchanged(CiGateFixture):
    """`cli.JSON_COMMANDS` promises ci's document shape. The gate changes what
    feeds the verdict, never the shape of the answer."""

    def test_every_row_still_carries_exactly_the_four_pinned_keys(self):
        self.wave_of_two()
        self.forge("01-api")
        code, data = self.rows()
        self.assertEqual(code, 1, data)
        self.assertEqual(sorted(data), ["checks", "failures", "ok"])
        for row in data["checks"]:
            self.assertEqual(sorted(row), ["detail", "name", "ok", "section"])
            self.assertIsInstance(row["detail"], str)

    def test_a_passing_gate_is_still_recorded_as_a_row(self):
        """Silent in the prose — a line per running unit saying nothing is
        wrong is noise in output read line by line — but never silent in the
        document, or a consumer cannot tell a gate that passed from one that
        never ran."""
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        code, data = self.rows()
        self.assertEqual(code, 0, data)
        rows = self.gate_rows(data)
        self.assertEqual(len(rows), 2, rows)
        self.assertTrue(all(row["ok"] for row in rows), rows)
        self.assertTrue(all(row["section"] == f"plan {self.slug}" for row in rows))

    def test_the_reason_travels_in_the_row_detail(self):
        self.wave_of_two()
        self.forge("01-api")
        _code, data = self.rows()
        refused = [row for row in self.gate_rows(data) if not row["ok"]]
        self.assertEqual(len(refused), 1, refused)
        self.assertEqual(refused[0]["detail"], "contract edited after dispatch")
        self.assertIn(refused[0]["name"], data["failures"])


# --------------------------------------------------------------------------- #
# the selection rule itself
# --------------------------------------------------------------------------- #

class TestGateableUnits(unittest.TestCase):
    """`_gateable_units` is the whole of the fail-red risk, so it is asserted
    directly as well as through the command."""

    class _Unit:
        def __init__(self, name, status):
            self.name, self.status = name, status

    def grouped(self, *pairs):
        return {1: [self._Unit(name, status) for name, status in pairs]}

    def test_dispatched_and_unfinished_units_are_gated(self):
        grouped = self.grouped(("a", "running"), ("b", "blocked"),
                               ("c", "verify_failed"))
        self.assertEqual([u.name for u in commands._gateable_units(grouped)],
                         ["a", "b", "c"])

    def test_pending_and_done_units_are_not(self):
        grouped = self.grouped(("a", "pending"), ("b", "done"), ("c", "running"))
        self.assertEqual([u.name for u in commands._gateable_units(grouped)], ["c"])

    def test_waves_are_visited_in_order(self):
        grouped = {2: [self._Unit("late", "running")],
                   1: [self._Unit("early", "running")]}
        self.assertEqual([u.name for u in commands._gateable_units(grouped)],
                         ["early", "late"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
