"""Regressions for the Wave 1 audit findings (F01, F03-F07, F09, F15, F17, F21).

Each test pins the *behaviour that was wrong*, not the shape of the fix, so a
future refactor is free to move the code but not to reintroduce the defect.
"""

import datetime
import io
import json
import os
import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    cli, frontmatter, hooks, miniyaml, plan as plan_mod, verify, worktree as wt,
)
from support import OK, Fixture  # noqa: E402


# --------------------------------------------------------------------------- #
# F05 — the miniyaml round-trip
# --------------------------------------------------------------------------- #

class TestYamlRoundTrip(unittest.TestCase):
    """`_emit` escaped quotes that `_scalar` never unescaped, so every save/load
    cycle added a backslash — and unit frontmatter is rewritten on every status
    change, so the damage compounded rather than staying put."""

    CORPUS = (
        '-flag "x"', '"quoted"', 'say "hi" now', "it's fine", "plain text",
        'back\\slash', 'both "\\" kinds', "-leading-dash", "true", "null",
        "trailing space ", " leading space", "a: colon", "[bracketed]",
    )

    def test_quoted_scalars_survive_a_round_trip(self):
        for value in self.CORPUS:
            with self.subTest(value=value):
                text = miniyaml.dumps({"k": value})
                self.assertEqual(miniyaml.loads(text)["k"], value)

    def test_repeated_round_trips_are_stable(self):
        # The compounding case: a unit file rewritten three times.
        value = 'run --name "auth test"'
        for _ in range(3):
            value = miniyaml.loads(miniyaml.dumps({"k": value}))["k"]
        self.assertEqual(value, 'run --name "auth test"')

    def test_inline_lists_do_not_split_inside_quotes(self):
        parsed = miniyaml.loads('owns: [src/a.py, "b,c.py", src/d.py]')
        self.assertEqual(parsed["owns"], ["src/a.py", "b,c.py", "src/d.py"])


# --------------------------------------------------------------------------- #
# F03/F04 — detection that survives a real repository
# --------------------------------------------------------------------------- #

class TestProjectDetection(Fixture):
    def test_a_code_repo_that_documents_itself_is_still_code(self):
        """`docs/` used to be tested before `code` and won on first match, so a
        Python project with documentation came out as a docs project and got no
        runnable gate at all."""
        self.write("pyproject.toml", "[project]\nname='x'\n")
        self.write("docs/index.md", "# Guide\n")
        self.assertEqual(cli._detect_profile(self.root), "code")

    def test_notebooks_directory_does_not_outrank_a_manifest(self):
        self.write("package.json", '{"name":"x"}')
        self.write("notebooks/explore.ipynb", "{}")
        self.assertEqual(cli._detect_profile(self.root), "code")

    def test_a_real_docs_project_is_still_detected(self):
        self.write("mkdocs.yml", "site_name: x\n")
        self.write("docs/index.md", "# Guide\n")
        self.assertEqual(cli._detect_profile(self.root), "docs")

    def test_terraform_beats_an_incidental_docs_folder(self):
        self.write("main.tf", 'resource "null_resource" "a" {}\n')
        self.write("docs/runbook.md", "# Runbook\n")
        self.assertEqual(cli._detect_profile(self.root), "infra")

    def test_an_empty_directory_falls_back_to_code(self):
        self.assertEqual(cli._detect_profile(self.root), "code")

    def test_proposed_python_command_is_actually_on_this_machine(self):
        """`python -m pytest -q` was proposed unconditionally; `python` does not
        exist on Homebrew or python.org installs, so `_runnable` rejected it and
        `init` wrote an empty `verify:` list."""
        self.write("pyproject.toml", "[project]\nname='x'\n")
        candidates = cli._verify_candidates(self.root, "code")
        self.assertTrue(candidates, "a python project must propose something")
        interpreter = candidates[0].split()[0]
        self.assertIsNotNone(
            __import__("shutil").which(interpreter),
            f"proposed {interpreter!r}, which is not on PATH",
        )

    def test_runnable_rejects_a_module_that_cannot_be_imported(self):
        exe = cli._python_exe()
        self.assertFalse(cli._runnable(f"{exe} -m definitely_not_a_module_xyz"))
        self.assertTrue(cli._runnable(f"{exe} -m json.tool"))


# --------------------------------------------------------------------------- #
# F01 — the gate belongs to the session that owns the work
# --------------------------------------------------------------------------- #

class TestSubagentStopIsNotGated(Fixture):
    def failing_task(self):
        self.cli("task", "demo", "--objective", "do a thing")
        path = self.layout.task_file("demo")
        doc = frontmatter.read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "exit 3"}]
        doc.write(path)
        self.trust(doc.meta["verify"])

    def test_a_finishing_subagent_does_not_run_the_projects_test_suite(self):
        """Every subagent that stopped used to run the whole verify suite and
        could be blocked against criteria it had never touched — N concurrent
        test runs per dispatched wave, all judging one shared unit pointer."""
        self.failing_task()
        code, out = self.run_hook("SubagentStop")
        self.assertEqual(code, 0)
        self.assertEqual(out, "", "SubagentStop must contribute nothing")
        self.assertNotIn("SubagentStop", hooks.HANDLERS)

    def test_the_owning_session_is_still_gated(self):
        self.failing_task()
        _code, out = self.run_hook("Stop")
        self.assertEqual(json.loads(out)["decision"], "block")


# --------------------------------------------------------------------------- #
# F07/F15 — the gate's time budget and its output
# --------------------------------------------------------------------------- #

class TestGateBudgetAndOutput(Fixture):
    def test_the_budget_covers_the_whole_run_not_each_command(self):
        """Per command, three checks at 240s outlive the 300s Stop hook; the
        harness kills it, no decision is returned, and the gate silently stops
        applying. The second command must be refused, not merely slow."""
        config = dict(self.config, gate=dict(self.config["gate"], timeout_seconds=2))
        checks = [{"kind": "cmd", "run": self.py("import time; time.sleep(3)")},
                  {"kind": "cmd", "run": self.py("pass")}]
        self.trust(checks)
        results, verdict = verify.run(
            self.layout, config, checks, cwd=self.root, key="budget",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual([r.status for r in results], [verify.ERROR, verify.ERROR])
        self.assertIn("budget", results[1].message)
        self.assertEqual(verdict, verify.ERROR, "infrastructure, so never blocking")

    def test_failure_output_is_scrubbed_before_it_reaches_the_model(self):
        """`redact` guarded the journal and bundles but not the block reason,
        which inlines raw command output straight into the transcript."""
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0"
        checks = [{"kind": "cmd", "run": self.py(
            f"import sys; print(chr(39)+chr(39)); print('token={secret}'); sys.exit(1)")}]
        self.trust(checks)
        results, _verdict = verify.run(
            self.layout, self.config, checks, cwd=self.root, key="leak",
        )
        self.assertEqual(results[0].status, verify.FAIL)
        self.assertNotIn(secret, results[0].message)
        self.assertIn("<<redacted>>", results[0].message)

    def test_symbol_and_exists_checks_do_not_leak_file_handles(self):
        self.write("src/auth.py", "def refresh(token):\n    return token\n")
        self.write("docs/guide.md", "# Guide\nstable API\n")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            verify.run(
                self.layout, self.config,
                [
                    {"kind": "symbol", "path": "src/auth.py", "contains": ["def refresh"]},
                    {"kind": "exists", "path": "docs/guide.md", "matches": "stable API"},
                ],
                cwd=self.root, key="handles",
            )
        self.assertEqual(
            [w for w in caught if issubclass(w.category, ResourceWarning)], []
        )


# --------------------------------------------------------------------------- #
# F09 — a stale error log is history, not a live problem
# --------------------------------------------------------------------------- #

class TestHookErrorLog(Fixture):
    def write_error(self, when, event="SessionStart"):
        self.layout.runtime.mkdir(parents=True, exist_ok=True)
        with self.layout.errors.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {when.isoformat(timespec='seconds')} {event} ---\n")
            handle.write("Traceback (most recent call last):\n  boom\n")

    def test_doctor_passes_once_the_failures_are_old(self):
        """The log never rotated and doctor counted its existence as a problem,
        so one transient failure left the command exiting 1 forever."""
        self.write_error(datetime.datetime.now() - datetime.timedelta(days=30))
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("stale log", out)

    def test_doctor_still_fails_on_a_failure_from_today(self):
        self.write_error(datetime.datetime.now() - datetime.timedelta(minutes=5))
        code, out = self.cli("doctor")
        self.assertEqual(code, 1, out)
        self.assertIn("in the last", out)

    def test_clear_removes_the_log(self):
        self.write_error(datetime.datetime.now())
        code, out = self.cli("doctor", "--clear")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.layout.errors.exists())

    def test_entries_are_stamped_and_the_log_is_bounded(self):
        hooks._log_error(self.layout, "SessionStart", "boom")
        text = self.layout.errors.read_text(encoding="utf-8")
        self.assertIn("boom", text)
        self.assertTrue(
            cli._recent_hook_errors(text), "a fresh failure must read as recent"
        )
        self.layout.errors.write_text(
            "x" * (hooks.ERROR_LOG_MAX_BYTES + 1) + "\n", encoding="utf-8"
        )
        hooks._log_error(self.layout, "Stop", "again")
        self.assertLess(
            self.layout.errors.stat().st_size, hooks.ERROR_LOG_MAX_BYTES + 1024
        )


# --------------------------------------------------------------------------- #
# F17 — `done` is earned, not asserted
# --------------------------------------------------------------------------- #

class TestUnitDoneIsGated(Fixture):
    slug = "auth"

    def unit(self, name, checks):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
                "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": list(checks),
            },
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(path)
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return path

    def status_of(self, name):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        return frontmatter.read(path).meta["status"]

    def test_a_failing_unit_cannot_be_marked_done(self):
        """Only the worktree merge path verified before completing a unit. For
        `subagent` — the default tier — `done` was whatever the orchestrator
        typed after reading the unit's own report about itself."""
        self.unit("01-api", [{"kind": "cmd", "run": "exit 1"}])
        code, out = self.cli("unit", "01-api", "--status", "done")
        self.assertEqual(code, 1, out)
        self.assertIn("refusing", out)
        self.assertEqual(self.status_of("01-api"), "pending", "nothing was written")

    def test_a_passing_unit_is_marked_done(self):
        self.unit("01-api", [{"kind": "cmd", "run": OK}])
        code, out = self.cli("unit", "01-api", "--status", "done")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-api"), "done")

    def test_force_is_the_deliberate_override(self):
        self.unit("01-api", [{"kind": "cmd", "run": "exit 1"}])
        code, _out = self.cli("unit", "01-api", "--status", "done", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(self.status_of("01-api"), "done")

    def test_a_unit_with_no_checks_is_refused_rather_than_waved_through(self):
        self.unit("01-api", [])
        code, out = self.cli("unit", "01-api", "--status", "done")
        self.assertEqual(code, 1, out)
        self.assertIn("no usable verify checks", out)

    def test_other_statuses_are_not_gated(self):
        self.unit("01-api", [{"kind": "cmd", "run": "exit 1"}])
        code, _out = self.cli("unit", "01-api", "--status", "blocked")
        self.assertEqual(code, 0)
        self.assertEqual(self.status_of("01-api"), "blocked")


# --------------------------------------------------------------------------- #
# F06 — one worktree, one branch
# --------------------------------------------------------------------------- #

class TestWorktreeBranchScope(Fixture):
    def setUp(self):
        super().setUp()
        self.git_init()

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

    def branches(self):
        code, out = wt.git(["branch", "--format=%(refname:short)"], self.root)
        self.assertEqual(code, 0, out)
        return set(out.split())

    def test_removing_a_unit_leaves_the_same_name_in_another_plan_alone(self):
        """`remove` looped over every plan directory deleting `ctx/<plan>/<unit>`,
        so discarding `01-api` in one plan destroyed `01-api` in an unrelated one
        — and with it any commits that branch was the only reference to."""
        for plan_slug in ("billing", "auth"):
            self.unit_in(plan_slug, "01-api")
        wt.git(["branch", wt.branch_for("billing", "01-api")], self.root)

        path, branch, created, error = wt.create(self.layout, "auth", "01-api")
        self.assertEqual(error, "")
        self.assertTrue(created)
        self.assertIn(branch, self.branches())

        self.assertEqual(wt.remove(self.layout, "01-api"), "")
        remaining = self.branches()
        self.assertNotIn(wt.branch_for("auth", "01-api"), remaining, "own branch goes")
        self.assertIn(
            wt.branch_for("billing", "01-api"), remaining,
            "the other plan's branch must survive",
        )

    def test_branch_of_reads_the_answer_from_git(self):
        self.unit_in("auth", "01-api")
        _path, branch, _created, error = wt.create(self.layout, "auth", "01-api")
        self.assertEqual(error, "")
        self.assertEqual(wt.branch_of(self.layout, "01-api"), branch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
