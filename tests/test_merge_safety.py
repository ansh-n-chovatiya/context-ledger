"""Where a merge lands, and what a collision check costs.

Two defects are pinned here, both found by running the real thing rather than
reading it.

The first is loss of work: `ctx merge` ran `git merge` at the repo root against
whatever HEAD happened to be. On a detached HEAD the merge commit was reachable
from nothing, and the unit branch — the only other ref to that work — was
deleted a line later, while `ctx status` went on reporting `done`. The softer
version of the same bug merged into whatever branch the user had since checked
out, and named neither it nor the branch the unit was planned against.

The second is cost: `plan.collisions()` runs on every dispatch and compared
every pattern of every unit against every pattern of every other one. At 200
units with a dozen owned paths each it took 9.6 s, which reads as a hang, and it
printed 19,000 sentences at 1,000 units. The tests below fix the semantics in
place first — an index over `owns` is a safety boundary, and a faster check that
matched *less* would be a far worse bug than the slowness.
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, plan as plan_mod, worktree as wt,
)
from support import FAILS, OK, Fixture  # noqa: E402


class MergeSafetyFixture(Fixture):
    """A throwaway project that is also a real git repository.

    The same shape as `test_worktree.WorktreeFixture`, kept local for the same
    reason that fixture is: these assertions are about what git actually does
    with divergent branches and a detached HEAD, so there is nothing here to
    mock and the fixture has to be a real repository.
    """

    slug = "auth"

    def setUp(self):
        super().setUp()
        self.git_init()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, check=True,
        )

    def git_out(self, *args, cwd=None):
        """Combined output of one git command, without insisting it succeeded —
        several assertions below are about a command that must fail."""
        done = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True,
        )
        return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()

    def unit(self, name, *, owns, tier="session", depends_on=(), checks=None):
        checks = [{"kind": "cmd", "run": OK}] if checks is None else checks
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
                "depends_on": list(depends_on), "owns": list(owns), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": list(checks),
            },
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(path)
        self.trust(checks)
        return path

    def plan_ready(self):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 0, out)
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")

    def work_in(self, unit_name, relative, text, commit=True):
        path = wt.path_for(self.layout, self.slug, unit_name) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if commit:
            tree = wt.path_for(self.layout, self.slug, unit_name)
            self.git("add", "-A", cwd=tree)
            self.git("commit", "-qm", f"work {unit_name}", cwd=tree)
        return path

    def dispatched(self, name="01-a", owns=("src/a.py",)):
        """A unit with a worktree, one commit in it, and a clean root tree."""
        self.unit(name, owns=list(owns))
        self.plan_ready()
        _path, branch, created, error = wt.create(self.layout, self.slug, name)
        self.assertEqual(error, "")
        self.assertTrue(created)
        self.work_in(name, owns[0], "x = 1\n")
        return branch

    def unit_meta(self, name="01-a"):
        return frontmatter.read(
            plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        ).meta


# --------------------------------------------------------------------------- #
# where the merge lands
# --------------------------------------------------------------------------- #

class TestMergeTarget(MergeSafetyFixture):
    def test_the_fork_point_is_recorded_when_the_worktree_is_made(self):
        """Creation is the only moment it is knowable — by merge time the user
        may have checked out anything at all."""
        self.dispatched()
        self.assertEqual(self.unit_meta().get("base_branch"), "main")

    def test_a_detached_head_is_refused_and_the_work_stays_reachable(self):
        """The data-loss case, reproduced.

        A merge commit is reachable only from HEAD, and `merge` deletes the unit
        branch the instant it succeeds. On a detached HEAD that left the unit's
        commits with nothing pointing at them — `git log --oneline --all` showed
        only the seed — while `ctx status` still said `done`.
        """
        branch = self.dispatched()
        _code, tip = self.git_out("rev-parse", branch)
        self.git("checkout", "--quiet", "--detach")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = " ".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("detached", joined)
        self.assertIn("git checkout", joined, "it says how to recover")

        code, still = self.git_out("rev-parse", "--verify", branch)
        self.assertEqual(code, 0, "the unit branch must survive a refusal")
        self.assertEqual(still, tip)
        _code, containing = self.git_out("branch", "--contains", tip)
        self.assertNotEqual(containing.strip(), "", "the work is still reachable")
        self.assertTrue(wt.path_for(self.layout, self.slug, "01-a").is_dir())
        self.assertEqual(self.unit_meta()["status"], "pending", "not marked done")

    def test_merging_from_a_different_branch_names_both(self):
        """Created from `main`, merged from `release`: the user is told which is
        which rather than discovering it in the reflog."""
        self.dispatched()
        self.git("checkout", "--quiet", "-b", "release")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = " ".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("main", joined)
        self.assertIn("release", joined)
        self.assertFalse(
            (self.root / "src" / "a.py").exists(), "nothing was merged"
        )
        self.assertEqual(self.unit_meta()["status"], "pending")

    def test_a_worktree_made_before_base_branch_existed_still_merges(self):
        """No recorded fork point is not a reason to refuse — only to say where
        the merge is going."""
        self.dispatched()
        path = plan_mod.units_dir(self.layout, self.slug) / "01-a.md"
        doc = frontmatter.read(path)
        del doc.meta["base_branch"]
        doc.write(path)

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, messages)
        self.assertIn("merging into main", " ".join(messages))

    def test_the_success_path_names_the_branch_it_merged_into(self):
        self.dispatched()
        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, messages)
        joined = " ".join(messages)
        self.assertIn("merging into main", joined)
        self.assertIn("merged ctx/auth/01-a into main", joined)
        self.assertTrue((self.root / "src" / "a.py").is_file())


class TestMergeSharesGatePreflightWithUnitDone(MergeSafetyFixture):
    """`ctx merge` and `ctx unit --status done` both refuse through `verify.
    gate_preflight` (see `worktree._contract_guard`). This pins the specific
    way the two used to drift apart: `_contract_guard` reimplemented only the
    dispatch-seal and contract-intact steps, from when those were the only two
    `gate_check` had. When findings and phases refusals were later added to
    `gate_check`, this copy was never updated to match, and `ctx merge` kept
    landing units with an open critical review finding, or a `kind: bug` fix
    with no recorded reproduction, as `done` — silently outperforming its own
    "route ctx merge through the same gate" fix.
    """

    def bug_unit(self, name="01-a", owns=("src/a.py",)):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        checks = [{"kind": "cmd", "run": OK}]
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "session",
                "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
                "budget_tokens": 1000, "status": "pending", "kind": "bug",
                # `_fix_satisfied` matches `fix`'s recorded `--command` against
                # this field verbatim (`ctx/phases.py:251`), not against
                # `reproduce`'s command — so this has to be the same string
                # `fix` is recorded with below, not free prose.
                "reproduction": OK,
                "verify": checks,
            },
            "## Objective\nFix the crash.\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / f"{name}.md")
        self.trust(checks)

    def test_an_open_critical_finding_blocks_the_merge(self):
        self.dispatched()
        code, out = self.cli("findings", "01-a", "--add", "critical",
                              "--summary", "drops rows on parse",
                              "--where", "src/a.py:1")
        self.assertEqual(code, 0, out)

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = " ".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("blocking review finding", joined)
        self.assertIn(
            "--skip-gate", joined,
            "a ctx merge refusal must name ctx merge's own override flag, "
            "not ctx unit's --force",
        )
        self.assertNotIn("--force", joined)

        # Positive control: the same merge succeeds once the finding is
        # closed, so the refusal above is about the open finding and nothing
        # else.
        code, out = self.cli("findings", "01-a", "--set", "1",
                              "--status", "addressed",
                              "--evidence", "fixed the parser")
        self.assertEqual(code, 0, out)
        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, " ".join(messages))

    def test_a_bug_unit_with_no_recorded_phases_blocks_the_merge(self):
        self.bug_unit()
        self.plan_ready()
        _path, _branch, created, error = wt.create(self.layout, self.slug, "01-a")
        self.assertEqual(error, "")
        self.assertTrue(created)
        self.work_in("01-a", "src/a.py", "x = 1\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = " ".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("no recorded outcome", joined)
        self.assertIn("reproduce", joined)
        self.assertIn("--skip-gate", joined)
        self.assertNotIn("--force", joined)

        # Positive control: recording all four required phases un-blocks it.
        for phase, extra in (
            ("reproduce", ["--command", FAILS, "--exit-code", "1",
                            "--evidence", "crashed as expected"]),
            ("locate", ["--evidence", "src/a.py:1 empty input unguarded"]),
            ("fix", ["--command", OK, "--exit-code", "0"]),
            ("guard", ["--exit-code", "0", "--evidence", "verifier: pass"]),
        ):
            code, out = self.cli("phase", "01-a", phase, "--plan", self.slug, *extra)
            self.assertEqual(code, 0, out)

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, " ".join(messages))


class TestConflictReporting(MergeSafetyFixture):
    def test_a_conflict_names_the_files_not_a_return_code(self):
        """`conflicts, _ = git(...)` unpacked `(returncode, output)` backwards,
        so the refusal read `... was violated: 0` — an integer in the one slot
        the message existed for."""
        self.dispatched()
        # The integration branch moves under the unit: exactly what happens when
        # a sibling lands first, or two merges run at once.
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "someone else edited a.py")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = " ".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("src/a.py", joined, "the conflicting file is named")
        self.assertNotIn("violated: 0", joined)
        self.assertIn("behind main", joined, "and the likely cause is not blame")
        self.assertIn("merge main", joined, "it names the recovery")

        _code, status = self.git_out("status", "--porcelain")
        self.assertNotIn("UU ", status, "the merge was aborted; nothing changed")
        self.assertEqual(
            (self.root / "src" / "a.py").read_text(encoding="utf-8"), "x = 2\n"
        )


class TestAMergeThatNeverStarted(MergeSafetyFixture):
    """The preflight's `.ctx/` exemption, met head-on by git's lack of one.

    `dirty_paths` excludes ledger paths from the clean-tree check by design —
    every `ctx` command appends to the journal, so insisting on a spotless tree
    would mean no merge ever runs. `git merge` has no such exemption. When the
    unit branch also committed a ledger file that the integration tree has
    uncommitted, git refuses to *begin*: no reconciliation is attempted, so
    `_conflicted` finds no unmerged paths and `_landed_since_fork` finds
    nothing either. The failure branch then fell through to its `else` and
    reported "one unit wrote outside its scope" — of a unit whose diff was
    entirely inside `owns`, about a merge that never happened, naming the one
    cause it was not.
    """

    JOURNAL = ".ctx/journal/2026-01-01--t.md"

    def ledger_file(self, text):
        path = self.root / self.JOURNAL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def setUp(self):
        super().setUp()
        self.ledger_file("base\n")

    def dirty_ledger_merge(self):
        """A unit with a clean, in-scope diff — and a ledger file that both the
        branch and the integration tree have moved."""
        self.dispatched()
        tree = wt.path_for(self.layout, self.slug, "01-a")
        (tree / self.JOURNAL).write_text("the unit's own journal write\n",
                                         encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "journal", cwd=tree)
        # ...and ctx's own uncommitted write sitting in the integration tree,
        # which is exactly what the preflight looks past.
        self.ledger_file("a ctx command wrote this and nobody committed it\n")

    def test_the_preflight_still_calls_the_integration_tree_clean(self):
        """The premise. If this ever stops holding, the case below is gone and
        the test is measuring something else."""
        self.dirty_ledger_merge()
        changed, error = wt.dirty_paths(self.layout)
        self.assertEqual(error, "")
        self.assertEqual(changed, [], "the ledger write is excluded by design")
        self.assertIn(self.JOURNAL, wt.ledger_dirt(self.layout))

    def test_the_cause_named_is_the_ledger_dirt_not_a_scope_violation(self):
        self.dirty_ledger_merge()

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertNotIn(
            "wrote outside its scope", joined,
            "the unit's diff was entirely inside `owns` — blaming it sends the "
            "reader auditing the wrong thing",
        )
        self.assertIn("never started", joined, "no merge was attempted at all")
        self.assertIn(self.JOURNAL, joined, "the dirty ledger file is named")
        self.assertIn("commit ctx's own ledger writes", joined,
                      "and the fix is spelled out")

    def test_nothing_landed_and_the_unit_is_not_done(self):
        self.dirty_ledger_merge()

        ok, _messages = wt.merge(self.layout, self.config, self.slug, "01-a")

        self.assertFalse(ok)
        code, _out = self.git_out("rev-parse", "--verify", wt.branch_for(self.slug, "01-a"))
        self.assertEqual(code, 0, "the branch still holds the work")
        self.assertFalse((self.root / "src" / "a.py").exists())
        self.assertEqual(self.unit_meta()["status"], "pending")
        self.assertEqual(
            (self.root / self.JOURNAL).read_text(encoding="utf-8"),
            "a ctx command wrote this and nobody committed it\n",
            "the uncommitted ledger write survives the refusal",
        )

    def test_dealing_with_the_ledger_write_is_all_it_takes(self):
        """The positive control, and the proof the advice is actionable: do what
        the message says about the integration tree and the same merge lands,
        with no change to the unit at all."""
        self.dirty_ledger_merge()
        self.assertFalse(wt.merge(self.layout, self.config, self.slug, "01-a")[0])

        self.git("checkout", "--", self.JOURNAL)

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, "\n".join(messages))
        self.assertTrue((self.root / "src" / "a.py").is_file())

    def test_committing_it_instead_turns_it_into_an_honest_conflict(self):
        """The other half of the advice. Committing a ledger write that really
        does diverge from the branch's is a genuine three-way conflict — and it
        is now reported as one, naming the file and the recovery, rather than as
        a unit writing outside its scope."""
        self.dirty_ledger_merge()
        self.git("add", "-A")
        self.git("commit", "-qm", "ledger")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("merge conflicted in", joined)
        self.assertIn(self.JOURNAL, joined)
        self.assertNotIn("wrote outside its scope", joined)

    def test_a_real_conflict_is_still_reported_as_a_conflict(self):
        """The new branch must not swallow the old one: a merge that genuinely
        started and could not reconcile still names the unmerged paths."""
        self.dispatched()
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "someone else edited a.py")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("merge conflicted in", joined)
        self.assertNotIn("never started", joined)


# --------------------------------------------------------------------------- #
# removal and listing
# --------------------------------------------------------------------------- #

class TestRemoveAndListing(MergeSafetyFixture):
    def test_remove_reports_a_branch_it_could_not_delete(self):
        """It used to print `removed worktree and branch` over a branch that was
        still there: the `branch -D` return code was thrown away, and a refused
        `worktree remove` fell through to it whenever the directory happened to
        be gone already."""
        branch = self.dispatched()
        # Take the tree away behind ctx's back, then hold the branch so it cannot
        # be deleted — the two halves of the old bug at once.
        self.git("worktree", "remove", str(wt.path_for(self.layout, self.slug, "01-a")))
        self.git("checkout", "--quiet", branch)

        error = wt.remove(self.layout, "01-a", self.slug)
        self.assertNotEqual(error, "", "a failed deletion is not a success")
        self.assertIn(branch, error)
        code, _out = self.git_out("rev-parse", "--verify", branch)
        self.assertEqual(code, 0, "and the branch really is still there")

    def test_listing_ignores_a_user_branch_that_merely_contains_ctx(self):
        """`"ctx/" in branch` claimed `docs/ctx/readme` as the unit `readme`, and
        `ctx worktree remove readme` then resolved to `git branch -D
        docs/ctx/readme`. It destroyed nothing only because git refuses to delete
        a checked-out branch — that safety was git's, not ours."""
        self.dispatched()
        theirs = self.untracked / "docs-tree"
        self.git("worktree", "add", "-b", "docs/ctx/readme", str(theirs))

        names = {name for name, _path, _branch in wt.listing(self.layout)}
        self.assertEqual(names, {"01-a"})
        branches = {branch for _n, _p, branch in wt.listing(self.layout)}
        self.assertNotIn("docs/ctx/readme", branches)
        self.assertEqual(
            wt.branch_of(self.layout, "readme"), "",
            "and no unit named `readme` is conjured out of it",
        )

    def test_an_existing_branch_is_reported_as_reused_not_created(self):
        """A stranger's branch of the same name became the unit's starting point
        in silence — and `ctx merge` force-deletes that branch when it lands."""
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        self.git("branch", "ctx/auth/01-a")

        path, branch, created, error = wt.create(self.layout, self.slug, "01-a")
        self.assertFalse(created, "it is not a fresh branch")
        self.assertNotEqual(error, "", "and the reuse is surfaced, not swallowed")
        self.assertIn("already existed", error)
        self.assertIn(branch, error)
        self.assertTrue(path.is_dir())


# --------------------------------------------------------------------------- #
# collisions: same answers, without the quadratic scan
# --------------------------------------------------------------------------- #

def make_unit(name, owns, reads=()):
    """A Unit with no file behind it — `collisions()` only reads frontmatter."""
    return plan_mod.Unit(
        Path(f"{name}.md"),
        frontmatter.Document(
            {"unit": name, "owns": list(owns), "reads": list(reads)}, ""
        ),
    )


# Exact paths, directory prefixes, trailing slashes, globs in either position,
# and a path that only *looks* like it is under another — one of each rule
# `_covers` defines, so agreement below is agreement about all of them.
SCOPE_ZOO = [
    make_unit("01-a", ["src/auth.py", "src/api/"], reads=["src/db/pool.py"]),
    make_unit("02-b", ["src/api/routes.py", "src/*.py"], reads=["src/auth.py"]),
    make_unit("03-c", ["src/db", "docs/ctx/readme.md"], reads=["src/api/routes.py"]),
    make_unit("04-d", ["src/authentic.py", "tests/test_*.py"], reads=["src/db"]),
    make_unit("05-e", ["lib/x.py"], reads=["tests/test_auth.py", "lib/x.py"]),
]


class TestCollisionSemantics(unittest.TestCase):
    def test_the_index_agrees_with_the_pairwise_definition(self):
        """`_overlap` is the rule; `_OwnsIndex` is only meant to be a faster way
        of asking it. `owns` matching is the boundary that makes parallel writes
        safe, so an index that matched less would be a worse defect than the
        slowness it removes."""
        index = plan_mod._OwnsIndex(SCOPE_ZOO)
        for unit in SCOPE_ZOO:
            for path in list(unit.owns) + list(unit.reads):
                expected = set(
                    position for position, other in enumerate(SCOPE_ZOO)
                    if plan_mod._overlap([path], other.owns)
                )
                self.assertEqual(index.owners_of(path), expected, path)

    def test_a_hand_checked_wave_reports_exactly_what_it_used_to(self):
        """Every message, verbatim, in order.

        01-a overlaps 02-b because the glob `src/*.py` covers `src/auth.py`;
        02-b overlaps 03-c because the directory `src/api/` contains
        `src/api/routes.py` *and* the same glob covers it. 03-c then races both
        of them for the one file it reads — 01-a owns it outright and 02-b's
        glob covers it, which is exactly the sort of second answer a cheaper
        index would quietly drop.
        """
        units = [
            make_unit("01-a", ["src/auth.py"]),
            make_unit("02-b", ["src/*.py", "src/api/"]),
            make_unit("03-c", ["src/api/routes.py"], reads=["src/auth.py"]),
        ]
        self.assertEqual(plan_mod.collisions(units), [
            "01-a and 02-b both own src/auth.py "
            "— add `depends_on: [01-a]` to 02-b or split the paths",
            "02-b and 03-c both own src/*.py, src/api/ "
            "— add `depends_on: [02-b]` to 03-c or split the paths",
            "03-c reads src/auth.py while 01-a rewrites it — add "
            "`depends_on: [01-a]` to 03-c",
            "03-c reads src/auth.py while 02-b rewrites it — add "
            "`depends_on: [02-b]` to 03-c",
        ])

    def test_a_unit_reading_what_it_owns_is_not_a_race_with_itself(self):
        units = [make_unit("01-a", ["src/a.py"], reads=["src/a.py"])]
        self.assertEqual(plan_mod.collisions(units), [])

    def test_the_report_is_capped_and_says_how_much_it_left_out(self):
        """A badly cut wave collides quadratically — 19,000 full sentences at
        1,000 units, all printed. The refusal has to be readable to be acted
        on."""
        units = [make_unit("%02d-u" % i, ["src/shared.py"]) for i in range(30)]
        problems = plan_mod.collisions(units)
        self.assertEqual(len(problems), plan_mod.MAX_REPORTED + 1)
        self.assertIn("and 415 more", problems[-1])
        self.assertIn("re-cutting", problems[-1])


class TestCollisionScale(unittest.TestCase):
    def test_two_hundred_units_check_in_well_under_a_second(self):
        """`collisions()` runs on every `ctx start`, via `plan.check()`. The
        pairwise version took 9.6 s for this wave and 4m22s at 1,000 units,
        which is indistinguishable from a hang.

        The bound is loose on purpose: it is here to catch a return to
        quadratic-in-patterns behaviour, not to police a slow machine.
        """
        units = [
            make_unit(
                "%02d-u%d" % (i % 100, i),
                ["src/mod%03d/part%d.py" % (i, k) for k in range(12)],
                ["src/mod%03d/part0.py" % ((i + j + 1) % 200) for j in range(20)],
            )
            for i in range(200)
        ]
        started = time.time()
        problems = plan_mod.collisions(units)
        elapsed = time.time() - started
        self.assertLess(elapsed, 2.0, f"took {elapsed:.2f}s")
        # Ownership is disjoint here; every unit reads twenty files owned by
        # somebody else, so the races are real and must still be found.
        self.assertTrue(problems)
        self.assertTrue(any("rewrites it" in p for p in problems))
        self.assertFalse(any("both own" in p for p in problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
