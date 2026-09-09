"""`test_first`: the verify kind that asks whether the test ever went red.

A gate that only checks "does it pass now" cannot tell a test written before
the implementation from one written after it to describe what already
happened — both look identical once green. The evidence that distinguishes
them is a timestamp: a failing run recorded *before* the implementation
snapshot proves the test constrained the code; a failing run recorded after,
or no run recorded at all, proves nothing.

These tests exercise the mechanism directly through `ctx.snapshot` rather than
through a real dispatch, because `test_first` is isolated on purpose — nothing
else in this plan wires it up yet, so the fixture supplies the two pieces of
evidence (a captured manifest, a recorded run) by hand.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import snapshot, verify  # noqa: E402
from support import FAILS, Fixture  # noqa: E402

KEY = "demo-plan/demo-unit"
TESTS = ["tests/test_widget.py"]


def _force_taken_at(layout, key, when):
    """Rewrite a captured manifest's timestamp so ordering can be controlled.

    `snapshot.capture` always stamps `taken_at` with the wall clock, which
    makes "the implementation landed before the test ever failed" impossible
    to arrange from a single test process without this — two calls a
    microsecond apart already have the ordering the assertion needs to
    control, not observe.
    """
    path = snapshot.snapshot_dir(layout, key) / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["taken_at"] = when
    path.write_text(json.dumps(data), encoding="utf-8")


class TestFirstKind(Fixture):
    def make_checks(self, **check):
        checks = [{"kind": "test_first", "tests": TESTS, **check}]
        self.trust(checks)
        return checks

    def run_checks(self, checks):
        return verify.run(self.layout, self.config, checks, cwd=self.root, key=KEY)

    def capture(self, taken_at):
        snapshot.capture(self.layout, self.config, KEY, self.root)
        _force_taken_at(self.layout, KEY, taken_at)

    # -- criterion 1: red before the implementation snapshot passes -------- #

    def test_failing_run_before_implementation_passes(self):
        snapshot.record_test_run(self.layout, KEY, TESTS, exit_code=1, when=100.0)
        self.capture(taken_at=200.0)

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.PASS)
        self.assertEqual(results[0].status, verify.PASS)

    def test_a_later_passing_run_does_not_erase_the_earlier_failing_one(self):
        # Units keep running their tests after they turn green — the most
        # recent run is (almost) always the passing one. That must not bury
        # the earlier failing run that actually proves test-first.
        snapshot.record_test_run(self.layout, KEY, TESTS, exit_code=1, when=100.0)
        self.capture(taken_at=200.0)
        snapshot.record_test_run(self.layout, KEY, TESTS, exit_code=0, when=300.0)

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.PASS)

    # -- criterion 2: implementation before every run fails, and says so --- #

    def test_implementation_before_every_run_fails_naming_the_tests(self):
        self.capture(taken_at=100.0)
        snapshot.record_test_run(self.layout, KEY, TESTS, exit_code=1, when=200.0)

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.FAIL)
        self.assertEqual(results[0].status, verify.FAIL)
        self.assertIn("tests/test_widget.py", results[0].message)
        self.assertIn("precedes", results[0].message)

    def test_only_passing_runs_before_the_implementation_also_fails(self):
        # A run recorded before the implementation is not enough on its own —
        # it has to be a *failing* run, or it proves nothing about test-first.
        self.capture(taken_at=200.0)
        snapshot.record_test_run(self.layout, KEY, TESTS, exit_code=0, when=100.0)

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.FAIL)

    # -- criterion 3: no recorded runs fails, not passes -------------------- #

    def test_no_recorded_runs_fails_rather_than_passing_silently(self):
        self.capture(taken_at=100.0)

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.FAIL)
        self.assertEqual(results[0].status, verify.FAIL)

    def test_runs_recorded_for_a_different_test_path_do_not_count(self):
        self.capture(taken_at=200.0)
        snapshot.record_test_run(
            self.layout, KEY, ["tests/test_unrelated.py"], exit_code=1, when=100.0
        )

        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(verdict, verify.FAIL)

    # -- configuration problems are errors, not silent passes --------------- #

    def test_missing_tests_field_errors(self):
        checks = self.make_checks()
        del checks[0]["tests"]

        results, verdict = self.run_checks(checks)

        self.assertEqual(results[0].status, verify.ERROR)

    def test_no_snapshot_captured_yet_errors(self):
        results, verdict = self.run_checks(self.make_checks())

        self.assertEqual(results[0].status, verify.ERROR)

    # -- criterion 4: cheapest-first ordering and short-circuit ------------- #

    def test_ordered_places_test_first_beside_review_and_before_cmd(self):
        checks = [
            {"kind": "cmd", "run": FAILS},
            {"kind": "test_first", "tests": TESTS},
            {"kind": "diff"},
        ]
        kinds = [c["kind"] for c in verify.ordered(checks)]
        self.assertEqual(kinds.index("diff"), 0)
        self.assertLess(kinds.index("test_first"), kinds.index("cmd"))

    def test_a_failing_test_first_short_circuits_the_expensive_cmd_after_it(self):
        self.capture(taken_at=100.0)  # no run ever recorded — guaranteed FAIL
        checks = self.make_checks()
        checks.append({"kind": "cmd", "run": FAILS})
        self.trust(checks)

        results, verdict = self.run_checks(checks)

        self.assertEqual(verdict, verify.FAIL)
        self.assertEqual(len(results), 1, "the cmd check must never have run")


if __name__ == "__main__":
    unittest.main()
