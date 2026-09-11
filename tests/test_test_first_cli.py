"""`test_first`, driven from the command line and nowhere else.

The kind shipped readable and unwritable: `verify` knew how to check a recorded
failing run, and the only thing that could record one was
`ctx.snapshot.record_test_run` — a Python function. A unit that declared
`kind: test_first` therefore could not satisfy it from a terminal at all, which
the wave-5 documentation unit wrote down as a known gap.

`ctx phase --command … --exit-code …` closes it. That command already existed to
record "this command exited with this code, for this unit", which is precisely
what the evidence is; for a `kind: bug` unit it is the same fact the preset
already gates on — `reproduce` demands a non-zero exit and `fix` demands zero
from the same command.

**Everything here goes through `cli.main`.** The one thing written directly is
the unit file itself, because authoring a unit is what an editor is for; from
that point on nothing touches `ctx.snapshot` or `ctx.verify` by hand. A unit
test of `record_test_run` would prove the mechanism and not the surface, and
the surface is what was missing.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, review, snapshot  # noqa: E402
from support import Fixture  # noqa: E402

SLUG = "widget-repair"
UNIT = "01-crash"
TESTS = ["tests/test_widget.py"]
# No shell metacharacters: the path in it has to survive `verify._argv`, which
# is what reads the tested paths back out of a recorded command.
RED = "python3 -m unittest tests/test_widget.py"


class TestFirstFromTheCLI(Fixture):
    def unit_file(self, checks, *, kind="bug"):
        """A unit declaring phases and a `test_first` gate. Authored, not run."""
        units = plan_mod.units_dir(self.layout, SLUG)
        units.mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": UNIT, "plan": SLUG, "tier": "subagent",
            "depends_on": [], "owns": ["src/widget.py"], "reads": [],
            "forbid": [], "budget_tokens": 45000, "status": "pending",
            "verify": list(checks), "kind": kind, "reproduction": RED,
        }
        frontmatter.Document(meta, "## Objective\nFix the widget.\n").write(
            units / f"{UNIT}.md")
        self.write("tests/test_widget.py", "# a test\n")
        self.write("src/widget.py", "# the fix\n")

    def check(self, **extra):
        """A `test_first` check keyed on the round-1 review snapshot — the one
        capture a person can actually take from the CLI (`ctx snapshot`)."""
        return [{"kind": "test_first", "tests": list(TESTS),
                 "key": review.after_key(SLUG, UNIT, 1), **extra}]

    def focus(self):
        self.assertEqual(self.cli("unit", UNIT, "--plan", SLUG)[0], 0)

    # -- the end-to-end run, through the CLI alone ------------------------ #

    def test_record_a_red_run_then_snapshot_then_verify_passes(self):
        """The whole kind, from a terminal: `ctx phase` → `ctx snapshot` → `ctx verify`."""
        self.unit_file(self.check())

        code, out = self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                             "--command", RED, "--exit-code", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("recorded a failing test run of tests/test_widget.py", out)

        # The implementation is captured *after* the failing run, which is the
        # ordering the kind exists to check.
        time.sleep(0.01)
        code, out = self.cli("snapshot", UNIT, "--plan", SLUG,
                             "--phase", "after", "--round", "1")
        self.assertEqual(code, 0, out)

        self.focus()
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("test_first", out)
        self.assertIn("PASS", out.upper())

    def test_without_the_phase_command_the_same_gate_fails(self):
        """The control: identical run with the recording step left out."""
        self.unit_file(self.check())
        self.assertEqual(self.cli("snapshot", UNIT, "--plan", SLUG,
                                  "--phase", "after", "--round", "1")[0], 0)
        self.focus()
        code, out = self.cli("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("no recorded test run touches tests/test_widget.py", out)

    def test_a_run_recorded_after_the_snapshot_still_fails(self):
        """Green-then-red-later is not test-first, and the CLI cannot make it so.

        The surface writes evidence; it does not get to reinterpret it. Recording
        the failing run *after* the implementation snapshot has to fail exactly
        as it does when `record_test_run` is called from Python.
        """
        self.unit_file(self.check())
        self.assertEqual(self.cli("snapshot", UNIT, "--plan", SLUG,
                                  "--phase", "after", "--round", "1")[0], 0)
        time.sleep(0.01)
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED, "--exit-code", "1")[0], 0)
        self.focus()
        code, out = self.cli("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("the implementation snapshot precedes every recorded run", out)

    def test_a_passing_run_recorded_first_does_not_satisfy_the_kind(self):
        """`--exit-code 0` is a green run, and green first proves nothing."""
        self.unit_file(self.check())
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED, "--exit-code", "0")[0], 0)
        time.sleep(0.01)
        self.assertEqual(self.cli("snapshot", UNIT, "--plan", SLUG,
                                  "--phase", "after", "--round", "1")[0], 0)
        self.focus()
        code, out = self.cli("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("the implementation snapshot precedes every recorded run", out)

    # -- what gets recorded, and where ------------------------------------ #

    def test_the_run_is_filed_under_every_key_a_gate_might_read_it_under(self):
        """The done-gate, `ctx verify` and `ctx ci` key snapshots differently.

        Recording under one of them would leave the kind feedable in theory and
        unusable from whichever command the user actually ran.
        """
        self.unit_file(self.check())
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED, "--exit-code", "1")[0], 0)
        for key in (f"{SLUG}/{UNIT}", UNIT, f"ci-{UNIT}",
                    review.before_key(SLUG, UNIT),
                    review.after_key(SLUG, UNIT, 1)):
            with self.subTest(key=key):
                runs = snapshot.test_runs(self.layout, key)
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["paths"], TESTS)
                self.assertEqual(runs[0]["exit_code"], 1)

    def test_a_whole_suite_run_counts_for_the_paths_the_unit_declared(self):
        """`pytest -q` names no path; it still ran the declared tests."""
        self.unit_file(self.check())
        code, out = self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                             "--command", "pytest -q", "--exit-code", "1")
        self.assertEqual(code, 0, out)
        runs = snapshot.test_runs(self.layout, f"{SLUG}/{UNIT}")
        self.assertEqual([r["paths"] for r in runs], [TESTS])

    def test_a_phase_with_no_command_records_nothing(self):
        """Only `--command` *and* `--exit-code` are a run. A note is not."""
        self.unit_file(self.check())
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED)[0], 0)
        self.assertEqual(snapshot.test_runs(self.layout, f"{SLUG}/{UNIT}"), [])

    def test_the_green_run_is_recorded_too_so_the_history_is_the_whole_story(self):
        """`fix` records the same command passing; both runs survive.

        `record_test_run` appends for exactly this reason — the run that proves
        red-before-green is never the last one.
        """
        self.unit_file(self.check())
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED, "--exit-code", "1")[0], 0)
        self.assertEqual(self.cli("phase", UNIT, "locate", "--plan", SLUG,
                                  "--evidence", "src/widget.py:12")[0], 0)
        code, out = self.cli("phase", UNIT, "fix", "--plan", SLUG,
                             "--command", RED, "--exit-code", "0")
        self.assertEqual(code, 0, out)
        self.assertIn("recorded a passing test run", out)
        runs = snapshot.test_runs(self.layout, f"{SLUG}/{UNIT}")
        self.assertEqual([r["exit_code"] for r in runs], [1, 0])

    def test_a_unit_declaring_no_test_first_records_the_paths_it_named(self):
        """No `tests:` to be specific about, so the command's own paths stand."""
        self.unit_file([{"kind": "exists", "path": "src/widget.py"}])
        self.assertEqual(self.cli("phase", UNIT, "reproduce", "--plan", SLUG,
                                  "--command", RED, "--exit-code", "1")[0], 0)
        runs = snapshot.test_runs(self.layout, f"{SLUG}/{UNIT}")
        self.assertEqual([r["paths"] for r in runs], [TESTS])


if __name__ == "__main__":
    unittest.main()
