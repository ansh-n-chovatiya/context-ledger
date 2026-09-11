"""The done-gate warns when it leaves untracked files behind.

The gate runs *before* the unit's work is committed, so a check that enumerates
through `git ls-files` cannot see files the unit has just written — they are
still untracked. It passed vacuously in a real session on 2026-09-11: a unit
wrote `docs/*.md` and a link checker in the same change, the checker saw no new
pages, and three broken anchors shipped on a tree `ctx unit --status done`,
`ctx doctor` and `ctx ci` had all just called green.

The gate cannot know a given check is scoped that way. It can know that new
files exist inside the unit's `owns`, and say so — advisory only, never a
refusal, because producing new files is what most units do.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod  # noqa: E402
from support import Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
DIFF = [{"kind": "diff"}]


class UntrackedWarningFixture(Fixture):
    """A real git repository, one unit, driven the way a session drives it."""

    slug = "routes"

    def setUp(self):
        super().setUp()
        self.git_init()

    def unit(self, name="01-api", *, owns=None, checks=None):
        checks = list(DIFF if checks is None else checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [],
                "owns": list(owns or [f"src/{name}.py"]), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "wave": 1, "verify": checks,
            },
            CRITERIA,
        ).write(directory / f"{name}.md")
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return directory / f"{name}.md"

    def dispatch(self):
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        return out

    def done(self, name="01-api"):
        return self.cli("unit", name, "--status", "done")

    def find(self, name="01-api"):
        return plan_mod.find_unit(self.layout, self.slug, name)

    # -- git ---------------------------------------------------------------- #

    def git(self, *args):
        env = dict(
            self._env, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
            GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com",
        )
        completed = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, env=env,
        )
        self.assertEqual(completed.returncode, 0,
                          f"git {' '.join(args)}: {completed.stderr}")
        return completed.stdout.strip()

    def commit(self, message="work"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)


class TestGateWarnsOnUntracked(UntrackedWarningFixture):
    def test_an_untracked_file_in_owns_produces_the_advisory_and_still_passes(self):
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        # In scope but never committed — still sitting in `git status` as `??`.
        self.assertIn(
            "??", self.git("status", "--porcelain", "--untracked-files=all"),
        )
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.find("01-api").status, "done")
        self.assertIn("untracked", out)
        self.assertIn("git ls-files", out)
        self.assertIn("1", out, "the count is named")

    def test_a_unit_with_no_new_files_stays_silent(self):
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        self.commit("the unit's work")
        self.assertEqual(
            self.git("status", "--porcelain", "--untracked-files=all"), "",
            "the working tree really is clean — otherwise this proves nothing",
        )
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.find("01-api").status, "done")
        self.assertNotIn("untracked", out)

    def test_the_verdict_and_exit_code_are_identical_either_way(self):
        """The warning is advisory: it must change nothing else the gate reports."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        code_untracked, out_untracked = self.done("01-api")
        self.assertIn("untracked", out_untracked)
        # Clear the leftover untracked file out of the way before the second
        # unit's own dispatch point is recorded, so its scope check compares
        # against a clean tree rather than this test's own residue.
        self.commit("cleanup")

        self.unit("02-api", owns=["src/02-api.py"])
        self.dispatch()
        self.write("src/02-api.py", "def api():\n    return 1\n")
        self.commit("the second unit's work")
        code_tracked, out_tracked = self.done("02-api")
        self.assertNotIn("untracked", out_tracked)

        self.assertEqual(code_untracked, code_tracked)
        self.assertEqual(code_untracked, 0)
        self.assertEqual(self.find("01-api").status, "done")
        self.assertEqual(self.find("02-api").status, "done")

    def test_untracked_files_outside_owns_do_not_trigger_the_advisory(self):
        """An untracked file outside this unit's `owns` already fails the diff
        check on its own terms — the gate refuses before the advisory is ever
        reached, so it must not appear on a verdict that is already a refusal."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        self.commit("the unit's work")
        self.write("src/elsewhere.py", "nothing = True\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("changed outside owned scope", out)
        self.assertNotIn("untracked", out)

    def test_ledger_churn_is_not_counted_as_untracked_work(self):
        """`.ctx/` bookkeeping is excluded by `is_ledger`, reused rather than
        reimplemented — the ledger changes on every command and belongs to no
        unit's `owns`."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        self.commit("the unit's work")
        # Untracked ledger bookkeeping alongside otherwise-clean, committed work.
        self.write(".ctx/runtime/scratch.txt", "not part of any owns\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertNotIn("untracked", out)


if __name__ == "__main__":
    unittest.main()
