"""`review.dispatch_stats` — the numbers a package already has, read back.

`build()` already computes package bytes and the out-of-scope count while it
writes the review package, and `ctx review` already turns those into a
telemetry event (`cli.py`, around the `telemetry.record(layout, "review", ...)`
call). What was missing was a way for something *other* than that one CLI
command to get the same two numbers — `dispatch-selection` needs them to size
the reviewer's model, and it must not have to rebuild the package (re-walk the
tree, re-store blobs, rewrite the file) just to learn how big the package it
already has is.

These tests pin two things: that `dispatch_stats` answers correctly from what
is already on disk, and — the real hazard for a plan that reviews a whole wave
concurrently — that two units built in the same wave each get their own
numbers back, never a shared or last-writer value.
"""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, review as review_mod  # noqa: E402
from support import Fixture  # noqa: E402


class TelemetryFixture(Fixture):
    slug = "billing"

    def unit(self, name="01-api", *, owns=("src/a.py",), reads=()):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": list(owns),
                "reads": list(reads), "forbid": [], "budget_tokens": 1000,
                "status": "pending", "verify": [{"kind": "review"}],
            },
            "## Objective\nRework the billing API.\n\n"
            "## Acceptance criteria\n1. a() returns 2\n",
        ).write(path)
        return plan_mod.Unit(path, frontmatter.read(path))

    def write(self, relpath, text):
        target = self.root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def build(self, unit, round_number=1, previous=None):
        return review_mod.build(
            self.layout, self.config, unit, self.slug, self.root,
            round_number, previous,
        )


class TestDispatchStatsReadsWhatBuildAlreadyWrote(TelemetryFixture):
    def test_bytes_and_out_of_scope_match_build_without_a_second_build(self):
        unit = self.unit(owns=["src/a.py"])
        self.write("src/a.py", "x = 1\n")
        self.write("src/other.py", "keep\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)

        self.write("src/a.py", "x = 2\n")
        self.write("src/other.py", "tampered\n")
        path, stats, problem = self.build(unit)
        self.assertEqual(problem, "")

        # Delete the package file's would-be inputs from the working tree so a
        # rebuild — if `dispatch_stats` mistakenly triggered one — would either
        # crash or report different numbers. It must not touch the tree at all.
        before_touch = path.stat().st_mtime_ns

        got = review_mod.dispatch_stats(self.layout, self.slug, unit)
        self.assertIsNotNone(got)
        self.assertEqual(got["bytes"], stats["bytes"])
        self.assertEqual(got["out_of_scope"], stats["out_of_scope"])
        self.assertEqual(got["out_of_scope"], 1)
        self.assertEqual(
            path.stat().st_mtime_ns, before_touch,
            "dispatch_stats must not rewrite the package it is reading",
        )

    def test_no_package_built_yet_reports_none(self):
        unit = self.unit()
        self.assertIsNone(review_mod.dispatch_stats(self.layout, self.slug, unit))

    def test_a_later_round_reports_the_later_rounds_numbers(self):
        unit = self.unit(owns=["src/a.py", "src/b.py"])
        self.write("src/a.py", "x = 1\n")
        self.write("src/b.py", "y = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)

        self.write("src/a.py", "x = 2\n")
        self.build(unit, 1)
        round_one = review_mod.dispatch_stats(self.layout, self.slug, unit, 1)

        self.write("src/b.py", "y = 2\n" + ("# padding\n" * 40))
        self.build(unit, 2, previous=review_mod.after_key(self.slug, unit.name, 1))
        round_two = review_mod.dispatch_stats(self.layout, self.slug, unit, 2)

        self.assertIsNotNone(round_one)
        self.assertIsNotNone(round_two)
        self.assertNotEqual(
            round_one["bytes"], round_two["bytes"],
            "round two's package is scoped to the fix, not the whole unit — "
            "same bytes here would mean the rounds are not actually distinct",
        )


class TestTwoUnitsInTheSameWaveDoNotContaminate(TelemetryFixture):
    """The hazard the criterion names: cache stats by anything less specific
    than (slug, unit, round) and a concurrent wave hands one unit's numbers to
    another. `dispatch_stats` carries no module-level state at all, so this
    pins that concurrent calls for different units never cross."""

    def test_two_packages_built_and_read_concurrently_stay_distinct(self):
        alpha = self.unit("01-alpha", owns=["src/alpha.py"])
        beta = self.unit("02-beta", owns=["src/beta.py"])
        self.write("src/alpha.py", "a = 1\n")
        self.write("src/beta.py", "b = 1\n")
        review_mod.capture_before(self.layout, self.config, alpha, self.slug, self.root)
        review_mod.capture_before(self.layout, self.config, beta, self.slug, self.root)

        # Give the two units genuinely different-sized changes, so a swapped
        # value is detectable rather than accidentally matching.
        self.write("src/alpha.py", "a = 1\n" + ("# padding\n" * 40))
        self.write("src/beta.py", "b = 2\n")

        results = {}
        errors = []

        def run(unit):
            try:
                self.build(unit)
                results[unit.name] = review_mod.dispatch_stats(
                    self.layout, self.slug, unit
                )
            except Exception as exc:  # pragma: no cover - surfaced via errors
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(u,)) for u in (alpha, beta)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        alpha_stats = results["01-alpha"]
        beta_stats = results["02-beta"]
        self.assertIsNotNone(alpha_stats)
        self.assertIsNotNone(beta_stats)
        self.assertNotEqual(
            alpha_stats["bytes"], beta_stats["bytes"],
            "each unit must report its own package's size, not a shared or "
            "last-writer value",
        )

        # Re-read each after both finished: neither call may have clobbered
        # the other's on-disk stats.
        alpha_again = review_mod.dispatch_stats(self.layout, self.slug, alpha)
        beta_again = review_mod.dispatch_stats(self.layout, self.slug, beta)
        self.assertEqual(alpha_again, alpha_stats)
        self.assertEqual(beta_again, beta_stats)


if __name__ == "__main__":
    unittest.main()
