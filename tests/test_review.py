"""The review loop — the half of the product that needs no commits.

Conventional review hands a reviewer a commit range, which forces the
implementer to commit before being reviewed and so lets the review protocol
dictate the project's git history. These tests pin the alternative: two content
snapshots, diffed. The load-bearing assertion is the first one — the whole loop
runs in a directory that is not a repository — because everything else this
module offers is available elsewhere, and that is not.

The second is the `owns` violation check. `unit-runner.md` has always said
"never write outside `owns` — not a suggestion", and until this landed nothing
performed it. It is decided by comparing two path sets, with no model in the
loop, which is why it can be a Critical finding nobody argues with.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    findings as findings_mod, frontmatter, plan as plan_mod, review as review_mod,
    snapshot as snapshot_mod, verify,
)
from support import Fixture  # noqa: E402


class ReviewFixture(Fixture):
    slug = "billing"

    def unit(self, name="01-api", *, owns=("src/a.py",), reads=(),
             checks=({"kind": "review"},)):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": list(owns),
                "reads": list(reads), "forbid": [], "budget_tokens": 1000,
                "status": "pending", "verify": [dict(c) for c in checks],
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


class TestReviewNeedsNoRepository(ReviewFixture):
    def test_a_package_is_built_with_no_git_at_all(self):
        """The fixture's `.git` is a bare directory, so every git command fails
        — which is what a project with no version control looks like."""
        self.assertFalse((self.root / ".git" / "HEAD").exists())
        unit = self.unit()
        self.write("src/a.py", "def a():\n    return 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)

        self.write("src/a.py", "def a():\n    return 2\n")
        path, stats, problem = self.build(unit)
        self.assertEqual(problem, "")
        body = path.read_text(encoding="utf-8")
        self.assertIn("-    return 1", body)
        self.assertIn("+    return 2", body)
        self.assertEqual(stats["modified"], 1)

    def test_the_package_tells_the_reviewer_not_to_reach_for_git(self):
        unit = self.unit()
        self.write("src/a.py", "x = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "x = 2\n")
        path, _stats, _problem = self.build(unit)
        self.assertIn("do not run git", path.read_text(encoding="utf-8"))

    def test_review_without_a_before_snapshot_refuses_and_says_how(self):
        unit = self.unit()
        self.write("src/a.py", "x = 1\n")
        path, _stats, problem = self.build(unit)
        self.assertIsNone(path)
        self.assertIn("no snapshot to compare against", problem)
        self.assertIn("ctx start", problem, "name the command that would have one")


class TestScopeViolationsAreMechanical(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.subject = self.unit(owns=["src/a.py"])
        self.write("src/a.py", "x = 1\n")
        self.write("src/other.py", "keep\n")
        review_mod.capture_before(
            self.layout, self.config, self.subject, self.slug, self.root
        )

    def test_a_write_outside_owns_is_found_with_no_model_in_the_loop(self):
        self.write("src/a.py", "x = 2\n")
        self.write("src/other.py", "tampered\n")
        path, stats, _problem = self.build(self.subject)
        self.assertEqual(stats["out_of_scope"], 1)
        body = path.read_text(encoding="utf-8")
        self.assertIn("## Scope violations", body)
        self.assertIn("src/other.py", body.split("## Scope violations")[1])

    def test_work_entirely_in_scope_reports_none(self):
        self.write("src/a.py", "x = 2\n")
        path, stats, _problem = self.build(self.subject)
        self.assertEqual(stats["out_of_scope"], 0)
        self.assertIn("None — every changed path", path.read_text(encoding="utf-8"))

    def test_the_ledgers_own_writes_are_never_a_violation(self):
        """Every ctx command appends to the journal and flips a `status:` field.
        Counting those would open every review with a false Critical, and a
        reader who learns to skip that section skips the real ones too."""
        self.write("src/a.py", "x = 2\n")
        self.write(".ctx/journal/2026-01-01.md", "# 2026-01-01\n\n12:00 | edit | x\n")
        path, stats, _problem = self.build(self.subject)
        self.assertEqual(stats["out_of_scope"], 0)
        listing = path.read_text(encoding="utf-8").split("## Files changed")[1]
        self.assertNotIn(".ctx/journal", listing.split("## Scope violations")[0])

    def test_a_deleted_file_is_shown_as_deleted(self):
        """A deletion has no "after" side. Representing it as nothing at all
        would hide the most destructive edit a unit can make."""
        (self.root / "src" / "other.py").unlink()
        path, stats, _problem = self.build(self.subject)
        self.assertEqual(stats["deleted"], 1)
        self.assertIn("- deleted   src/other.py", path.read_text(encoding="utf-8"))


class TestTheFixRoundIsScopedToTheFix(ReviewFixture):
    def test_round_two_diffs_from_what_round_one_saw(self):
        """A re-review that re-reads the whole unit finds new work every round
        and the loop never terminates."""
        unit = self.unit(owns=["src/a.py", "src/b.py"])
        self.write("src/a.py", "x = 1\n")
        self.write("src/b.py", "y = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)

        self.write("src/a.py", "x = 2\n")
        _path, stats, _problem = self.build(unit, 1)
        self.assertEqual(stats["modified"], 1)

        self.write("src/b.py", "y = 2\n")
        path, stats, _problem = self.build(
            unit, 2, previous=review_mod.after_key(self.slug, unit.name, 1)
        )
        self.assertEqual(stats["modified"], 1, "only the fix, not the whole unit")
        body = path.read_text(encoding="utf-8")
        self.assertIn("src/b.py", body)
        self.assertNotIn("-x = 1", body, "round one's change is already reviewed")


class TestSecretsDoNotReachThePackage(ReviewFixture):
    def test_the_package_is_scrubbed_on_render(self):
        """The package is a file a subagent reads, so anything in it reaches a
        model's context. Snapshots hold raw bytes; the render is the boundary."""
        unit = self.unit()
        self.write("src/a.py", "TOKEN = 'placeholder'\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", 'password = "hunter2-not-a-real-secret"\n')
        path, _stats, _problem = self.build(unit)
        body = path.read_text(encoding="utf-8")
        self.assertIn("<<redacted>>", body)
        self.assertNotIn("hunter2-not-a-real-secret", body)


class TestTheGateHoldsOnOpenFindings(ReviewFixture):
    def run_gate(self, unit):
        return verify.run(
            self.layout, self.config, unit.checks, cwd=self.root,
            key=f"{self.slug}/{unit.name}", owns=unit.owns, recorded=[],
            judged=False,
        )

    def test_no_findings_passes(self):
        unit = self.unit()
        _results, verdict = self.run_gate(unit)
        self.assertEqual(verdict, verify.PASS)

    def test_an_open_critical_finding_blocks(self):
        unit = self.unit()
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        ledger.add("critical", "writes outside owns", where="src/other.py:1")
        results, verdict = self.run_gate(unit)
        self.assertEqual(verdict, verify.FAIL)
        self.assertIn("writes outside owns", results[0].message)

    def test_a_minor_finding_never_blocks(self):
        """A loop that reruns for "coverage could be broader" is a loop people
        learn to bypass, which costs more than the finding was worth."""
        unit = self.unit()
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        ledger.add("minor", "could use a docstring", where="src/a.py:1")
        _results, verdict = self.run_gate(unit)
        self.assertEqual(verdict, verify.PASS)

    def test_addressing_the_finding_reopens_the_gate(self):
        unit = self.unit()
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        finding = ledger.add("important", "swallowed error", where="src/a.py:9")
        self.assertEqual(self.run_gate(unit)[1], verify.FAIL)
        ok, problem = ledger.set_status(finding.id, "addressed")
        self.assertTrue(ok, problem)
        self.assertEqual(self.run_gate(unit)[1], verify.PASS)

    def test_findings_outlive_the_session_that_raised_them(self):
        """The point of a file: a gate running days later still knows the change
        was never signed off."""
        unit = self.unit()
        findings_mod.load(self.layout, self.slug, unit.name).add(
            "critical", "unreviewed", where="src/a.py:1"
        )
        reloaded = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(len(reloaded.blocking()), 1)
        self.assertEqual(self.run_gate(unit)[1], verify.FAIL)


class TestTheCommandSurface(ReviewFixture):
    def test_start_takes_the_before_snapshot(self):
        """The snapshot has to exist before the work does, and dispatch is the
        only moment that is known to be true."""
        unit = self.unit(checks=[{"kind": "review"}])
        self.write("src/a.py", "x = 1\n")
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(
            snapshot_mod.load(self.layout, review_mod.before_key(self.slug, unit.name)),
            "dispatch must leave a snapshot to review against",
        )

    def test_review_prints_a_path_and_not_the_diff(self):
        """Reading the diff in the orchestrator is what the separate seat exists
        to avoid; printing it here would put it in the wrong context window."""
        unit = self.unit()
        self.write("src/a.py", "x = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "x = 2\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        self.assertIn("review package", out)
        self.assertNotIn("+x = 2", out)

    def test_findings_refuses_agreement_without_substance(self):
        """There is deliberately no "acknowledged" state: performative agreement
        is the failure this design removes by making it unavailable."""
        unit = self.unit()
        self.cli("findings", unit.name, "--plan", self.slug, "--add", "critical",
                 "--summary", "writes outside owns")
        code, out = self.cli("findings", unit.name, "--plan", self.slug,
                             "--set", "1", "--status", "disputed")
        self.assertEqual(code, 1)
        self.assertIn("evidence", out)

        code, out = self.cli("findings", unit.name, "--plan", self.slug,
                             "--set", "1", "--status", "parked")
        self.assertEqual(code, 1)
        self.assertIn("ruling", out)

    def test_the_round_advances_only_when_a_round_raised_something(self):
        """Re-running a review that found nothing must not burn a round — the
        cap exists to stop an unconvergent loop, not to punish a second look."""
        unit = self.unit()
        self.write("src/a.py", "x = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "x = 2\n")
        for _ in range(3):
            self.cli("review", unit.name, "--plan", self.slug)
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(ledger.round, 1, "a clean review does not advance the round")

    def test_the_cap_stops_the_loop_and_demands_rulings(self):
        """Past the cap the loop does not converge; the failure is structural
        and more rounds only spend tokens on it."""
        unit = self.unit()
        self.write("src/a.py", "x = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "x = 2\n")
        for round_number in range(findings_mod.MAX_ROUNDS):
            code, out = self.cli("review", unit.name, "--plan", self.slug)
            self.assertEqual(code, 0, out)
            self.cli("findings", unit.name, "--plan", self.slug, "--add",
                     "important", "--summary", f"still wrong {round_number}")

        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("round cap reached", out)
        self.assertIn("--status parked", out, "name the way out")
        self.assertIn("/ctx:decide", out, "a ruling is a decision worth recording")

    def test_findings_exits_non_zero_while_work_is_blocked(self):
        unit = self.unit()
        self.cli("findings", unit.name, "--plan", self.slug, "--add", "important",
                 "--summary", "missed a criterion")
        code, out = self.cli("findings", unit.name, "--plan", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("blocking", out)


if __name__ == "__main__":
    unittest.main()
