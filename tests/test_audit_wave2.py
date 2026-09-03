"""Regressions for the Wave 2 audit findings (F02, F08, F10, F11, F13).

As in Wave 1, each test pins the behaviour that was wrong rather than the shape
of the fix.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    cli, frontmatter, hooks, plan as plan_mod, state, verify, work,
)
from support import Fixture  # noqa: E402


# --------------------------------------------------------------------------- #
# F13 — concurrent read-modify-write
# --------------------------------------------------------------------------- #

class TestStateLocking(Fixture):
    def test_concurrent_bumps_are_not_lost(self):
        """`os.replace` made each write atomic, but load-then-save is not: two
        processes that both read before either wrote left one update gone."""
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from ctx import paths, state\n"
            "layout = paths.Layout(%r)\n"
            "for _ in range(20): state.bump_attempts(layout, 'demo')\n"
        ) % (str(Path(__file__).resolve().parent.parent), str(self.layout.root))
        workers = [
            subprocess.Popen([sys.executable, "-c", script],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(4)
        ]
        for worker in workers:
            _out, err = worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0, err.decode())
        self.assertEqual(state.attempts(self.layout, "demo"), 80)

    def test_a_stale_lock_is_reclaimed_rather_than_waited_out(self):
        lock = self.layout.runtime / "state.lock"
        self.layout.runtime.mkdir(parents=True, exist_ok=True)
        lock.write_text("")
        os.utime(lock, (0, 0))  # far older than LOCK_STALE_SECONDS
        state.update(self.layout, level="1")
        self.assertEqual(state.load(self.layout)["level"], "1")

    def test_the_lock_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with state.locked(self.layout):
                raise ValueError("boom")
        self.assertFalse((self.layout.runtime / "state.lock").exists())


# --------------------------------------------------------------------------- #
# F02 — a claim that belongs to one process
# --------------------------------------------------------------------------- #

class TestPerProcessClaim(Fixture):
    slug = "auth"

    def setUp(self):
        super().setUp()
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("01-keys", "02-clock"):
            frontmatter.Document(
                {
                    "ctx_schema": 1, "unit": name, "plan": self.slug,
                    "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                    "reads": [], "forbid": [], "budget_tokens": 1000,
                    "status": "pending", "verify": [{"kind": "cmd", "run": "true"}],
                },
                f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
            ).write(directory / f"{name}.md")
        self.cli("plan", self.slug, "--no-spec")
        self.addCleanup(os.environ.pop, "CTX_UNIT", None)
        self.addCleanup(os.environ.pop, "CTX_PLAN", None)

    def test_the_environment_claim_wins_over_the_shared_pointer(self):
        """`state.json` is machine-local, so its `unit` pointer describes the
        machine and not the agent — two sessions in one tree clobbered it."""
        self.cli("unit", "01-keys")
        self.assertEqual(work.active(self.layout).key, "01-keys")

        os.environ["CTX_UNIT"] = "02-clock"
        os.environ["CTX_PLAN"] = self.slug
        self.assertEqual(work.active(self.layout).key, "02-clock")

    def test_an_unknown_claim_falls_back_rather_than_failing(self):
        self.cli("unit", "01-keys")
        os.environ["CTX_UNIT"] = "99-nonexistent"
        os.environ["CTX_PLAN"] = self.slug
        self.assertEqual(work.active(self.layout).key, "01-keys")

    def test_attempt_keys_are_namespaced_by_plan(self):
        """Unit names are unique only inside their plan, so two plans with an
        `01-api` shared one counter and one plan's failures escalated the other's."""
        self.cli("unit", "01-keys")
        item = work.active(self.layout)
        self.assertEqual(item.attempt_key, f"{self.slug}/01-keys")

    def test_a_task_keeps_its_bare_key(self):
        self.cli("drop")
        self.cli("task", "demo", "--objective", "x")
        self.assertEqual(work.active(self.layout).attempt_key, "demo")


# --------------------------------------------------------------------------- #
# F08 — the shell is not a bypass
# --------------------------------------------------------------------------- #

class TestBashIsSeen(unittest.TestCase):
    def targets(self, command):
        return hooks._bash_targets(command)

    def test_writing_commands_are_detected(self):
        self.assertIn("src/auth.py", self.targets("sed -i '' 's/a/b/' src/auth.py"))
        self.assertIn("src/new.py", self.targets("cat > src/new.py <<'EOF'\nx\nEOF"))
        self.assertIn("notes/log.txt", self.targets("echo hi >> notes/log.txt"))
        self.assertIn("build/cache", self.targets("rm -rf build/cache"))
        self.assertIn("src/auth.py", self.targets("git checkout main -- src/auth.py"))

    def test_a_sed_script_is_not_mistaken_for_a_path(self):
        self.assertNotIn("s/a/b/", self.targets("sed -i '' 's/a/b/' src/auth.py"))

    def test_read_only_commands_are_ignored(self):
        for command in ("ls -la", "grep -rn foo src/", "npm test",
                        "git status --porcelain", "cat src/auth.py",
                        "python3 -c 'print(1)'"):
            with self.subTest(command=command):
                self.assertEqual(self.targets(command), [])

    def test_the_hook_matchers_include_bash(self):
        config = json.loads(Path(__file__).resolve().parent.parent
                            .joinpath("hooks/hooks.json").read_text())
        for event in ("PreToolUse", "PostToolUse"):
            for group in config["hooks"][event]:
                self.assertIn("Bash", group["matcher"], event)


class TestBashScopeEnforcement(Fixture):
    slug = "auth"

    def setUp(self):
        super().setUp()
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": "01-keys", "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": ["src/keys.py"],
                "reads": [], "forbid": ["src/clock.py"], "budget_tokens": 1000,
                "status": "pending", "verify": [{"kind": "cmd", "run": "true"}],
            },
            "## Objective\nDo it.\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / "01-keys.md")
        self.cli("plan", self.slug, "--no-spec")
        self.cli("unit", "01-keys")

    def bash(self, command):
        return self.run_hook(
            "PreToolUse", tool_name="Bash", tool_input={"command": command}
        )

    def test_a_shell_write_outside_owns_is_nudged(self):
        """One `sed -i` used to walk straight past `owns` isolation, which is the
        property that makes a parallel wave safe rather than hopeful."""
        self.bash("sed -i '' 's/a/b/' src/other.py")
        _code, out = self.run_hook("UserPromptSubmit")
        self.assertIn("outside the `owns` scope", out)
        self.assertIn("src/other.py", out)

    def test_a_shell_write_to_a_forbidden_path_is_nudged(self):
        self.bash("echo x > src/clock.py")
        _code, out = self.run_hook("UserPromptSubmit")
        self.assertIn("forbid", out)

    def test_a_shell_write_inside_owns_is_silent(self):
        self.bash("sed -i '' 's/a/b/' src/keys.py")
        _code, out = self.run_hook("UserPromptSubmit")
        self.assertEqual(out, "")

    def test_a_read_only_command_is_silent(self):
        self.bash("grep -rn refresh src/")
        _code, out = self.run_hook("UserPromptSubmit")
        self.assertEqual(out, "")

    def test_shell_writes_reach_the_journal(self):
        self.run_hook(
            "PostToolUse", tool_name="Bash",
            tool_input={"command": "sed -i '' 's/a/b/' src/keys.py"},
        )
        entries, _earlier = __import__("ctx.journal", fromlist=["journal"]).tail(
            self.layout, 10
        )
        self.assertTrue(
            any("src/keys.py" in e for e in entries),
            f"shell edits must be journalled: {entries}",
        )


# --------------------------------------------------------------------------- #
# F10 — a check can say where it runs
# --------------------------------------------------------------------------- #

class TestCheckWorkingDirectory(Fixture):
    def test_a_command_runs_in_its_declared_subdirectory(self):
        """Every command ran at the ledger's parent, so a monorepo could not say
        "run `npm test` in apps/web" — which ruled out most large repositories."""
        self.write("apps/web/marker.txt", "here\n")
        results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": "cmd", "run": "test -f marker.txt", "cwd": "apps/web"}],
            cwd=self.root, key="mono",
        )
        self.assertEqual(verdict, verify.PASS, results[0].message)

    def test_the_same_command_fails_from_the_root(self):
        self.write("apps/web/marker.txt", "here\n")
        _results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": "cmd", "run": "test -f marker.txt"}],
            cwd=self.root, key="mono",
        )
        self.assertEqual(verdict, verify.FAIL)

    def test_a_missing_cwd_is_a_configuration_error_not_a_work_failure(self):
        results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": "cmd", "run": "true", "cwd": "apps/nope"}],
            cwd=self.root, key="mono",
        )
        self.assertEqual(verdict, verify.ERROR, "never blocking")
        self.assertIn("not a directory", results[0].message)

    def test_env_is_layered_over_the_session(self):
        results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": "cmd", "run": '[ "$CTX_PROBE" = "yes" ]',
              "env": {"CTX_PROBE": "yes"}}],
            cwd=self.root, key="env",
        )
        self.assertEqual(verdict, verify.PASS, results[0].message)

    def test_exists_resolves_against_the_declared_cwd(self):
        self.write("apps/web/marker.txt", "here\n")
        _results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": "exists", "path": "marker.txt", "cwd": "apps/web"}],
            cwd=self.root, key="mono",
        )
        self.assertEqual(verdict, verify.PASS)


# --------------------------------------------------------------------------- #
# F11 — the ecosystem table
# --------------------------------------------------------------------------- #

class TestEcosystemCoverage(Fixture):
    def candidates(self, files, profile="code"):
        for name, body in files.items():
            self.write(name, body)
        return cli._verify_candidates(self.root, profile)

    def test_maven_and_gradle_are_covered(self):
        self.assertIn("mvn -q -B test-compile", self.candidates({"pom.xml": "<x/>"}))

    def test_dotnet_is_covered(self):
        self.assertIn("dotnet build --nologo", self.candidates({"App.csproj": "<x/>"}))

    def test_ruby_php_elixir_and_swift_are_covered(self):
        for marker, command in (
            ("Gemfile", "bundle exec rake test"),
            ("composer.json", "composer run-script test"),
            ("mix.exs", "mix compile --warnings-as-errors"),
            ("Package.swift", "swift build"),
        ):
            with self.subTest(marker=marker):
                self.assertIn(command, self.candidates({marker: "x"}))

    def test_a_makefile_target_is_proposed(self):
        self.assertIn("make test", self.candidates({"Makefile": "test:\n\techo hi\n"}))

    def test_a_pnpm_workspace_uses_pnpm_not_npm(self):
        found = self.candidates({
            "package.json": '{"scripts":{"test":"x","typecheck":"y"}}',
            "pnpm-workspace.yaml": "packages:\n  - apps/*\n",
        })
        self.assertIn("pnpm test", found)
        self.assertNotIn("npm test", found)

    def test_house_commands_from_config_are_included(self):
        found = cli._verify_candidates(self.root, "code", ["bazel test //..."])
        self.assertIn("bazel test //...", found)

    def test_candidates_are_deduplicated(self):
        found = self.candidates({"App.sln": "x", "App.csproj": "<x/>"})
        self.assertEqual(found.count("dotnet build --nologo"), 1)

    def test_detection_no_longer_depends_on_the_profile(self):
        """A terraform repo with a Makefile still gets `make`; candidates come
        from what is on disk, not from the profile label."""
        found = self.candidates({"main.tf": "x", "Makefile": "test:\n\techo hi\n"},
                                profile="infra")
        self.assertIn("terraform validate", found)
        self.assertIn("make test", found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
