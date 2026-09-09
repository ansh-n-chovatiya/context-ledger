"""A wave of two must not deadlock its own gate on scope violations it invented.

`subagent`-tier units of one wave run in one working tree, several Task calls
deep, and `snapshot.capture` fingerprints every file in the project. So a
sibling's write to its own declared path lands between this unit's before and
after captures. Checked against this unit's `owns` alone, that sibling's
perfectly legitimate edit came back as a scope violation, in a section whose
own prose says it "is not open to argument. Report it as Critical" — and
`review` is a mechanical gate check, so a Critical blocks the done-gate. Every
wave of two or more units failed its own gate, N−1 times over, on writes
nothing was wrong with. That is the regression pinned here.

The fix widens the check by exactly what concurrency costs it and no more, so
the interesting tests in this file are the ones that prove it was *not* simply
switched off:

- a path nobody in the wave declared is still a violation, still Critical, and
  is asserted **in the same wave** as the clean sibling case — so `out_of_scope`
  returning `[]` would fail this file rather than pass it;
- a unit in a *different* wave is not running now, so its `owns` excuses
  nothing;
- a `done` unit in the same wave has already been reviewed, so its `owns`
  excuses nothing either;
- a unit alone in its wave gets the package it always got, word for word.

The concurrent case is barrier-forced rather than sequential, the way
`11-proofs` reproduced it: both threads inside `capture`, then both writing,
then both inside `build`. Sequential execution is the case nobody doubted.
"""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, plan as plan_mod, review as review_mod,
)
from support import OK, Fixture  # noqa: E402


# A stuck thread must fail the suite, not hang it: a barrier that times out
# breaks for everyone waiting on it, so `concurrently` returns and asserts.
BARRIER_TIMEOUT = 30

ALPHA_MARK = "ALPHA_WROTE_THIS"
BETA_MARK = "BETA_WROTE_THIS"
STRAY_MARK = "NOBODY_DECLARED_THIS"

# The historical wording, quoted verbatim. A single-unit wave must still read
# exactly like this — that is the "byte-identical package" criterion, pinned as
# the literal strings rather than as a paraphrase that could drift.
SOLO_CLEAN_LINE = "None — every changed path is within the declared `owns`."
SOLO_VIOLATION_PROSE = (
    "These paths changed and are **not** covered by the unit's `owns`. This\n"
    "was decided mechanically — no model judged it, and it is not open to\n"
    "argument. Report it as Critical."
)


def section_lines(text, heading):
    """The body lines of one `## heading` section, up to the next heading."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == heading
            continue
        if inside:
            out.append(line)
    return out


def violation_paths(text):
    """`## Scope violations` as a set of paths. The section always exists; when
    nothing is out of scope it says so in prose and lists nothing."""
    return {
        line[2:].strip() for line in section_lines(text, "Scope violations")
        if line.startswith("- ")
    }


class WaveFixture(Fixture):
    """A throwaway ledger with hand-written unit files.

    Units are written straight to disk rather than scaffolded through
    `ctx plan-unit`: what is under test is what `review` does with units that
    are already there, which is the state a dispatched wave is always in.
    """

    slug = "wave"

    def unit(self, name, *, owns, wave=1, status="pending", depends_on=()):
        """A unit file on disk. `wave=None` omits the field entirely, which is
        the state of a plan that has not been through `ctx plan-check` yet."""
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug,
            "tier": "subagent", "depends_on": list(depends_on),
            "owns": list(owns), "reads": [], "forbid": [],
            "budget_tokens": 1000, "status": status,
            "verify": [{"kind": "cmd", "run": OK}],
        }
        if wave is not None:
            meta["wave"] = wave
        frontmatter.Document(
            meta,
            "## Objective\nDo the one thing this unit owns.\n\n"
            "## Acceptance criteria\n1. It does that thing.\n",
        ).write(path)
        return plan_mod.Unit(path, frontmatter.read(path))

    def concurrently(self, work, units):
        """Run `work(unit, barrier)` per unit, in its own thread.

        Exceptions are collected, not raised: an exception inside a thread is
        invisible to unittest, so the test would pass with half of it never
        having run.
        """
        barrier = threading.Barrier(len(units))
        errors = []

        def target(unit):
            try:
                work(unit, barrier)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(f"{getattr(unit, 'name', unit)}: {exc!r}")
                barrier.abort()

        threads = [threading.Thread(target=target, args=(unit,), daemon=True)
                   for unit in units]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT * 2)
            self.assertFalse(thread.is_alive(), "a wave thread never finished")
        self.assertEqual(errors, [])

    def run_wave(self, units, writes):
        """Capture, write and review every unit at the same instant.

        `writes` maps a unit name to `{path: text}` — everything that unit's
        thread writes, which is how a stray write is introduced into the same
        run as the legitimate ones rather than staged separately afterwards.
        """
        packages = {}

        def work(unit, barrier):
            barrier.wait(BARRIER_TIMEOUT)
            review_mod.capture_before(
                self.layout, self.config, unit, self.slug, self.root
            )
            barrier.wait(BARRIER_TIMEOUT)
            for relative, text in writes.get(unit.name, {}).items():
                self.write(relative, text)
            barrier.wait(BARRIER_TIMEOUT)
            packages[unit.name] = review_mod.build(
                self.layout, self.config, unit, self.slug, self.root
            )

        self.concurrently(work, units)
        for name, (built, _stats, problem) in packages.items():
            self.assertEqual(problem, "", f"{name} failed to build a package")
        return {name: (built, stats, built.read_text(encoding="utf-8"))
                for name, (built, stats, _problem) in packages.items()}


class TestAWaveOfTwoWithOneStrayWrite(WaveFixture):
    """The regression and the protection, proved by the same run.

    Two units edit only what they own; one of them also writes a path nobody in
    the wave declared. If the fix had been "stop checking", the stray would go
    unreported and this class would fail. If the fix had been no fix at all,
    each package would name the other unit's file and this class would fail.
    Only the intended behaviour satisfies both halves.
    """

    def setUp(self):
        super().setUp()
        self.alpha = self.unit("01-alpha", owns=["src/alpha.py"])
        self.beta = self.unit("02-beta", owns=["src/beta.py"])
        self.write("src/alpha.py", "alpha = 1\n")
        self.write("src/beta.py", "beta = 1\n")
        self.write("src/nobody.py", "nobody = 1\n")
        self.packages = self.run_wave(
            [self.alpha, self.beta],
            {
                "01-alpha": {"src/alpha.py": f"alpha = 1\n{ALPHA_MARK} = 2\n"},
                "02-beta": {
                    "src/beta.py": f"beta = 1\n{BETA_MARK} = 2\n",
                    # The stray. Written by beta's thread, in the same shared
                    # tree, at the same moment as the legitimate writes.
                    "src/nobody.py": f"nobody = 1\n{STRAY_MARK} = 2\n",
                },
            },
        )

    def test_a_siblings_declared_path_is_not_this_units_violation(self):
        """The regression: each unit edited only what it owns, so neither
        package may accuse the other of anything."""
        for name, sibling_path in (("01-alpha", "src/beta.py"),
                                   ("02-beta", "src/alpha.py")):
            _built, _stats, text = self.packages[name]
            self.assertNotIn(sibling_path, violation_paths(text),
                             f"{name} reported its wave sibling's own path")

    def test_a_path_nobody_declared_is_still_a_violation_in_both_packages(self):
        """The check is not weakened. `src/nobody.py` belongs to no unit in the
        wave, so both units — either of which could have written it — are told
        about it, in the same run that reported zero sibling violations."""
        for name in ("01-alpha", "02-beta"):
            _built, stats, text = self.packages[name]
            self.assertEqual(violation_paths(text), {"src/nobody.py"},
                             f"{name} did not report the undeclared path alone")
            self.assertEqual(stats["out_of_scope"], 1, name)

    def test_the_undeclared_path_is_still_reported_as_critical(self):
        """A violation nobody is asked to treat as blocking does not block. The
        severity instruction has to survive the widening."""
        for name in ("01-alpha", "02-beta"):
            _built, _stats, text = self.packages[name]
            self.assertIn("Report it as Critical.", text, name)

    def test_the_stray_bytes_are_still_quoted(self):
        """Naming a violation without showing it makes a reviewer go and read
        the file, which is the cost the package exists to remove."""
        for name in ("01-alpha", "02-beta"):
            _built, _stats, text = self.packages[name]
            self.assertIn(STRAY_MARK, text, name)

    def test_dispatch_stats_agrees_with_the_package_it_sizes(self):
        """`dispatch_stats` re-derives the count from the stored manifests. If
        it kept the old scope, a routine wave would be routed to a stronger
        reviewer over violations the package does not report."""
        for name, unit in (("01-alpha", self.alpha), ("02-beta", self.beta)):
            got = review_mod.dispatch_stats(self.layout, self.slug, unit)
            self.assertEqual(got["out_of_scope"],
                             self.packages[name][1]["out_of_scope"], name)

    def test_the_package_says_what_it_actually_checked(self):
        """The prose claimed an absolute rule — "not covered by the unit's
        `owns`" — that the code no longer applies. A reviewer reading the old
        wording would either raise a finding the check did not make, or learn
        that this section is approximate. Both are worse than saying it."""
        for name, sibling in (("01-alpha", "02-beta"), ("02-beta", "01-alpha")):
            _built, _stats, text = self.packages[name]
            self.assertIn(sibling, text,
                          f"{name}'s package never names the unit running beside it")
            violations = "\n".join(section_lines(text, "Scope violations"))
            self.assertIn("still running in this wave", violations, name)
            self.assertNotIn(SOLO_VIOLATION_PROSE, violations,
                             f"{name} kept prose asserting a rule it did not apply")

    def test_a_siblings_content_is_not_quoted_into_this_package(self):
        """A sibling's path is no longer a violation, so its bytes are not
        filled in — and the reviewer is told which unit it belongs to instead
        of being left to blame a size cap for the missing diff."""
        _built, _stats, alpha_text = self.packages["01-alpha"]
        self.assertNotIn(BETA_MARK, alpha_text)
        self.assertIn("Another unit's work", alpha_text)
        self.assertIn("src/beta.py", alpha_text)


class TestOnlyTheUnitsOwnWaveExcusesAPath(WaveFixture):
    """`owns` is a free pass only while the unit holding it is running.

    Two ways that could have been got wrong, both of which would quietly turn
    the check into "any path any unit in the plan ever claimed": counting a
    unit from another wave, which is not dispatched yet, and counting a `done`
    unit, whose work has already been reviewed and landed.
    """

    def setUp(self):
        super().setUp()
        self.subject = self.unit("01-alpha", owns=["src/alpha.py"], wave=1)
        self.unit("02-later", owns=["src/later.py"], wave=2,
                  depends_on=["01-alpha"])
        self.unit("03-finished", owns=["src/finished.py"], wave=1,
                  status="done")
        for relative in ("src/alpha.py", "src/later.py", "src/finished.py"):
            self.write(relative, "x = 1\n")

    def _review_after_touching(self, *paths):
        review_mod.capture_before(
            self.layout, self.config, self.subject, self.slug, self.root
        )
        for relative in paths:
            self.write(relative, "x = 2\n")
        built, stats, problem = review_mod.build(
            self.layout, self.config, self.subject, self.slug, self.root
        )
        self.assertEqual(problem, "")
        return stats, built.read_text(encoding="utf-8")

    def test_a_later_waves_owner_does_not_excuse_a_path(self):
        """`02-later` is not running — nothing of its is being written now, so
        a change under its `owns` is an unexplained change."""
        stats, text = self._review_after_touching("src/alpha.py", "src/later.py")
        self.assertEqual(violation_paths(text), {"src/later.py"})
        self.assertEqual(stats["out_of_scope"], 1)

    def test_a_done_units_owner_does_not_excuse_a_path(self):
        """`03-finished` shares the wave but has been reviewed and signed off.
        A change to its paths now is somebody else's, and belongs in a report."""
        stats, text = self._review_after_touching("src/alpha.py",
                                                  "src/finished.py")
        self.assertEqual(violation_paths(text), {"src/finished.py"})
        self.assertEqual(stats["out_of_scope"], 1)

    def test_wave_scope_lists_only_the_units_running_alongside(self):
        """The same rule read straight off the helper, so a failure above says
        which of the two exclusions broke."""
        patterns, siblings = review_mod.wave_scope(
            self.layout, self.slug, self.subject
        )
        self.assertEqual(siblings, [])
        self.assertEqual(patterns, ["src/alpha.py"])


class TestAnUnscheduledPlanDerivesItsWave(WaveFixture):
    """A plan whose units have no `wave:` field yet.

    `ctx plan-check` writes the computed wave back to disk, but `review` can be
    driven against a plan that has not been through it. The wave is then
    derived from the dependency graph, the way `plan.waves` derives it for
    everything else. What must *not* happen is matching one absent `wave`
    against another: that would put every unscheduled unit in the plan into one
    enormous wave and excuse paths nothing is concurrently writing — the exact
    weakening this whole change is trying not to be.
    """

    def setUp(self):
        super().setUp()
        self.subject = self.unit("01-alpha", owns=["src/alpha.py"], wave=None)
        self.unit("02-beta", owns=["src/beta.py"], wave=None)
        self.unit("03-gamma", owns=["src/gamma.py"], wave=None,
                  depends_on=["01-alpha"])

    def test_the_wave_is_derived_from_the_dependency_graph(self):
        patterns, siblings = review_mod.wave_scope(
            self.layout, self.slug, self.subject
        )
        # `02-beta` has no unmet dependency either, so it runs alongside.
        # `03-gamma` waits for this unit, so it is not running at all.
        self.assertEqual(siblings, [("02-beta", ["src/beta.py"])])
        self.assertEqual(patterns, ["src/alpha.py", "src/beta.py"])


class TestASoloUnitIsUnaffected(WaveFixture):
    """No concurrency, no widening, and not a word of difference.

    Most units in most plans are alone in their wave. The wording they produce
    is what reviewers have been reading all along, and changing it for them
    would be churn in the section that matters most, bought for nothing.
    """

    def setUp(self):
        super().setUp()
        self.subject = self.unit("01-solo", owns=["src/solo.py"])
        self.write("src/solo.py", "solo = 1\n")
        review_mod.capture_before(
            self.layout, self.config, self.subject, self.slug, self.root
        )

    def _build(self):
        built, stats, problem = review_mod.build(
            self.layout, self.config, self.subject, self.slug, self.root
        )
        self.assertEqual(problem, "")
        return stats, built.read_text(encoding="utf-8")

    def test_a_clean_solo_package_reads_exactly_as_it_always_did(self):
        self.write("src/solo.py", "solo = 2\n")
        stats, text = self._build()
        self.assertEqual(stats["out_of_scope"], 0)
        self.assertIn(SOLO_CLEAN_LINE, text)
        self.assertNotIn("running alongside", text)

    def test_a_solo_violation_reads_exactly_as_it_always_did(self):
        self.write("src/solo.py", "solo = 2\n")
        self.write("src/stray.py", f"{STRAY_MARK} = 1\n")
        stats, text = self._build()
        self.assertEqual(stats["out_of_scope"], 1)
        self.assertEqual(violation_paths(text), {"src/stray.py"})
        self.assertIn(SOLO_VIOLATION_PROSE, text)
        self.assertNotIn("running alongside", text)

    def test_a_unit_with_no_plan_on_disk_still_reviews(self):
        """`review` is also driven against units the plan loader cannot see —
        an ad-hoc unit file, a plan directory that was moved. Losing the wave
        must fall back to the unit's own `owns`, which reports too much rather
        than excusing too much."""
        stray = review_mod.wave_scope(self.layout, "no-such-plan", self.subject)
        self.assertEqual(stray, (["src/solo.py"], []))


if __name__ == "__main__":
    unittest.main()
