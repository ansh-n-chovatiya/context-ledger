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
import unittest.mock
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
    another. `dispatch_stats` carries no module-level state and no lock —
    there is nothing to serialise, because every key it reads
    (`package_path`, `after_key`, `before_key`) already carries `slug`,
    `unit.name` and `round_number`. So the thing worth pinning here is not
    "two threads happened not to collide" — a sequential pair of calls would
    never collide either, proving nothing — but that the *scoping itself* is
    what keeps them apart. `_read_concurrently` forces the two reads to be
    genuinely inside `dispatch_stats` at the same instant via a
    `threading.Barrier` seated in the read path (`snapshot.load`), and the
    positive control below breaks the scoping directly — via monkeypatch,
    since there is no lock to disable — to prove this test would notice."""

    # Long enough that a loaded CI box never times out a real rendezvous;
    # short enough that a genuinely deadlocked barrier fails the test instead
    # of hanging the suite.
    RENDEZVOUS_TIMEOUT = 30.0

    def _build_both(self, alpha, beta):
        self.write("src/alpha.py", "a = 1\n")
        self.write("src/beta.py", "b = 1\n")
        review_mod.capture_before(self.layout, self.config, alpha, self.slug, self.root)
        review_mod.capture_before(self.layout, self.config, beta, self.slug, self.root)

        # Give the two units genuinely different-sized changes, so a swapped
        # value is detectable rather than accidentally matching.
        self.write("src/alpha.py", "a = 1\n" + ("# padding\n" * 40))
        self.write("src/beta.py", "b = 2\n")
        self.build(alpha)
        self.build(beta)

    def _read_concurrently(self, alpha, beta):
        """Call `dispatch_stats` for both units from two threads, with a
        rendezvous seated inside `snapshot.load` — the call `dispatch_stats`
        makes, twice, to pull back the head and base manifests it reports on.
        Both threads must arrive at each `snapshot.load` call before either
        is allowed to proceed past it, so the two reads are provably
        overlapping rather than one finishing before the other was even
        scheduled — the failure mode a plain "start two threads" test can't
        rule out on a fast filesystem.
        """
        barrier = threading.Barrier(2, timeout=self.RENDEZVOUS_TIMEOUT)
        real_load = review_mod.snapshot.load

        def gated_load(*args, **kwargs):
            barrier.wait()
            return real_load(*args, **kwargs)

        results = {}
        errors = []

        def run(unit):
            try:
                results[unit.name] = review_mod.dispatch_stats(
                    self.layout, self.slug, unit
                )
            except Exception as exc:  # pragma: no cover - surfaced via errors
                errors.append(exc)

        with unittest.mock.patch.object(review_mod.snapshot, "load", gated_load):
            threads = [threading.Thread(target=run, args=(u,)) for u in (alpha, beta)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        return results, errors

    def test_two_packages_read_concurrently_stay_distinct(self):
        alpha = self.unit("01-alpha", owns=["src/alpha.py"])
        beta = self.unit("02-beta", owns=["src/beta.py"])
        self._build_both(alpha, beta)

        results, errors = self._read_concurrently(alpha, beta)

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

    def test_collapsing_the_per_unit_key_leaks_one_units_bytes_into_the_other(self):
        """The positive control the criterion asks for. `dispatch_stats`
        needs no lock because `after_key`/`package_path` already scope every
        read to `(slug, unit.name, round_number)` — so the way to prove this
        suite would catch a regression is to break that scoping directly,
        the same way the module docstring's hazard describes: cache (here,
        name) the package by anything less specific than the full key, and a
        concurrent wave hands one unit's numbers to another.

        With `after_key` and `package_path` collapsed to ignore `unit_name`,
        both units' builds land on the same manifest directory and the same
        package file — deterministically, since the second `build()` call
        (forced with `force=True` for the head capture) simply overwrites
        what the first one wrote, with no race needed to make that happen.
        Reading both back — even with the same forced-overlap rendezvous
        used above — then reports the identical, last-writer size for both
        units: exactly the failure `test_two_packages_read_concurrently_stay_
        distinct`'s `assertNotEqual` exists to catch.
        """
        alpha = self.unit("01-alpha", owns=["src/alpha.py"])
        beta = self.unit("02-beta", owns=["src/beta.py"])

        def shared_after_key(slug, unit_name, round_number=1):
            return f"{slug}@shared@r{round_number}"

        def shared_package_path(layout, slug, unit_name, round_number=1):
            return review_mod.review_dir(layout) / f"{slug}-shared-r{round_number}.md"

        with unittest.mock.patch.object(review_mod, "after_key", shared_after_key), \
                unittest.mock.patch.object(review_mod, "package_path", shared_package_path):
            self._build_both(alpha, beta)
            results, errors = self._read_concurrently(alpha, beta)

        self.assertEqual(errors, [])
        self.assertIsNotNone(results["01-alpha"])
        self.assertIsNotNone(results["02-beta"])
        self.assertEqual(
            results["01-alpha"]["bytes"], results["02-beta"]["bytes"],
            "collapsing the per-unit key should have produced a shared, "
            "last-writer value for both units — if this ever fails, the "
            "per-unit scoping this suite depends on is protecting nothing",
        )


if __name__ == "__main__":
    unittest.main()
