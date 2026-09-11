"""Worktrees are partitioned by plan, and a removal resolves rather than guesses.

The defect these pin: `worktree.path_for` took a unit name and nothing else, so
two plans with a unit called `01-api` — numbered kebab names make that likely,
not exotic — shared one directory at `.ctx/runtime/worktrees/01-api`. Running
`ctx worktree remove 01-api --force` from the second plan deleted the first
plan's live checkout and the uncommitted work inside it, and reported success.

`test_removing_one_plan_leaves_the_other_plans_tree_alone` is the positive
control: written against the old flat layout it fails, destroying plan-a's tree
and the file in it.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, worktree as wt  # noqa: E402
from support import OK, Fixture  # noqa: E402


class PlanScopeFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.git_init()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, check=True,
        )

    def unit_in(self, plan_slug, name):
        directory = plan_mod.units_dir(self.layout, plan_slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": plan_slug, "tier": "session",
                "depends_on": [], "owns": [f"src/{plan_slug}.py"], "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": [{"kind": "cmd", "run": OK}],
            },
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / f"{name}.md")
        self.trust([{"kind": "cmd", "run": OK}])

    def dispatched(self, plan_slug, name):
        self.unit_in(plan_slug, name)
        path, branch, created, error = wt.create(self.layout, plan_slug, name)
        self.assertEqual(error, "")
        self.assertTrue(created, f"{plan_slug}/{name} got no worktree")
        return path, branch

    def branch_exists(self, branch):
        return subprocess.run(
            ["git", "rev-parse", "--verify", branch], cwd=str(self.root),
            capture_output=True, text=True,
        ).returncode == 0

    def write(self, tree, relative, text):
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target


class TestThePathCarriesThePlan(PlanScopeFixture):
    def test_path_for_partitions_by_plan(self):
        a = wt.path_for(self.layout, "plan-a", "01-api")
        b = wt.path_for(self.layout, "plan-b", "01-api")
        self.assertNotEqual(a, b, "two plans must not share one directory")
        self.assertEqual(a.parent.name, "plan-a")
        self.assertEqual(a.name, "01-api")
        self.assertEqual(a.parent.parent, wt.worktree_root(self.layout))

    def test_a_missing_slug_is_an_error_not_a_flat_path(self):
        """An optional slug would have preserved the collision for every caller
        that did not opt in, which was all of them."""
        with self.assertRaises(ValueError):
            wt.path_for(self.layout, "", "01-api")

    def test_create_puts_the_tree_under_its_plan(self):
        path, branch = self.dispatched("plan-a", "01-api")
        self.assertEqual(path, wt.path_for(self.layout, "plan-a", "01-api"))
        self.assertEqual(branch, wt.branch_for("plan-a", "01-api"))
        self.assertTrue(path.is_dir())


class TestRemovalIsScopedToOnePlan(PlanScopeFixture):
    def test_removing_one_plan_leaves_the_other_plans_tree_alone(self):
        """The reason this partition exists.

        Against the flat layout this fails at the first assertion: plan-b's
        forced removal resolved to `worktrees/01-api`, which *was* plan-a's
        checkout, and took the uncommitted file with it.
        """
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        precious = self.write(a_path, "src/precious.py", "PRECIOUS = 1\n")
        b_path, b_branch = self.dispatched("plan-b", "01-api")
        self.assertNotEqual(a_path, b_path)

        self.assertEqual(wt.remove(self.layout, "01-api", "plan-b", force=True), "")

        self.assertTrue(a_path.is_dir(), "plan-a's worktree was destroyed")
        self.assertEqual(
            precious.read_text(encoding="utf-8"), "PRECIOUS = 1\n",
            "plan-a's uncommitted work was destroyed",
        )
        self.assertTrue(self.branch_exists(a_branch), "plan-a's branch was deleted")
        self.assertFalse(b_path.exists(), "plan-b's own tree should be gone")
        self.assertFalse(self.branch_exists(b_branch), "and so should its branch")

    def test_an_ambiguous_name_is_refused_and_names_every_plan(self):
        """No slug and two candidates: refuse. Picking one is the bug."""
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        b_path, b_branch = self.dispatched("plan-b", "01-api")

        error = wt.remove(self.layout, "01-api", force=True)

        self.assertNotEqual(error, "", "an ambiguous removal must not proceed")
        self.assertIn("plan-a", error)
        self.assertIn("plan-b", error)
        self.assertIn("--plan", error, "and it says how to disambiguate")
        self.assertTrue(a_path.is_dir(), "neither tree may be removed")
        self.assertTrue(b_path.is_dir(), "neither tree may be removed")
        self.assertTrue(self.branch_exists(a_branch), "neither branch may be deleted")
        self.assertTrue(self.branch_exists(b_branch), "neither branch may be deleted")

    def test_one_candidate_still_needs_no_slug(self):
        """The ordinary single-plan case keeps working, which is what `ctx
        worktree remove <name>` drives."""
        path, branch = self.dispatched("plan-a", "01-api")
        self.assertEqual(wt.remove(self.layout, "01-api"), "")
        self.assertFalse(path.exists())
        self.assertFalse(self.branch_exists(branch))

    def test_a_tree_on_another_branch_is_not_removed(self):
        """The directory's name is a hope; git's answer is the fact."""
        path, branch = self.dispatched("plan-a", "01-api")
        self.git("checkout", "-q", "-b", "sidetrack", cwd=path)

        error = wt.remove(self.layout, "01-api", "plan-a", force=True)

        self.assertNotEqual(error, "", "a tree on another branch must be refused")
        self.assertIn("sidetrack", error, "the branch that is actually there")
        self.assertIn(branch, error, "and the branch that was expected")
        self.assertTrue(path.is_dir(), "the tree survives")
        self.assertTrue(self.branch_exists("sidetrack"), "and so does its branch")
        self.assertTrue(self.branch_exists(branch))


class TestChangesAreFoundThroughTheBranch(PlanScopeFixture):
    """`branch_changes` gets the unit *and the plan* out of the branch name.

    The slug is hyphenated on purpose: recovering it by splitting on `-`, or by
    taking the last path element alone, gives a directory that does not exist —
    and a worktree that cannot be found is a worktree whose uncommitted work is
    silently dropped by the merge.
    """

    slug = "billing-api-v2"

    def test_committed_and_uncommitted_work_are_both_reported(self):
        tree, branch = self.dispatched(self.slug, "01-api")
        self.assertIn(self.slug, str(tree))
        self.write(tree, f"src/{self.slug}.py", "x = 1\n")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "work", cwd=tree)

        committed, warning = wt.branch_changes(self.layout, branch)
        self.assertEqual(committed, [f"src/{self.slug}.py"])
        self.assertEqual(warning, "", "nothing is uncommitted yet")

        self.write(tree, "src/later.py", "y = 2\n")
        committed, warning = wt.branch_changes(self.layout, branch)
        self.assertEqual(committed, [f"src/{self.slug}.py"])
        self.assertIn("src/later.py", warning, "the uncommitted file was not seen")

    def test_the_slug_survives_the_round_trip_through_the_branch(self):
        self.assertEqual(
            wt.split_branch(wt.branch_for(self.slug, "01-api")),
            (self.slug, "01-api"),
        )
        self.assertEqual(wt.split_branch("docs/ctx/readme"), ("", "docs/ctx/readme"))


class TestTheOldFlatLayoutIsNotOrphaned(PlanScopeFixture):
    """Decision: `remove` *finds* a flat tree rather than reporting that it
    cannot. A directory left by the previous layout is a real checkout that may
    hold real work, and the alternative — telling the user it cannot be found —
    leaves it to be cleaned up by hand with `git worktree remove`.
    """

    def flat(self, plan_slug, unit_name):
        """A worktree where the old layout put it: `worktrees/<unit>`."""
        self.unit_in(plan_slug, unit_name)
        path = wt.legacy_path_for(self.layout, unit_name)
        wt.worktree_root(self.layout).mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-b", wt.branch_for(plan_slug, unit_name),
                 str(path), "HEAD")
        return path

    def test_remove_finds_a_flat_tree_without_being_told_the_plan(self):
        path = self.flat("plan-a", "01-api")
        self.assertEqual(wt.remove(self.layout, "01-api"), "")
        self.assertFalse(path.exists(), "the flat tree was orphaned")
        self.assertFalse(self.branch_exists(wt.branch_for("plan-a", "01-api")))

    def test_remove_finds_a_flat_tree_when_it_is_told_the_plan(self):
        path = self.flat("plan-a", "01-api")
        self.assertEqual(wt.remove(self.layout, "01-api", "plan-a"), "")
        self.assertFalse(path.exists(), "the flat tree was orphaned")

    def test_a_flat_tree_still_protects_uncommitted_work(self):
        path = self.flat("plan-a", "01-api")
        self.write(path, "src/precious.py", "PRECIOUS = 1\n")
        self.assertNotEqual(wt.remove(self.layout, "01-api"), "", "must refuse")
        self.assertTrue((path / "src" / "precious.py").is_file())
        self.assertEqual(wt.remove(self.layout, "01-api", force=True), "")
        self.assertFalse(path.exists())

    def test_a_flat_tree_and_a_scoped_tree_of_one_name_are_ambiguous(self):
        """Two trees, one name, no slug: still a refusal, not a coin toss."""
        flat = self.flat("plan-a", "01-api")
        scoped, _branch = self.dispatched("plan-b", "01-api")

        error = wt.remove(self.layout, "01-api", force=True)

        self.assertNotEqual(error, "")
        self.assertIn("plan-b", error)
        self.assertTrue(flat.is_dir())
        self.assertTrue(scoped.is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
