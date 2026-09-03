"""Hardening tests — phase 7.

Migration, CI mode, budget accounting and telemetry. The properties that make
these safe to point at someone's repository:

* `migrate --check` never writes, and applying twice is a no-op.
* Migration refuses a ledger stamped newer than the plugin, rather than
  downgrading it.
* `ctx.yaml` keeps its comments — migration edits the line, not the file.
* Telemetry never breaks a hook, and never grows without bound.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    briefing, bundle, config as config_mod, frontmatter, migrate as migrate_mod,
    plan as plan_mod, state, telemetry,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class MigrateFixture(Fixture):
    def unstamp(self, path, key="ctx_schema"):
        """Make a file look like it predates schema stamping."""
        doc = frontmatter.read(path)
        doc.meta.pop(key, None)
        doc.write(path)
        return path

    def make_unit(self, plan_slug, name, stamped=True):
        plan_mod.units_dir(self.layout, plan_slug).mkdir(parents=True, exist_ok=True)
        meta = {"unit": name, "plan": plan_slug, "tier": "subagent",
                "owns": [f"src/{name}.py"], "verify": list(CHECK)}
        if stamped:
            meta["ctx_schema"] = 1
        path = plan_mod.units_dir(self.layout, plan_slug) / f"{name}.md"
        frontmatter.Document(
            meta, "## Objective\nx\n\n## Acceptance criteria\n1. y\n"
        ).write(path)
        return path


class TestMigrate(MigrateFixture):
    def test_a_fresh_ledger_needs_no_migration(self):
        behind, ahead = migrate_mod.pending(self.layout)
        self.assertEqual((behind, ahead), ([], []))
        code, out = self.cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("nothing to migrate", out)

    def test_unstamped_files_are_discovered_and_stamped(self):
        self.cli("task", "old-task")
        self.cli("spec", "old-spec")
        self.make_unit("old-plan", "01-a", stamped=False)
        bundle.save(self.layout, "old-bundle", "## Situation\nx\n", config=self.config)

        self.unstamp(self.layout.task_file("old-task"))
        self.unstamp(bundle.resolve(self.layout, "old-bundle"), key="ctx_bundle")

        behind, ahead = migrate_mod.pending(self.layout)
        kinds = sorted({item.kind for item in behind})
        self.assertEqual(ahead, [])
        self.assertEqual(kinds, ["bundle", "task", "unit"])

        code, out = self.cli("migrate")
        self.assertEqual(code, 0, out)
        self.assertIn("migrated", out)
        self.assertEqual(migrate_mod.pending(self.layout), ([], []))

        doc = frontmatter.read(self.layout.task_file("old-task"))
        self.assertEqual(doc.meta["ctx_schema"], 1)
        doc = frontmatter.read(bundle.resolve(self.layout, "old-bundle"))
        self.assertEqual(doc.meta["ctx_bundle"], 1)

    def test_check_reports_without_writing_and_exits_nonzero(self):
        self.cli("task", "old-task")
        path = self.unstamp(self.layout.task_file("old-task"))
        before = path.read_text(encoding="utf-8")

        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 1, "CI needs a non-zero exit")
        self.assertIn("would migrate", out)
        self.assertIn("run `ctx migrate` to apply", out)
        self.assertEqual(path.read_text(encoding="utf-8"), before, "wrote nothing")

    def test_migration_is_idempotent(self):
        self.cli("task", "old-task")
        self.unstamp(self.layout.task_file("old-task"))
        self.cli("migrate")
        after_first = self.layout.task_file("old-task").read_text(encoding="utf-8")

        code, out = self.cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("nothing to migrate", out)
        self.assertEqual(
            self.layout.task_file("old-task").read_text(encoding="utf-8"), after_first
        )

    def test_a_newer_ledger_is_refused_not_downgraded(self):
        path = self.layout.task_file("future")
        frontmatter.Document({"ctx_schema": 99, "task": "future"}, "## Objective\nx\n").write(path)

        behind, ahead = migrate_mod.pending(self.layout)
        self.assertEqual(len(ahead), 1)
        code, out = self.cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("newer than this plugin", out)
        doc = frontmatter.read(path)
        self.assertEqual(doc.meta["ctx_schema"], 99, "left alone")

    def test_config_migration_preserves_comments_and_values(self):
        text = self.layout.config.read_text(encoding="utf-8")
        self.assertIn("#", text, "the generated config has comments to preserve")
        stripped = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("schema:")
        ) + "\n"
        self.layout.config.write_text(stripped, encoding="utf-8")

        behind, _ahead = migrate_mod.pending(self.layout)
        self.assertTrue(any(i.kind == "config" for i in behind))

        self.cli("migrate")
        after = self.layout.config.read_text(encoding="utf-8")
        self.assertIn("schema: 1", after)
        self.assertIn("# Context Ledger configuration.", after, "comments survived")
        self.assertIn("briefing_chars", after, "values survived")
        self.assertEqual(config_mod.load(self.layout)["schema"], 1)

    def test_plan_graph_is_migrated(self):
        self.make_unit("p", "01-a")
        self.cli("plan", "p", "--no-spec")
        self.cli("plan-check", "p")
        graph = plan_mod.graph_path(self.layout, "p")
        data = json.loads(graph.read_text(encoding="utf-8"))
        del data["ctx_schema"]
        graph.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        self.cli("migrate")
        data = json.loads(graph.read_text(encoding="utf-8"))
        self.assertEqual(data["ctx_schema"], 1)
        self.assertEqual(data["waves"], [["01-a"]], "other content untouched")

    def test_a_hand_written_unit_gains_its_identity(self):
        """A unit file with no frontmatter must still become discoverable."""
        directory = plan_mod.units_dir(self.layout, "hand")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "01-manual.md"
        path.write_text("## Objective\nwritten by hand\n", encoding="utf-8")

        self.cli("migrate")
        doc = frontmatter.read(path)
        self.assertEqual(doc.meta["ctx_schema"], 1)
        self.assertEqual(doc.meta["unit"], "01-manual")
        self.assertIn("written by hand", doc.body)


class TestCiMode(MigrateFixture):
    def test_a_fresh_ledger_passes(self):
        code, out = self.cli("ci")
        self.assertEqual(code, 0, out)
        self.assertIn("all checks passed", out)

    def test_a_stale_schema_fails_ci(self):
        self.cli("task", "old")
        self.unstamp(self.layout.task_file("old"))
        code, out = self.cli("ci")
        self.assertEqual(code, 1)
        self.assertIn("schema current", out)
        self.assertIn("ctx migrate", out)

    def test_open_blocking_questions_fail_ci(self):
        self.cli("spec", "auth")
        self.cli("question", "auth", "Is the legacy path retired?")
        code, out = self.cli("ci")
        self.assertEqual(code, 1)
        self.assertIn("blocking questions", out)

    def test_a_colliding_plan_fails_ci(self):
        for name in ("01-a", "02-b"):
            path = self.make_unit("p", name)
            doc = frontmatter.read(path)
            doc.meta["owns"] = ["src/same.py"]
            doc.write(path)
        self.cli("plan", "p", "--no-spec")
        code, out = self.cli("ci", "--plan", "p")
        self.assertEqual(code, 1)
        self.assertIn("collision-free", out)
        self.assertIn("both own", out)

    def test_a_truncated_briefing_fails_ci(self):
        """The cap can never be exceeded — `_fit` clamps — so truncation is the
        signal. It means state a session needed was dropped to fit."""
        self.cli("task", "fat")
        path = self.layout.task_file("fat")
        doc = frontmatter.read(path)
        doc.body = (
            "## Objective\nDo the thing.\n\n## Acceptance criteria\n"
            + "".join(f"{i}. criterion {'x' * 100}\n" for i in range(1, 20))
        )
        doc.write(path)

        measured = briefing.measure(
            self.layout, self.config, dict(state.load(self.layout), level="1")
        )
        self.assertTrue(measured["truncated"])
        self.assertLessEqual(measured["chars"], measured["cap"], "still clamped")

        code, out = self.cli("ci")
        self.assertEqual(code, 1)
        self.assertIn("fits without truncation", out)
        self.assertIn("rather than raising the cap", out)

    def test_a_normal_briefing_is_not_flagged(self):
        self.cli("task", "lean")
        code, out = self.cli("ci")
        self.assertEqual(code, 0, out)


class TestVerifyPlan(MigrateFixture):
    """The §12 claim that `verify --plan` runs headless — now actually true."""

    def build(self, *specs):
        for name, checks in specs:
            path = self.make_unit("p", name)
            doc = frontmatter.read(path)
            doc.meta["verify"] = checks
            doc.write(path)
            self.trust(checks)
        self.cli("plan", "p", "--no-spec")
        self.cli("plan-check", "p")

    def test_all_units_passing_exits_zero(self):
        self.build(("01-a", CHECK), ("02-b", CHECK))
        code, out = self.cli("verify", "--plan", "p")
        self.assertEqual(code, 0, out)
        self.assertIn("2 passed", out)

    def test_one_failing_unit_fails_the_run(self):
        self.build(("01-a", CHECK), ("02-b", [{"kind": "cmd", "run": "exit 1"}]))
        code, out = self.cli("verify", "--plan", "p")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("02-b", out)
        self.assertIn("1 failed", out)

    def test_judged_checks_are_reported_pending_not_judged(self):
        self.build(("01-a", [{"kind": "rubric"}]))
        code, out = self.cli("verify", "--plan", "p")
        self.assertEqual(code, 0, "pending is not a failure")
        self.assertIn("awaiting sign-off", out)

    def test_a_unit_without_checks_fails_rather_than_passing_silently(self):
        """Caught upstream by plan validation, before any check is run."""
        self.build(("01-a", []))
        code, out = self.cli("verify", "--plan", "p")
        self.assertEqual(code, 1)
        self.assertIn("no usable `verify` checks", out)
        self.assertIn("not verifying units", out)

    def test_a_broken_plan_is_reported_before_running_anything(self):
        for name in ("01-a", "02-b"):
            path = self.make_unit("p", name)
            doc = frontmatter.read(path)
            doc.meta["owns"] = ["src/same.py"]
            doc.write(path)
        code, out = self.cli("verify", "--plan", "p")
        self.assertEqual(code, 1)
        self.assertIn("not verifying units", out)


class TestTelemetry(Fixture):
    def test_hooks_record_duration_and_injected_size(self):
        self.run_hook("SessionStart")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertIn("SessionStart", rows)
        self.assertEqual(rows["SessionStart"]["count"], 1)
        self.assertGreater(rows["SessionStart"]["median_chars"], 0,
                           "records what the session actually paid")

    def test_silent_hooks_record_zero_chars(self):
        self.run_hook("UserPromptSubmit")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["UserPromptSubmit"]["median_chars"], 0)

    def test_a_failed_hook_is_recorded_not_lost(self):
        original = briefing.build
        briefing.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.run_hook("SessionStart")
        finally:
            briefing.build = original
        entries = telemetry.read(self.layout)
        self.assertTrue(any(e.get("failed") for e in entries))

    def test_the_file_does_not_grow_without_bound(self):
        for index in range(4000):
            telemetry.record(self.layout, "SessionStart", 1.0, chars=index)
        size = telemetry.path_for(self.layout).stat().st_size
        self.assertLess(size, telemetry.MAX_BYTES * 2)

    def test_recording_never_raises(self):
        telemetry.record(self.layout, "X", "not-a-number")
        telemetry.record(self.layout, "X", 1.0, weird=object())
        self.assertTrue(True, "no exception escaped")

    def test_corrupt_lines_are_skipped(self):
        telemetry.record(self.layout, "SessionStart", 5.0, chars=10)
        with telemetry.path_for(self.layout).open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        rows = telemetry.summarise(self.layout)
        self.assertEqual(rows[0]["count"], 1)

    def test_telemetry_command_reports_and_survives_empty(self):
        code, out = self.cli("telemetry")
        self.assertEqual(code, 0)
        self.assertIn("no telemetry recorded", out)

        self.run_hook("SessionStart")
        code, out = self.cli("telemetry")
        self.assertEqual(code, 0)
        self.assertIn("SessionStart", out)
        self.assertIn("median ms", out)


class TestBudget(MigrateFixture):
    def test_budget_reports_predicted_and_measured(self):
        self.run_hook("SessionStart")
        code, out = self.cli("budget")
        self.assertEqual(code, 0)
        self.assertIn("predicted", out)
        self.assertIn("measured", out)
        self.assertIn("session(s) recorded", out)
        self.assertIn("claude plugin details ctx", out,
                      "must point at the cost it cannot measure itself")

    def test_budget_reports_declared_wave_totals(self):
        for name in ("01-a", "02-b"):
            path = self.make_unit("p", name)
            doc = frontmatter.read(path)
            doc.meta["budget_tokens"] = 30000
            doc.write(path)
        self.cli("plan", "p", "--no-spec")
        self.cli("plan-check", "p")

        code, out = self.cli("budget", "--plan", "p")
        self.assertEqual(code, 0)
        self.assertIn("declared unit budgets", out)
        self.assertIn("60,000", out)

    def test_budget_works_before_any_session_is_recorded(self):
        code, out = self.cli("budget")
        self.assertEqual(code, 0)
        self.assertIn("no sessions recorded yet", out)


class TestProfileDefaults(Fixture):
    """No profile may ship a default that can never fail."""

    def test_no_tautological_defaults(self):
        for name, checks in config_mod.PROFILES.items():
            for check in checks:
                self.assertNotEqual(
                    (check.get("kind"), check.get("path")), ("exists", "."),
                    f"{name}: the working directory always exists, so this check "
                    "makes an unguarded project look guarded",
                )

    def test_every_profile_is_usable(self):
        for name in config_mod.PROFILES:
            code, out = self.cli("init", "--profile", name, "--force")
            self.assertEqual(code, 0, out)
            config = config_mod.load(self.layout)
            self.assertEqual(config["profile"], name)

    def test_judged_only_fallbacks_are_flagged_at_init(self):
        code, out = self.cli("init", "--profile", "docs", "--force")
        self.assertEqual(code, 0)
        self.assertIn("falling back to rubric", out)
        self.assertIn("decides objectively", out, "must say the gate is not objective")


if __name__ == "__main__":
    unittest.main(verbosity=2)
