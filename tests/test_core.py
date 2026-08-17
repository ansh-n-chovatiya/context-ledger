"""Core tests. Stdlib unittest so `python -m unittest` works with no install.

The assertions that matter most are the ones guarding the risks the design
called out: briefing budgets stay under cap, briefings are deterministic,
journal cost does not grow with project age, hooks stay silent in untracked
projects, and hooks fail open when something unexpected happens.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    briefing, bundle, config as config_mod, frontmatter, hooks, journal,
    miniyaml, paths, redact, state,
)
from support import Fixture  # noqa: E402


class TestMiniYaml(unittest.TestCase):
    def test_subset_round_trip(self):
        text = """
        # comment
        profile: code
        level: 0
        gate:
          enabled: true
          max_attempts: 3
        auto_load: [house, style]
        verify:
          - kind: cmd
            run: pytest -q
          - kind: exists
            path: docs/
        """.replace("        ", "")
        data = miniyaml.loads(text)
        self.assertEqual(data["profile"], "code")
        self.assertEqual(data["level"], 0)
        self.assertEqual(data["gate"], {"enabled": True, "max_attempts": 3})
        self.assertEqual(data["auto_load"], ["house", "style"])
        self.assertEqual(data["verify"][0], {"kind": "cmd", "run": "pytest -q"})
        self.assertEqual(data["verify"][1], {"kind": "exists", "path": "docs/"})

    def test_url_value_is_not_a_nested_map(self):
        data = miniyaml.loads("home: https://example.com/x\nrun: pytest -q --tb=short")
        self.assertEqual(data["home"], "https://example.com/x")
        self.assertEqual(data["run"], "pytest -q --tb=short")

    def test_malformed_raises_rather_than_guessing(self):
        with self.assertRaises(miniyaml.MiniYamlError):
            miniyaml.loads("key: {inline: map}")
        with self.assertRaises(miniyaml.MiniYamlError):
            miniyaml.loads("not a mapping at all")

    def test_dumps_reparses(self):
        original = {
            "schema": 1, "profile": "code", "flag": False,
            "nested": {"a": 1}, "items": ["x", "y"],
            "verify": [{"kind": "cmd", "run": "pytest -q"}],
        }
        self.assertEqual(miniyaml.loads(miniyaml.dumps(original)), original)


class TestRedact(unittest.TestCase):
    def test_removes_credentials(self):
        for secret in (
            "api_key: sk-abcdefghijklmnopqrstuvwx",
            "AWS key AKIAIOSFODNN7EXAMPLE here",
            "password=hunter2correct",
            "token: ghp_abcdefghijklmnopqrstuvwxyz012345",
        ):
            self.assertIn(redact.PLACEHOLDER, redact.scrub(secret), secret)

    def test_keeps_git_shas_and_paths(self):
        text = "edit src/auth/refresh.ts at 356a192b7913b04c54574d18c28d46e6395428ab"
        self.assertEqual(redact.scrub(text), text)

    def test_bad_user_pattern_does_not_break_writes(self):
        self.assertEqual(redact.scrub("hello", ["("]), "hello")


class TestFrontmatter(unittest.TestCase):
    def test_sections_and_items(self):
        doc = frontmatter.parse(
            "---\ntask: demo\nverify:\n  - kind: cmd\n    run: pytest\n---\n\n"
            "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. one\n2. two\n"
        )
        self.assertEqual(doc.meta["task"], "demo")
        self.assertEqual(doc.section("objective"), "Do the thing.")
        self.assertEqual(doc.list_items("acceptance criteria"), ["one", "two"])

    def test_missing_frontmatter_is_still_a_document(self):
        doc = frontmatter.parse("## Objective\nplain\n")
        self.assertFalse(doc.had_frontmatter)
        self.assertEqual(doc.section("objective"), "plain")


class TestJournalCost(Fixture):
    def test_digest_is_bounded_regardless_of_history(self):
        for index in range(400):
            journal.append(self.layout, self.config, "edit", f"src/file{index}.py")
        journal.write_digest(self.layout, self.config)
        text = self.layout.digest.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.startswith("- ")]
        self.assertEqual(len(lines), self.config["journal"]["digest_lines"])
        self.assertIn("earlier entries", text)
        self.assertLess(len(text), 2500)

    def test_entries_are_scrubbed_and_length_capped(self):
        journal.append(self.layout, self.config, "edit", "x.py", "token: ghp_" + "a" * 40)
        body = self.layout.journal_file(journal.today()).read_text(encoding="utf-8")
        self.assertNotIn("ghp_aaaa", body)
        for line in body.splitlines():
            self.assertLessEqual(len(line), self.config["journal"]["max_line_chars"])

    def test_recent_paths_prefers_most_recent(self):
        journal.append(self.layout, self.config, "edit", "a.py")
        journal.append(self.layout, self.config, "edit", "b.py")
        self.assertEqual(journal.recent_paths(self.layout, 2), ["b.py", "a.py"])


class TestBriefingBudget(Fixture):
    def _briefing(self, level):
        current = dict(state.load(self.layout), level=level)
        return briefing.build(self.layout, self.config, current)

    def test_l0_is_tiny(self):
        text = self._briefing("0")
        self.assertLessEqual(len(text), config_mod.briefing_cap(self.config, "0"))
        self.assertLess(len(text) / 3.6, 80, "L0 must stay near-free")

    def test_every_level_respects_its_cap(self):
        self.cli("task", "big-task")
        doc = frontmatter.read(self.layout.task_file("big-task"))
        doc.body = (
            "## Objective\n" + ("very long objective sentence. " * 200) + "\n\n"
            "## Acceptance criteria\n"
            + "".join(f"{i}. criterion {'x' * 120}\n" for i in range(1, 30))
        )
        doc.write(self.layout.task_file("big-task"))
        for level in config_mod.LEVELS:
            text = self._briefing(level)
            cap = config_mod.briefing_cap(self.config, level)
            self.assertLessEqual(len(text), cap, f"L{level} exceeded its cap")

    def test_deterministic_across_calls(self):
        self.cli("task", "stable")
        first = self._briefing("1")
        second = self._briefing("1")
        self.assertEqual(first, second, "briefing must be byte-identical for cache hits")
        self.assertNotRegex(first, r"\d{4}-\d{2}-\d{2}T", "no timestamps in briefings")

    def test_criteria_reach_the_briefing(self):
        self.cli("task", "refresh")
        path = self.layout.task_file("refresh")
        doc = frontmatter.read(path)
        doc.body = (
            "## Objective\nRenew expiring tokens.\n\n"
            "## Acceptance criteria\n1. exactly one refresh\n2. shared promise\n"
        )
        doc.write(path)
        text = self._briefing("1")
        self.assertIn("exactly one refresh", text)
        self.assertIn("objective: Renew expiring tokens.", text)


class TestBundles(Fixture):
    def test_save_resolve_and_promote(self):
        body = "# Context — demo\n\n## Situation\nWe are testing.\n\n## Resume here\nRun tests.\n"
        path = bundle.save(self.layout, "Demo Bundle", body, config=self.config)
        self.assertTrue(path.is_file())
        self.assertEqual(bundle.resolve(self.layout, "demo-bundle"), path)
        self.assertIn("demo-bundle", self.layout.context_index.read_text(encoding="utf-8"))

        promoted = bundle.promote(self.layout, "demo-bundle")
        self.assertTrue(promoted.is_file())
        self.assertIn("scope: global", promoted.read_text(encoding="utf-8"))

        # A bundle saved in one project resolves from another via the global store.
        other = self.root / "other"
        (other / ".ctx" / "contexts").mkdir(parents=True)
        self.assertEqual(
            bundle.resolve(paths.Layout(other / ".ctx"), "demo-bundle"), promoted
        )

    def test_secrets_never_reach_disk(self):
        path = bundle.save(
            self.layout, "leaky",
            "## Situation\nkey is sk-abcdefghijklmnopqrstuvwx\n", config=self.config,
        )
        self.assertNotIn("sk-abcdefghij", path.read_text(encoding="utf-8"))

    def test_template_has_the_full_schema(self):
        rendered = bundle.template("x").render()
        for heading, _hint in bundle.SECTIONS:
            self.assertIn(f"## {heading}", rendered)


class TestHooks(Fixture):
    def test_silent_in_untracked_project(self):
        out = io.StringIO()
        code = hooks.main(
            "SessionStart",
            io.StringIO(json.dumps({"cwd": str(self.untracked), "session_id": "s"})),
            out,
        )
        self.assertEqual((code, out.getvalue()), (0, ""))

    def test_subdirectory_of_a_tracked_project_is_tracked(self):
        nested = self.root / "src" / "deep"
        nested.mkdir(parents=True)
        out = io.StringIO()
        hooks.main(
            "SessionStart",
            io.StringIO(json.dumps({"cwd": str(nested), "session_id": "s"})),
            out,
        )
        self.assertTrue(out.getvalue().startswith("[ctx]"))

    def test_session_start_emits_budgeted_briefing(self):
        code, text = self.run_hook("SessionStart")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("[ctx] L0"))
        self.assertLessEqual(len(text.strip()), config_mod.briefing_cap(self.config, "0"))

    def test_post_tool_use_journals_without_emitting(self):
        code, text = self.run_hook(
            "PostToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.root / "src" / "a.py")},
        )
        self.assertEqual((code, text), (0, ""), "journalling must cost no context")
        self.assertIn("src/a.py", self.layout.journal_file(journal.today()).read_text())

    def test_prompt_submit_is_silent_until_drift(self):
        self.assertEqual(self.run_hook("UserPromptSubmit"), (0, ""))
        state.set_nudge(self.layout, "out of scope")
        code, text = self.run_hook("UserPromptSubmit")
        self.assertEqual(code, 0)
        self.assertIn("out of scope", text)
        # One-shot: consumed, so steady-state cost returns to zero.
        self.assertEqual(self.run_hook("UserPromptSubmit"), (0, ""))

    def test_scope_violation_sets_a_nudge_but_does_not_block(self):
        plan = self.layout.plans / "p" / "units"
        plan.mkdir(parents=True)
        frontmatter.Document(
            {"unit": "01-a", "owns": ["src/allowed.py"], "forbid": ["src/theirs.py"]},
            "## Objective\nx\n",
        ).write(plan / "01-a.md")
        state.update(self.layout, level="2", plan="p", unit="01-a")

        code, text = self.run_hook(
            "PreToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.root / "src" / "theirs.py")},
        )
        self.assertEqual((code, text), (0, ""), "PreToolUse must warn, not block")
        self.assertIn("forbid", state.take_nudge(self.layout))

        self.run_hook(
            "PreToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.root / "src" / "allowed.py")},
        )
        self.assertEqual(state.take_nudge(self.layout), "", "owned paths are silent")

    def test_pre_compact_writes_a_recoverable_autosave(self):
        journal.append(self.layout, self.config, "edit", "src/a.py")
        code, text = self.run_hook("PreCompact")
        self.assertEqual((code, text), (0, ""))
        saved = bundle.resolve(self.layout, "autosave-sess1234")
        self.assertIsNotNone(saved, "compaction must leave a resumable snapshot")
        self.assertIn("src/a.py", saved.read_text(encoding="utf-8"))
        # The name on disk must match the name the code asks for.
        self.assertEqual(saved.name, "autosave-sess1234.ctx.md")

    def test_fails_open_on_unexpected_error(self):
        original = briefing.build
        briefing.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            code, text = self.run_hook("SessionStart")
        finally:
            briefing.build = original
        self.assertEqual((code, text), (0, ""), "a broken hook must not brick a session")
        self.assertIn("boom", self.layout.errors.read_text(encoding="utf-8"))

    def test_garbage_payload_is_survivable(self):
        out = io.StringIO()
        self.assertEqual(hooks.main("SessionStart", io.StringIO("not json"), out), 0)
        self.assertEqual(hooks.main("SessionStart", io.StringIO(""), out), 0)


class TestLevels(Fixture):
    def test_task_escalates_and_drop_returns(self):
        self.cli("task", "Fix Token Refresh")
        current = state.load(self.layout)
        self.assertEqual((current["level"], current["task"]), ("1", "fix-token-refresh"))
        self.assertTrue(self.layout.task_file("fix-token-refresh").is_file())

        self.cli("drop")
        current = state.load(self.layout)
        self.assertEqual(current["level"], "0")
        self.assertIsNone(current["task"])
        self.assertTrue(
            self.layout.task_file("fix-token-refresh").is_file(),
            "dropping ceremony must not delete work",
        )

    def test_corrupt_state_degrades_to_l0(self):
        self.layout.state.write_text("{ not json", encoding="utf-8")
        self.assertEqual(state.load(self.layout)["level"], "0")

    def test_attempt_counter_bounds_the_gate(self):
        for expected in (1, 2, 3):
            self.assertEqual(state.bump_attempts(self.layout, "unit-a"), expected)
        state.clear_attempts(self.layout, "unit-a")
        self.assertEqual(state.attempts(self.layout, "unit-a"), 0)


class TestInit(Fixture):
    def test_scaffold_is_complete_and_runtime_is_ignored(self):
        for directory in self.layout.dirs():
            self.assertTrue(directory.is_dir(), directory)
        self.assertEqual(
            (self.layout.root / ".gitignore").read_text(encoding="utf-8").strip(),
            "runtime/",
        )
        self.assertTrue(self.layout.digest.is_file())
        self.assertTrue(self.layout.context_index.is_file())
        self.assertEqual(self.config["level"], "0")

    def test_refuses_a_newer_schema(self):
        self.layout.config.write_text("schema: 99\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            config_mod.load(self.layout)

    def test_doctor_passes_on_a_fresh_ledger(self):
        self.assertEqual(self.cli("doctor")[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
