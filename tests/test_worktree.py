"""Worktree tier tests — phase 6.

These run against real git repositories, because the whole point of the tier is
what git actually does with two divergent branches. The assertions that matter:

* Nothing merges past a failed gate, and the gate runs in the *unit's own* tree.
* A unit that wrote outside `owns` is refused, and refusing changes nothing.
* Two units in a wave really do merge cleanly — the second merge is not a
  fast-forward, which is the bug in the original design this implementation
  corrects.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, plan as plan_mod, state, verify, worktree as wt,
)
from support import Fixture  # noqa: E402


class WorktreeFixture(Fixture):
    slug = "auth"

    def setUp(self):
        super().setUp()
        self.git_init()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, check=True,
        )

    def unit(self, name, *, owns, tier="session", depends_on=(), checks=None):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
                "depends_on": list(depends_on), "owns": list(owns), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": checks if checks is not None else [{"kind": "cmd", "run": "true"}],
            },
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(path)
        return path

    def plan_ready(self):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 0, out)
        # The plan lives in .ctx/, which is tracked; commit it so the integration
        # tree is clean and worktrees inherit the unit files.
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")

    def work_in(self, unit_name, relative, text, commit=True):
        path = wt.path_for(self.layout, unit_name) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if commit:
            tree = wt.path_for(self.layout, unit_name)
            self.git("add", "-A", cwd=tree)
            self.git("commit", "-qm", f"work {unit_name}", cwd=tree)
        return path


class TestWorktreeLifecycle(WorktreeFixture):
    def test_create_is_idempotent_and_names_the_branch(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()

        path, branch, created, error = wt.create(self.layout, self.slug, "01-a")
        self.assertEqual(error, "")
        self.assertTrue(created)
        self.assertTrue(path.is_dir())
        self.assertEqual(branch, "ctx/auth/01-a")
        self.assertTrue((path / ".ctx").exists(), "the worktree sees the plan files")

        _p, _b, created_again, error = wt.create(self.layout, self.slug, "01-a")
        self.assertEqual(error, "")
        self.assertFalse(created_again, "an existing worktree is reused, not duplicated")

    def test_listing_reports_only_ctx_worktrees(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        wt.create(self.layout, self.slug, "02-b")
        names = {name for name, _path, _branch in wt.listing(self.layout)}
        self.assertEqual(names, {"01-a", "02-b"})

    def test_remove_discards_the_tree_and_branch(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.assertEqual(wt.remove(self.layout, "01-a"), "")
        self.assertFalse(wt.path_for(self.layout, "01-a").exists())
        self.assertEqual(wt.listing(self.layout), [])

    def test_remove_protects_uncommitted_work_unless_forced(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n", commit=False)

        self.assertNotEqual(wt.remove(self.layout, "01-a"), "", "must refuse")
        self.assertTrue(wt.path_for(self.layout, "01-a").exists())
        self.assertEqual(wt.remove(self.layout, "01-a", force=True), "")

    def test_no_repo_is_reported_not_crashed_on(self):
        import shutil

        shutil.rmtree(self.root / ".git")
        self.assertIn("not a git repository", wt.check_repo(self.layout))


class TestMergeRefusals(WorktreeFixture):
    def test_a_failing_gate_stops_the_merge(self):
        self.unit("01-a", owns=["src/a.py"], checks=[{"kind": "cmd", "run": "exit 1"}])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("done-gate failed", " ".join(messages))
        self.assertTrue(wt.path_for(self.layout, "01-a").exists(), "worktree survives")
        doc = frontmatter.read(plan_mod.units_dir(self.layout, self.slug) / "01-a.md")
        self.assertEqual(doc.meta["status"], "pending", "not marked done")

    def test_writing_outside_owns_is_refused(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n", commit=False)
        self.work_in("01-a", "src/elsewhere.py", "y = 2\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        joined = " ".join(messages)
        self.assertIn("outside its `owns` scope", joined)
        self.assertIn("src/elsewhere.py", joined)
        self.assertIn("discard the worktree", joined, "names the recovery")

    def test_uncommitted_work_in_the_worktree_is_refused(self):
        """A merge would silently drop it, so say so instead."""
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n", commit=False)

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("uncommitted work in the worktree", " ".join(messages))

    def test_a_dirty_integration_tree_is_refused(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")
        (self.root / "stray.txt").write_text("unrelated\n", encoding="utf-8")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("uncommitted changes", " ".join(messages))

    def test_an_empty_branch_has_nothing_to_merge(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("changed nothing", " ".join(messages))

    def test_a_unit_with_no_checks_is_not_merged_blind(self):
        self.unit("01-a", owns=["src/a.py"], checks=[])
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        self.cli("plan", self.slug, "--no-spec")
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("refusing to merge blind", " ".join(messages))

    def test_an_undispatched_unit_has_no_branch(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("was this unit dispatched", " ".join(messages))


class TestMergeSuccess(WorktreeFixture):
    def test_a_passing_unit_merges_and_is_cleaned_up(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, messages)
        joined = " ".join(messages)
        self.assertIn("gate passed", joined)
        self.assertIn("merged ctx/auth/01-a", joined)

        self.assertTrue((self.root / "src" / "a.py").is_file(), "work landed")
        self.assertFalse(wt.path_for(self.layout, "01-a").exists(), "worktree removed")
        self.assertEqual(wt.listing(self.layout), [])
        doc = frontmatter.read(plan_mod.units_dir(self.layout, self.slug) / "01-a.md")
        self.assertEqual(doc.meta["status"], "done")

    def test_two_units_in_one_wave_both_merge(self):
        """The second merge is not a fast-forward — the original design was wrong."""
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"])
        self.plan_ready()
        for name in ("01-a", "02-b"):
            wt.create(self.layout, self.slug, name)
        self.work_in("01-a", "src/a.py", "a = 1\n")
        self.work_in("02-b", "src/b.py", "b = 2\n")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, messages)
        ok, messages = wt.merge(self.layout, self.config, self.slug, "02-b")
        self.assertTrue(ok, messages)

        self.assertTrue((self.root / "src" / "a.py").is_file())
        self.assertTrue((self.root / "src" / "b.py").is_file())
        self.assertEqual(wt.listing(self.layout), [])

    def test_the_ledgers_own_writes_do_not_block_a_merge(self):
        """Regression: every ctx command writes the journal and flips unit status.

        Before the fix the integration tree was never clean, so `ctx merge` was
        unreachable in any real project — the whole tier was dead on arrival.
        """
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        # Generate exactly the noise normal use produces.
        self.cli("journal", "edit", "src/a.py", "--note", "some work")
        self.cli("unit", "01-a", "--status", "running")
        changed, error = verify.changed_files(self.root)
        self.assertEqual(error, "")
        self.assertTrue(
            any(p.startswith(".ctx/") for p in changed),
            "the test must actually produce a dirty .ctx/ to be meaningful",
        )

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertTrue(ok, messages)

    def test_a_stray_write_is_still_caught_alongside_ledger_noise(self):
        """Excluding .ctx/ must not weaken the ownership check for real code."""
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n", commit=False)
        self.work_in("01-a", "src/stray.py", "y = 2\n")
        self.cli("journal", "edit", "src/a.py", "--note", "noise")

        ok, messages = wt.merge(self.layout, self.config, self.slug, "01-a")
        self.assertFalse(ok)
        self.assertIn("src/stray.py", " ".join(messages))

    def test_skip_gate_merges_without_running_checks(self):
        self.unit("01-a", owns=["src/a.py"], checks=[{"kind": "cmd", "run": "exit 1"}])
        self.plan_ready()
        wt.create(self.layout, self.slug, "01-a")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        ok, _messages = wt.merge(
            self.layout, self.config, self.slug, "01-a", skip_gate=True
        )
        self.assertTrue(ok, "an explicit override is allowed")


class TestDispatchWithWorktrees(WorktreeFixture):
    def test_start_prepares_worktrees_and_prints_commands(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"])
        self.plan_ready()

        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIn("own worktree", out)
        self.assertIn("ctx/auth/01-a", out)
        self.assertIn("cd ", out)
        self.assertIn("ctx unit 01-a", out)
        self.assertIn("ctx merge", out)
        for name in ("01-a", "02-b"):
            self.assertTrue(wt.path_for(self.layout, name).is_dir(), name)

    def test_no_worktree_flag_skips_preparation(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        self.cli("start", "--no-worktree")
        self.assertFalse(wt.path_for(self.layout, "01-a").exists())

    def test_plan_check_says_worktrees_will_be_created(self):
        self.unit("01-a", owns=["src/a.py"])
        self.cli("plan", self.slug, "--no-spec")
        _code, out = self.cli("plan-check", self.slug)
        self.assertIn("will each get a git worktree", out)

    def test_merge_command_advances_the_plan(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"], depends_on=["01-a"])
        self.plan_ready()
        self.cli("start", "--wave", "1")
        self.work_in("01-a", "src/a.py", "a = 1\n")

        code, out = self.cli("merge", "01-a")
        self.assertEqual(code, 0, out)
        self.assertIn("next: wave 2", out)
        self.assertIsNone(state.load(self.layout)["unit"])

    def test_merge_command_reports_refusal_without_merging(self):
        self.unit("01-a", owns=["src/a.py"], checks=[{"kind": "cmd", "run": "exit 1"}])
        self.plan_ready()
        self.cli("start")
        self.work_in("01-a", "src/a.py", "x = 1\n")

        code, out = self.cli("merge", "01-a")
        self.assertEqual(code, 1)
        self.assertIn("nothing was merged", out)

    def test_worktree_subcommands(self):
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        self.cli("start")

        _code, out = self.cli("worktree", "list")
        self.assertIn("01-a", out)
        code, out = self.cli("worktree", "remove", "01-a")
        self.assertEqual(code, 0, out)
        _code, out = self.cli("worktree", "list")
        self.assertIn("no ctx worktrees", out)


class TestInterfaceFreeze(Fixture):
    """The `symbol` verify kind: interface freeze enforced, not merely instructed."""

    def run_check(self, check):
        return verify.run(
            self.layout, self.config, [check], cwd=self.root, key="k"
        )

    def test_present_signature_passes(self):
        self.write("src/auth.py", "def refresh(token: Token) -> Token:\n    ...\n")
        _results, verdict = self.run_check(
            {"kind": "symbol", "path": "src/auth.py",
             "contains": ["def refresh(token: Token) -> Token:"]}
        )
        self.assertEqual(verdict, verify.PASS)

    def test_renamed_signature_fails_and_says_not_to_adjust_the_check(self):
        self.write("src/auth.py", "def renew(token):\n    ...\n")
        results, verdict = self.run_check(
            {"kind": "symbol", "path": "src/auth.py", "contains": ["def refresh("]}
        )
        self.assertEqual(verdict, verify.FAIL)
        self.assertIn("no longer provides: def refresh(", results[0].message)
        self.assertIn("planning decision", results[0].message)

    def test_deleted_file_fails(self):
        _results, verdict = self.run_check(
            {"kind": "symbol", "path": "src/gone.py", "contains": ["x"]}
        )
        self.assertEqual(verdict, verify.FAIL)

    def test_misconfigured_check_is_an_error_not_a_failure(self):
        results, verdict = self.run_check({"kind": "symbol", "path": "src/auth.py"})
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertEqual(verdict, verify.ERROR)

    def test_symbol_runs_before_cmd_but_after_exists(self):
        order = [
            c["kind"] for c in verify.ordered([
                {"kind": "cmd", "run": "true"}, {"kind": "symbol"},
                {"kind": "exists", "path": "."}, {"kind": "diff"},
            ])
        ]
        self.assertEqual(order, ["diff", "exists", "symbol", "cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
