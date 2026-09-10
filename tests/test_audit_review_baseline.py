"""A re-run of `ctx start` must not destroy the evidence `ctx review` judges.

The audit's fifth P0. `cmd_start` called `review.capture_before` unconditionally
for every unit in the wave, `snapshot.capture` began with an unconditional
`rmtree`, and nothing ever set `status: running` — a unit stayed `pending` until
someone typed `--status done`. So the documented crash-recovery step ("run
`/ctx:start` again") re-snapshotted units that were mid-flight or already
finished, over their completed state. `ctx review` then diffed post-work against
post-work, handed the reviewer a package saying *nothing changed outside the
ledger's own bookkeeping*, and collected `verdict: approved` for work nobody had
looked at.

The seal added beside it is the same finding at a second call site. A re-run did
not merely lose the baseline — it re-sealed whatever the unit file said *now* as
the contract it was dispatched with, silently undoing the forgery refusal on the
line above. Both are guarded here, and both are tested with a positive control:
`--rebaseline` is shown to genuinely replace the manifest and genuinely re-seal,
so the refusal is a guard rather than a capture that quietly stopped happening.

Every assertion about "the baseline survived" is on the manifest's bytes, not on
its mtime. A capture that rewrote identical-looking metadata with a fresh
`taken_at` and a fresh file table would pass an mtime check and still have
destroyed the diff.
"""

import hashlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    cli, contract as contract_mod, dispatch as dispatch_mod,
    findings as findings_mod, frontmatter, plan as plan_mod,
    review as review_mod, snapshot as snapshot_mod,
)
from support import FAILS, OK, Fixture  # noqa: E402


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class BaselineFixture(Fixture):
    """A dispatchable plan whose units can be worked, forged and re-started."""

    slug = "auth"

    def unit(self, name="01-api", checks=None, *, owns=None, wave=1,
             depends_on=(), body=CRITERIA):
        checks = [{"kind": "cmd", "run": OK}] if checks is None else list(checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": list(depends_on),
                "owns": list(owns or [f"src/{name}.py"]), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "wave": wave, "verify": checks,
            },
            body,
        ).write(path)
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return path

    # -- driving it ------------------------------------------------------- #

    def dispatch(self, *extra):
        code, out = self.cli("start", "--no-worktree", *extra)
        self.assertEqual(code, 0, out)
        return out

    def work(self, name="01-api", text="x = 2\n"):
        """What the runner does between the two dispatches."""
        return self.write(f"src/{name}.py", text)

    def mark_done(self, name="01-api", *extra):
        return self.cli("unit", name, "--status", "done", *extra)

    def find(self, name="01-api"):
        return plan_mod.find_unit(self.layout, self.slug, name)

    def status_of(self, name="01-api"):
        return frontmatter.read(
            plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        ).meta["status"]

    def edit(self, name="01-api", body=None, **changes):
        """Edit a unit file the way a runner holding `Write` would."""
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        if body is not None:
            doc.body = body
        doc.write(path)
        return path

    # -- looking at the evidence ------------------------------------------ #

    def before_key(self, name="01-api"):
        return review_mod.before_key(self.slug, name)

    def manifest_path(self, name="01-api"):
        return (snapshot_mod.snapshot_dir(self.layout, self.before_key(name))
                / "manifest.json")

    def manifest_digest(self, name="01-api"):
        """The bytes of the stored manifest, hashed.

        Not `st_mtime`: a re-capture writes a new `taken_at` and a new file
        table into a file of much the same shape, which an mtime assertion
        cannot tell from a capture that never happened at all.
        """
        return hashlib.sha256(self.manifest_path(name).read_bytes()).hexdigest()

    def baseline_text(self, name="01-api"):
        """The captured bytes of the unit's owned file, as of dispatch."""
        return snapshot_mod.stored_text(
            self.layout, self.before_key(name), f"src/{name}.py"
        )

    def seal(self, name="01-api"):
        return contract_mod.load_seal(self.layout, self.slug, name)

    def package(self, name="01-api"):
        unit = self.find(name)
        path, stats, problem = review_mod.build(
            self.layout, self.config, unit, self.slug, self.root
        )
        self.assertEqual(problem, "", "the review must have something to diff")
        return path.read_text(encoding="utf-8"), stats

    def journal_text(self):
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(self.layout.journal.glob("*.md"))
        )


# --------------------------------------------------------------------------- #
# 1 — the before snapshot survives a second dispatch
# --------------------------------------------------------------------------- #

class TestTheBaselineSurvivesARestart(BaselineFixture):

    def test_a_second_start_does_not_overwrite_the_before_snapshot(self):
        """Criterion 1, on the stored bytes."""
        self.unit()
        self.write("src/01-api.py", "x = 1\n")
        self.dispatch()
        first = self.manifest_digest()
        self.assertEqual(self.baseline_text(), "x = 1\n")

        self.work()  # the unit does its work
        self.dispatch()  # the crash-recovery step, run over the finished work

        self.assertEqual(self.manifest_digest(), first,
                         "the pre-work manifest was replaced by a post-work one")
        self.assertEqual(self.baseline_text(), "x = 1\n",
                         "and the captured bytes still predate the work")

    def test_the_manifest_is_kept_even_with_no_unit_file_status_to_read(self):
        """The guard is the evidence on disk, not the unit's `status:` field.

        A runner holds `Write` over its own unit file, so a status set back to
        `pending` must not buy it a fresh baseline — otherwise the guard is one
        line of YAML away from being off.
        """
        self.unit()
        self.write("src/01-api.py", "x = 1\n")
        self.dispatch()
        first = self.manifest_digest()
        self.edit(status="pending")  # laundering the flag
        self.work()
        self.dispatch()
        self.assertEqual(self.manifest_digest(), first)

    def test_snapshot_capture_refuses_a_second_capture_under_the_same_key(self):
        """The guard read straight off the primitive, so a failure above says
        which layer broke."""
        self.write("a.txt", "one\n")
        first = snapshot_mod.capture(self.layout, self.config, "k", self.root)
        self.write("a.txt", "two\n")
        again = snapshot_mod.capture(self.layout, self.config, "k", self.root)
        self.assertEqual(again["files"]["a.txt"]["sha"],
                         first["files"]["a.txt"]["sha"])
        forced = snapshot_mod.capture(self.layout, self.config, "k", self.root,
                                      force=True)
        self.assertNotEqual(forced["files"]["a.txt"]["sha"],
                            first["files"]["a.txt"]["sha"],
                            "positive control: `force` still replaces it")


# --------------------------------------------------------------------------- #
# 1b — nor is the contract re-sealed
# --------------------------------------------------------------------------- #

class TestTheSealSurvivesARestart(BaselineFixture):

    def test_a_contract_forged_after_dispatch_is_still_refused_after_a_restart(self):
        """Criterion 1b. The crash-recovery step must not launder a forgery."""
        self.unit()
        self.dispatch()
        sealed = self.seal()["digest"]

        self.edit(owns=["src/01-api.py", "src/anything-i-like.py"])
        self.dispatch()  # run again, exactly as the docs tell a crashed session to

        self.assertEqual(self.seal()["digest"], sealed,
                         "the seal still records what was dispatched")
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("contract changed after", out)
        self.assertIn("changed: owns", out, "and names the field that moved")
        self.assertNotEqual(self.status_of(), "done")

    def test_an_untouched_contract_still_reaches_done_after_a_restart(self):
        """Positive control for 1b: the refusal is about the edit, not about
        having run `ctx start` twice."""
        self.unit()
        self.dispatch()
        self.work()
        self.dispatch()
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_a_trimmed_acceptance_criterion_is_still_caught_after_a_restart(self):
        """The other half of the promise — the body, not the frontmatter."""
        self.unit()
        self.dispatch()
        self.edit(body="## Objective\nDo the thing.\n\n## Acceptance criteria\n")
        self.dispatch()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("acceptance criteria", out)


# --------------------------------------------------------------------------- #
# 2 — the review still has something to review
# --------------------------------------------------------------------------- #

class TestTheReviewStillSeesTheWork(BaselineFixture):

    def test_review_after_a_second_start_still_diffs_against_pre_work(self):
        """Criterion 2. The empty package that reviews as `approved` is the
        exact failure this unit exists to prevent, so both halves are asserted:
        the package names the changed file, and does not carry the sentence it
        prints when nothing changed."""
        self.unit()
        self.write("src/01-api.py", "x = 1\n")
        self.dispatch()
        self.work(text="x = 2\n")
        self.dispatch()

        text, stats = self.package()
        self.assertIn("- modified  src/01-api.py", text)
        self.assertNotIn("nothing changed outside the ledger", text)
        self.assertIn("x = 2", text, "and quotes what actually landed")
        self.assertGreaterEqual(stats["modified"], 1)

    def test_the_cli_review_reports_a_non_empty_diff_too(self):
        """The same thing through the command a session actually runs."""
        self.unit()
        self.write("src/01-api.py", "x = 1\n")
        self.dispatch()
        self.work(text="x = 2\n")
        self.dispatch()
        code, out = self.cli("review", "01-api")
        self.assertEqual(code, 0, out)
        self.assertNotIn("~0 ", out, "a package with no modifications is the bug")


# --------------------------------------------------------------------------- #
# 3, 4 — running, and what a second start says about it
# --------------------------------------------------------------------------- #

class TestDispatchRecordsThatItDispatched(BaselineFixture):

    def test_start_sets_running_on_every_unit_it_dispatches(self):
        """Criterion 3, on disk — a status held only in memory would be gone by
        the time the next process asked."""
        self.unit("01-api")
        self.unit("02-store")
        self.dispatch()
        for name in ("01-api", "02-store"):
            self.assertEqual(self.status_of(name), "running")
            raw = (plan_mod.units_dir(self.layout, self.slug)
                   / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn("status: running", raw)

    def test_a_second_start_reports_the_units_as_in_flight_by_name(self):
        """Criterion 4."""
        self.unit("01-api")
        self.unit("02-store")
        self.dispatch()
        out = self.dispatch()
        self.assertIn("in flight", out)
        self.assertIn("01-api", out)
        self.assertIn("02-store", out)
        self.assertIn("--rebaseline", out, "and says how to retake one on purpose")

    def test_the_first_start_says_nothing_about_in_flight_units(self):
        """Positive control for criterion 4: the report is about state that
        exists, not a banner printed on every dispatch."""
        self.unit()
        out = self.dispatch()
        self.assertNotIn("in flight", out)

    def test_a_unit_added_to_a_dispatched_wave_is_still_snapshotted(self):
        """The guard is per unit, not per wave: a unit that has never been out
        gets its baseline even when its wave-mates are mid-flight."""
        self.unit("01-api")
        self.dispatch()
        self.unit("02-store")
        out = self.dispatch()
        self.assertIn("in flight", out)
        self.assertIsNotNone(
            snapshot_mod.load(self.layout, self.before_key("02-store")),
            "the new unit was dispatched for the first time and must have one",
        )


# --------------------------------------------------------------------------- #
# 5, 5b — the deliberate escape hatch
# --------------------------------------------------------------------------- #

class TestRebaseline(BaselineFixture):

    def test_rebaseline_replaces_the_manifest_for_that_unit_only(self):
        """Criteria 5 and 11. The positive control that makes criterion 1 mean
        something: the refusal is a guard, not a capture that stopped
        happening."""
        self.unit("01-api")
        self.unit("02-store")
        self.write("src/01-api.py", "x = 1\n")
        self.dispatch()
        first_api = self.manifest_digest("01-api")
        first_store = self.manifest_digest("02-store")

        self.work("01-api", "x = 2\n")
        out = self.dispatch("--rebaseline", "01-api")

        self.assertNotEqual(self.manifest_digest("01-api"), first_api,
                            "the named unit really was re-captured")
        self.assertEqual(self.baseline_text("01-api"), "x = 2\n",
                         "over the tree as it is now")
        self.assertEqual(self.manifest_digest("02-store"), first_store,
                         "and nothing else in the wave was touched")
        self.assertIn("re-baselined 01-api", out)

    def test_rebaseline_reseals_the_contract_as_it_now_stands(self):
        """Criterion 5: the escape hatch is only honest if the new contract
        becomes the new promise, rather than the old seal being dropped."""
        self.unit()
        self.dispatch()
        before = self.seal()["digest"]
        self.edit(owns=["src/01-api.py", "src/extra.py"])
        self.assertEqual(self.mark_done()[0], 1, "refused while the seal stands")

        self.dispatch("--rebaseline", "01-api")
        self.assertNotEqual(self.seal()["digest"], before, "re-sealed")
        self.assertEqual(self.seal()["digest"],
                         contract_mod.digest(self.find()),
                         "against the unit exactly as it reads now")
        code, out = self.mark_done()
        self.assertEqual(code, 0, "so the same unit is now answerable to it: " + out)

    def test_rebaseline_is_journalled_plainly_enough_to_audit(self):
        """Criterion 5. A loophole nobody can find afterwards is not an escape
        hatch; the entry has to name the unit and say what it did."""
        self.unit()
        self.dispatch()
        self.dispatch("--rebaseline", "01-api")
        text = self.journal_text()
        self.assertIn("re-baselined", text)
        self.assertIn("01-api", text)
        self.assertIn("re-captured", text)
        self.assertIn("re-sealed", text)

    def test_rebaseline_keeps_the_findings_the_unit_is_answerable_to(self):
        """Criterion 5b. A unit sent back for round 2 does not get to shed the
        findings that sent it back by being re-baselined."""
        self.unit()
        self.dispatch()
        code, out = self.cli("findings", "01-api", "--add", "critical",
                             "--summary", "the parser drops rows",
                             "--where", "ctx/parse.py:40")
        self.assertEqual(code, 0, out)
        self.assertIn("1", self.seal()["findings"], "sealed at the time it was raised")

        self.dispatch("--rebaseline", "01-api")
        self.assertIn("1", self.seal()["findings"],
                      "and still sealed after the re-baseline")

        findings_mod.path_for(self.layout, self.slug, "01-api").unlink()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("finding [1]", out)
        self.assertIn("deleted", out)

    def test_rebaselining_a_name_that_is_not_in_the_wave_says_so(self):
        """The whole point of typing it is that something gets retaken, so a
        name that matches nothing is said out loud rather than ignored."""
        self.unit()
        self.dispatch()
        out = self.dispatch("--rebaseline", "99-nope")
        self.assertIn("99-nope", out)
        self.assertIn("not a unit of wave 1", out)

    def test_rebaseline_is_repeatable(self):
        self.unit("01-api")
        self.unit("02-store")
        self.write("src/01-api.py", "a = 1\n")
        self.write("src/02-store.py", "b = 1\n")
        self.dispatch()
        first = (self.manifest_digest("01-api"), self.manifest_digest("02-store"))
        self.work("01-api", "a = 2\n")
        self.work("02-store", "b = 2\n")
        self.dispatch("--rebaseline", "01-api", "--rebaseline", "02-store")
        self.assertNotEqual(self.manifest_digest("01-api"), first[0])
        self.assertNotEqual(self.manifest_digest("02-store"), first[1])


# --------------------------------------------------------------------------- #
# 6 — `done` still works, and the gate still applies
# --------------------------------------------------------------------------- #

class TestDoneStillWorksOnARunningUnit(BaselineFixture):

    def test_a_running_unit_reaches_done_through_the_gate(self):
        """Criterion 6."""
        self.unit()
        self.dispatch()
        self.assertEqual(self.status_of(), "running")
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_a_running_unit_with_a_failing_check_is_still_refused(self):
        """Criterion 6's other half: `running` is not a status the gate is
        lenient about."""
        self.unit(checks=[{"kind": "cmd", "run": FAILS}])
        self.dispatch()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("the gate did not pass", out)
        self.assertEqual(self.status_of(), "running",
                         "the transition did not happen")


# --------------------------------------------------------------------------- #
# 7, 8 — a status nothing had ever set, read by everything that reads statuses
# --------------------------------------------------------------------------- #

class TestRunningIsUnderstoodByTheAdvisor(BaselineFixture):

    def test_next_does_not_advise_start_for_a_wave_already_in_flight(self):
        """Criterion 7. `/ctx:start` for a dispatched wave is advice that never
        changes anything and never stops being given — and it used to cost the
        baselines as well."""
        self.unit("01-api")
        self.unit("02-store")
        self.dispatch()
        command, why = cli._next_action(self.layout, self.config)
        self.assertNotIn("/ctx:start", command)
        self.assertIn("in flight", why)
        self.assertIn("01-api", why)

    def test_next_still_advises_start_when_a_unit_has_not_been_dispatched(self):
        """Positive control for criterion 7: a wave with work left to send out
        is not reported as in flight."""
        self.unit("01-api")
        self.unit("02-store")
        code, out = self.cli("start", "--no-worktree", "--wave", "1")
        self.assertEqual(code, 0, out)
        self.edit("02-store", status="pending")  # never went out
        command, why = cli._next_action(self.layout, self.config)
        self.assertEqual(command, "/ctx:start")
        self.assertIn("wave 1", why)

    def test_next_advises_start_before_anything_is_dispatched(self):
        self.unit()
        command, _why = cli._next_action(self.layout, self.config)
        self.assertEqual(command, "/ctx:start")

    def test_next_reports_the_plan_complete_once_the_wave_is_done(self):
        self.unit()
        self.dispatch()
        self.assertEqual(self.mark_done()[0], 0)
        command, _why = cli._next_action(self.layout, self.config)
        self.assertEqual(command, "/ctx:handoff")


class TestRunningIsRenderedDistinctly(BaselineFixture):

    def test_the_wave_board_marks_a_running_unit(self):
        """Criterion 8."""
        self.unit("01-api")
        self.unit("02-store", wave=2, depends_on=["01-api"])
        self.cli("start", "--no-worktree", "--wave", "1")
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        board = [line for line in out.splitlines() if "01-api" in line]
        self.assertTrue(board, out)
        self.assertIn("running (in flight)", board[0])
        pending = [line for line in out.splitlines() if "02-store" in line]
        self.assertIn("pending", pending[0])
        self.assertNotIn("in flight", pending[0])

    def test_a_done_unit_is_not_rendered_as_running(self):
        self.unit()
        self.dispatch()
        self.assertEqual(self.mark_done()[0], 0)
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        row = [line for line in out.splitlines() if "01-api" in line][0]
        self.assertIn("done", row)
        self.assertNotIn("in flight", row)

    def test_the_boards_next_line_does_not_point_at_start_for_a_flying_wave(self):
        self.unit()
        self.dispatch()
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        nxt = [line for line in out.splitlines() if line.strip().startswith("next:")]
        self.assertTrue(nxt, out)
        self.assertIn("in flight", nxt[0])

    def test_the_unit_listing_marks_running_too(self):
        self.unit()
        self.dispatch()
        code, out = self.cli("unit")  # no name: prints the listing
        self.assertEqual(code, 0, out)
        self.assertIn("running (in flight)", out)


# --------------------------------------------------------------------------- #
# 9 — selection still works now that dispatched units are `running`
# --------------------------------------------------------------------------- #

class TestSelectionStillSelects(BaselineFixture):

    def test_prepare_still_returns_a_wave_whose_units_are_running(self):
        """Criterion 9. `running` is not `done`: a unit that failed its gate
        has to be re-dispatchable, and a wave holding one is not finished."""
        self.unit("01-api")
        self.unit("02-store")
        self.dispatch()
        level, units, problems, budget = dispatch_mod.prepare(
            self.layout, self.config, self.slug
        )
        self.assertEqual(problems, [])
        self.assertEqual(level, 1)
        self.assertEqual(sorted(u.name for u in units), ["01-api", "02-store"])
        self.assertEqual(budget, 2000)

    def test_a_second_start_does_not_report_nothing_left_to_dispatch(self):
        self.unit()
        self.dispatch()
        out = self.dispatch()
        self.assertNotIn("nothing left to dispatch", out)
        self.assertIn("01-api", out, "the brief still names it for re-dispatch")

    def test_a_wave_whose_units_are_all_done_is_finished(self):
        """Positive control: `running` keeps a wave open, `done` closes it."""
        self.unit()
        self.dispatch()
        self.assertEqual(self.mark_done()[0], 0)
        _level, units, problems, _budget = dispatch_mod.prepare(
            self.layout, self.config, self.slug
        )
        self.assertEqual(units, [])
        self.assertEqual(problems, ["plan is complete — every unit is done"])

    def test_a_unit_sent_back_after_a_failed_gate_still_dispatches(self):
        self.unit(checks=[{"kind": "cmd", "run": FAILS}])
        self.dispatch()
        self.assertEqual(self.mark_done()[0], 1)
        out = self.dispatch()
        self.assertIn("01-api", out)
        self.assertIn("in flight", out)


# --------------------------------------------------------------------------- #
# 10 — a snapshot must never block a dispatch
# --------------------------------------------------------------------------- #

class TestASnapshotNeverBlocksADispatch(BaselineFixture):

    def test_an_oserror_from_the_capture_is_reported_and_dispatch_continues(self):
        """Criterion 10, unchanged behaviour — asserted here because the
        capture call now carries an argument it did not before."""
        self.unit()
        with mock.patch.object(cli.review_mod, "capture_before",
                               side_effect=OSError("no space left on device")):
            code, out = self.cli("start", "--no-worktree")
        self.assertEqual(code, 0, out)
        self.assertIn("could not snapshot 01-api", out)
        self.assertIn("no space left on device", out)
        self.assertIn("Wave 1", out, "and the brief was still printed")

    def test_a_unit_whose_snapshot_failed_is_not_snapshotted_after_the_fact(self):
        """The dangerous half. Its `status: running` says it went out, so a
        later capture would fingerprint the finished work and call it the
        baseline — which is the lie, not the missing snapshot. The gate already
        handles an absent baseline by saying so and falling through."""
        self.unit()
        with mock.patch.object(cli.review_mod, "capture_before",
                               side_effect=OSError("no space left on device")):
            self.assertEqual(self.cli("start", "--no-worktree")[0], 0)
        self.work()
        out = self.dispatch()
        self.assertIn("in flight", out)
        self.assertIsNone(
            snapshot_mod.load(self.layout, self.before_key()),
            "no baseline was invented over the finished work",
        )
        code, out = self.mark_done()
        self.assertEqual(code, 0, "the missing snapshot blocks nothing: " + out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
