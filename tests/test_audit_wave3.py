"""Regressions for the Wave 3 audit findings (F12, F14, F16, F18, F19, F20)."""

import ast
import datetime
import os
import json
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ctx import journal, telemetry, trust, verify  # noqa: E402
from support import OK, Fixture  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# F14 — a committed ctx.yaml is executable shell
# --------------------------------------------------------------------------- #

class TestCommandTrust(Fixture):
    CHECK = [{"kind": "cmd", "run": OK}]

    def test_an_unaccepted_command_is_reported_rather_than_run(self):
        """ctx.yaml is committed and its commands run with `shell=True` from a
        hook, which never sees a permission prompt. A cloned ledger must not
        execute on arrival."""
        marker = self.root / "executed.txt"
        checks = [{"kind": "cmd", "run": f"touch {marker}"}]
        results, verdict = verify.run(
            self.layout, self.config, checks, cwd=self.root, key="untrusted"
        )
        self.assertFalse(marker.exists(), "the command must not have run")
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertIn("not been accepted", results[0].message)
        self.assertEqual(verdict, verify.ERROR, "ungated, never broken")

    def test_accepting_lets_it_run(self):
        marker = self.root / "executed.txt"
        checks = [{"kind": "cmd", "run": f"touch {marker}"}]
        self.trust(checks)
        _results, verdict = verify.run(
            self.layout, self.config, checks, cwd=self.root, key="trusted"
        )
        self.assertTrue(marker.exists())
        self.assertEqual(verdict, verify.PASS)

    def test_changing_the_command_revokes_acceptance(self):
        self.trust([{"kind": "cmd", "run": "echo one"}])
        accepted = trust.load(self.layout)
        self.assertFalse(
            trust.is_accepted({"kind": "cmd", "run": "echo two"}, accepted)
        )

    def test_changing_cwd_or_env_is_a_new_command(self):
        base = {"kind": "cmd", "run": OK}
        self.trust([base])
        accepted = trust.load(self.layout)
        self.assertTrue(trust.is_accepted(base, accepted))
        self.assertFalse(trust.is_accepted(dict(base, cwd="apps/web"), accepted))
        self.assertFalse(trust.is_accepted(dict(base, env={"X": "1"}), accepted))

    def test_init_accepts_what_it_configured(self):
        """You watched init propose and print these, which is the review that
        `ctx trust` exists to force on a ledger from somewhere else."""
        self.write("Makefile", "test:\n\techo hi\n")
        code, _out = self.cli("init", "--force")
        self.assertEqual(code, 0)
        config = __import__("ctx.config", fromlist=["config"]).load(self.layout)
        accepted = trust.load(self.layout)
        for check in config.get("verify") or []:
            if check.get("kind") == "cmd":
                self.assertTrue(trust.is_accepted(check, accepted), check)

    def test_trust_lists_before_it_accepts(self):
        path = self.layout.task_file("demo")
        self.cli("task", "demo", "--objective", "x")
        doc = __import__("ctx.frontmatter", fromlist=["frontmatter"]).read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "echo house-command"}]
        doc.write(path)

        code, out = self.cli("trust")
        self.assertEqual(code, 1, "listing alone must not accept")
        self.assertIn("echo house-command", out)
        self.assertIn("not yet accepted", out)

        code, out = self.cli("trust", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("accepted 1", out)

        code, out = self.cli("trust")
        self.assertEqual(code, 0)
        self.assertIn("already accepted", out)

    def test_declared_covers_units_not_just_the_config(self):
        """A unit carries a snapshot of the block, so it can name a command
        ctx.yaml no longer does. Trust must cover what will actually run."""
        plan_mod = __import__("ctx.plan", fromlist=["plan"])
        frontmatter = __import__("ctx.frontmatter", fromlist=["frontmatter"])
        directory = plan_mod.units_dir(self.layout, "auth")
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": "auth", "tier": "subagent",
             "owns": ["src/a.py"], "status": "pending",
             "verify": [{"kind": "cmd", "run": "echo from-a-unit"}]},
            "## Objective\nx\n",
        ).write(directory / "01-a.md")
        found = [c.get("run") for c, _s in trust.declared(self.layout, self.config)]
        self.assertIn("echo from-a-unit", found)

    def test_doctor_reports_unaccepted_commands(self):
        path = self.layout.task_file("demo")
        self.cli("task", "demo", "--objective", "x")
        doc = __import__("ctx.frontmatter", fromlist=["frontmatter"]).read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "echo pending"}]
        doc.write(path)
        code, out = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("not accepted", out)


# --------------------------------------------------------------------------- #
# F16 — the journal does not grow forever
# --------------------------------------------------------------------------- #

class TestJournalRetention(Fixture):
    def day(self, when, note="did a thing"):
        journal.append(self.layout, self.config, "edit", f"src/{note}.py", note,
                       when=when)

    def test_old_days_fold_into_a_monthly_archive(self):
        for offset in (400, 399, 370):
            self.day(datetime.datetime.now() - datetime.timedelta(days=offset))
        self.day(datetime.datetime.now(), note="today")

        folded, archives = journal.prune(
            self.layout, self.config,
            before=datetime.date.today() - datetime.timedelta(days=30),
        )
        self.assertEqual(len(folded), 3)
        self.assertTrue(archives)
        for path in folded:
            self.assertFalse(path.exists(), "folded days are removed")
        text = "\n".join(p.read_text(encoding="utf-8") for p in archives)
        self.assertIn("did a thing", text, "history is kept, not dropped")

    def test_recent_days_are_untouched(self):
        self.day(datetime.datetime.now())
        folded, _archives = journal.prune(
            self.layout, self.config,
            before=datetime.date.today() - datetime.timedelta(days=30),
        )
        self.assertEqual(folded, [])

    def test_keep_days_drives_the_default(self):
        self.day(datetime.datetime.now() - datetime.timedelta(days=90))
        config = dict(self.config,
                      journal=dict(self.config["journal"], keep_days=30))
        folded, _archives = journal.prune(self.layout, config)
        self.assertEqual(len(folded), 1)

    def test_keep_days_zero_means_keep_everything(self):
        self.day(datetime.datetime.now() - datetime.timedelta(days=900))
        folded, _archives = journal.prune(self.layout, self.config)
        self.assertEqual(folded, [], "0 must not silently delete history")

    def test_the_cli_reports_what_it_did(self):
        self.day(datetime.datetime.now() - datetime.timedelta(days=90))
        code, out = self.cli("prune", "--before",
                             (datetime.date.today()
                              - datetime.timedelta(days=30)).isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("archived 1", out)

    def test_a_bad_date_is_refused(self):
        code, out = self.cli("prune", "--before", "last-tuesday")
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", out)


# --------------------------------------------------------------------------- #
# F19 — telemetry has a switch
# --------------------------------------------------------------------------- #

class TestTelemetrySwitch(Fixture):
    def test_disabled_writes_nothing(self):
        config_path = self.layout.config
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "telemetry:\n  enabled: true", "telemetry:\n  enabled: false"),
            encoding="utf-8",
        )
        target = telemetry.path_for(self.layout)
        if target.exists():
            target.unlink()
        self.run_hook("SessionStart")
        self.assertFalse(target.exists(), "no records once it is switched off")

    def test_enabled_by_default(self):
        self.run_hook("SessionStart")
        self.assertTrue(telemetry.path_for(self.layout).is_file())

    def test_the_switch_is_visible_in_the_generated_config(self):
        self.assertIn("telemetry", self.layout.config.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# F20 — drift between ctx.yaml and a work file
# --------------------------------------------------------------------------- #

class TestVerifyDrift(Fixture):
    def test_doctor_names_work_files_whose_checks_have_drifted(self):
        """The snapshot is defensible; the silence was not. Fixing a broken
        command in ctx.yaml left every existing task carrying the old one."""
        path = self.layout.task_file("demo")
        self.cli("task", "demo", "--objective", "x")
        doc = __import__("ctx.frontmatter", fromlist=["frontmatter"]).read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "stale-command"}]
        doc.write(path)
        self.trust(doc.meta["verify"])

        _code, out = self.cli("doctor")
        self.assertIn("verify drift", out)
        self.assertIn("demo.md", out)

    def test_a_task_matching_the_default_is_not_reported(self):
        self.cli("task", "demo", "--objective", "x")
        _code, out = self.cli("doctor")
        self.assertNotIn("verify drift", out)


# --------------------------------------------------------------------------- #
# F12 / F18 — the shipped surface
# --------------------------------------------------------------------------- #

class TestShippedSurface(unittest.TestCase):
    def test_a_windows_entry_point_ships(self):
        """`bin/ctx` is bash and every command file invokes it, so Windows had
        no path through that was not a workaround."""
        self.assertTrue((ROOT / "bin/ctx.cmd").is_file())
        self.assertTrue((ROOT / "bin/ctx.py").is_file())

    def test_every_entry_point_reports_the_same_version(self):
        # `bin/ctx` is a bash script; on Windows the shipped wrapper is the .cmd,
        # which is the whole point of having both.
        wrapper = ROOT / ("bin/ctx.cmd" if os.name == "nt" else "bin/ctx")
        versions = set()
        for command in (
            [str(wrapper), "--version"],
            [sys.executable, str(ROOT / "bin/ctx.py"), "--version"],
            [sys.executable, "-m", "ctx", "--version"],
        ):
            done = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                                  text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            versions.add(done.stdout.strip())
        self.assertEqual(len(versions), 1, versions)

    def test_the_manifest_version_matches_the_package(self):
        manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        source = (ROOT / "ctx/__init__.py").read_text()
        import re
        declared = re.search(r'__version__ = "([^"]+)"', source).group(1)
        self.assertEqual(manifest["version"], declared)

    def test_ci_runs_the_suite_on_three_platforms(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        for platform in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(platform, text)
        self.assertIn("unittest discover", text)

    def test_the_documented_python_floor_is_the_one_that_parses(self):
        """The README claims 3.8+. Assert the grammar actually holds rather than
        trusting it — CI pins a 3.8 job for the runtime half."""
        for path in sorted(ROOT.glob("**/*.py")):
            if "__pycache__" in str(path):
                continue
            with self.subTest(path=path.name):
                ast.parse(path.read_text(), filename=str(path),
                          feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
