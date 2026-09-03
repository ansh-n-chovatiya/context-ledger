"""Regressions for the Wave 4 audit findings (F23, F24, F25, F26, F27, F28)."""

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ctx import frontmatter, journal, plan as plan_mod, state  # noqa: E402
from support import Fixture  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# F23 — one entry point
# --------------------------------------------------------------------------- #

class TestNext(Fixture):
    """Using the tool required knowing which level you were at and which command
    that level implied. The state machine already knew."""

    def next_line(self):
        code, out = self.cli("next")
        self.assertEqual(code, 0, out)
        return out

    def test_l0_with_nothing_recorded_points_at_a_task(self):
        self.assertIn("/ctx:task", self.next_line())

    def test_l0_with_recent_work_points_at_resume(self):
        journal.append(self.layout, self.config, "edit", "src/auth.py", "")
        self.assertIn("/ctx:resume", self.next_line())

    def test_l1_points_at_the_gate(self):
        self.cli("task", "demo", "--objective", "x")
        self.assertIn("/ctx:verify", self.next_line())

    def test_a_blocked_spec_points_at_the_questions(self):
        self.cli("spec", "billing", "--intent", "Move billing")
        self.cli("question", "billing", "Does invoice_v1 still take traffic?")
        out = self.next_line()
        self.assertIn("/ctx:ask", out)
        self.assertIn("blocking", out)

    def test_a_ready_spec_points_at_planning(self):
        self.cli("spec", "billing", "--intent", "Move billing")
        self.assertIn("/ctx:plan", self.next_line())

    def test_a_dispatchable_plan_points_at_start(self):
        self.cli("spec", "auth", "--intent", "Rotate keys")
        directory = plan_mod.units_dir(self.layout, "auth")
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-keys", "plan": "auth", "tier": "subagent",
             "depends_on": [], "owns": ["src/keys.py"], "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": "pending",
             "verify": [{"kind": "cmd", "run": "true"}]},
            "## Objective\nx\n\n## Acceptance criteria\n1. y\n",
        ).write(directory / "01-keys.md")
        self.trust([{"kind": "cmd", "run": "true"}])
        self.cli("plan", "auth")
        self.cli("plan-check", "auth")
        self.assertIn("/ctx:start", self.next_line())

    def test_an_unaccepted_command_outranks_everything_else(self):
        path = self.layout.task_file("demo")
        self.cli("task", "demo", "--objective", "x")
        doc = frontmatter.read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "echo unreviewed"}]
        doc.write(path)
        self.assertIn("ctx trust", self.next_line())

    def test_a_command_file_ships_for_it(self):
        self.assertTrue((ROOT / "commands/next.md").is_file())


# --------------------------------------------------------------------------- #
# F24 — escalation is a ramp
# --------------------------------------------------------------------------- #

class TestEscalate(Fixture):
    def make_task(self):
        self.cli("task", "add-auth", "--objective", "Users can sign in with SSO")
        path = self.layout.task_file("add-auth")
        doc = frontmatter.read(path)
        doc.body = (
            "## Objective\nUsers can sign in with SSO\n\n"
            "## Acceptance criteria\n"
            "1. A failed handshake surfaces AuthExpiredError\n"
            "2. Refresh happens exactly once per expiry\n"
        )
        doc.write(path)
        return path

    def test_criteria_come_across_rather_than_being_rewritten(self):
        """Going L1 to L2 meant abandoning the task file at exactly the moment
        when throwing away what was already written costs the most."""
        self.make_task()
        code, out = self.cli("escalate")
        self.assertEqual(code, 0, out)
        spec = (self.layout.specs / "add-auth" / "spec.md").read_text(encoding="utf-8")
        self.assertIn("A failed handshake surfaces AuthExpiredError", spec)
        self.assertIn("Refresh happens exactly once per expiry", spec)
        self.assertIn("Users can sign in with SSO", spec)

    def test_the_level_moves_and_the_spec_becomes_active(self):
        self.make_task()
        self.cli("escalate")
        current = state.load(self.layout)
        self.assertEqual(current["level"], "2")
        self.assertEqual(current["spec"], "add-auth")
        self.assertIsNone(current["task"])

    def test_the_task_file_is_kept_as_the_record(self):
        path = self.make_task()
        self.cli("escalate")
        doc = frontmatter.read(path)
        self.assertTrue(path.is_file(), "the task file is why this grew")
        self.assertEqual(doc.meta["status"], "escalated")
        self.assertIn("add-auth", doc.meta["escalated_to"])

    def test_escalating_with_nothing_active_asks_rather_than_failing(self):
        code, out = self.cli("escalate")
        self.assertEqual(code, 0, "must not abort the slash command")
        self.assertIn("no task name", out)

    def test_a_missing_task_file_is_reported(self):
        code, out = self.cli("escalate", "never-existed")
        self.assertEqual(code, 1)
        self.assertIn("no task file", out)

    def test_a_command_file_ships_for_it(self):
        self.assertTrue((ROOT / "commands/escalate.md").is_file())


# --------------------------------------------------------------------------- #
# F25 — the split is visible
# --------------------------------------------------------------------------- #

class TestTaskSplitIsVisible(Fixture):
    def test_task_reports_how_it_read_the_arguments(self):
        """`_split_name` guesses from punctuation on the most-used command. A
        wrong guess used to be silent."""
        _code, out = self.cli("task", "add-auth", "let", "users", "log", "in")
        self.assertIn("read as", out)
        self.assertIn("name: add-auth", out)
        self.assertIn("let users log in", out)

    def test_it_says_how_to_correct_a_wrong_split(self):
        _code, out = self.cli("task", "Fix", "the", "login", "page")
        self.assertIn("read as", out)
        self.assertIn("--objective", out)


# --------------------------------------------------------------------------- #
# F26 — the orchestrator rule is at least measured
# --------------------------------------------------------------------------- #

class TestOrchestratorDiscipline(Fixture):
    def setUp(self):
        super().setUp()
        directory = plan_mod.units_dir(self.layout, "auth")
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-keys", "plan": "auth", "tier": "subagent",
             "depends_on": [], "owns": ["src/keys.py"], "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": "pending",
             "verify": [{"kind": "cmd", "run": "true"}]},
            "## Objective\nx\n\n## Acceptance criteria\n1. y\n",
        ).write(directory / "01-keys.md")
        self.trust([{"kind": "cmd", "run": "true"}])
        self.cli("plan", "auth", "--no-spec")
        self.cli("plan-check", "auth")

    def test_editing_unowned_source_during_a_wave_is_reported(self):
        """"Do not read source files" was repeated in the brief and checked
        nowhere. Reads cost a hook spawn each; edits are already journalled."""
        journal.append(self.layout, self.config, "edit", "src/unrelated.py", "")
        _code, out = self.cli("status")
        self.assertIn("orchestrator discipline", out)
        self.assertIn("src/unrelated.py", out)

    def test_editing_an_owned_path_is_not_reported(self):
        journal.append(self.layout, self.config, "edit", "src/keys.py", "")
        _code, out = self.cli("status")
        self.assertNotIn("orchestrator discipline", out)

    def test_ledger_writes_are_never_counted(self):
        journal.append(self.layout, self.config, "edit", ".ctx/tasks/x.md", "")
        _code, out = self.cli("status")
        self.assertNotIn("orchestrator discipline", out)

    def test_a_claimed_unit_means_you_are_not_the_orchestrator(self):
        journal.append(self.layout, self.config, "edit", "src/unrelated.py", "")
        self.cli("unit", "01-keys")
        _code, out = self.cli("status")
        self.assertNotIn("orchestrator discipline", out)


# --------------------------------------------------------------------------- #
# F27 / F28 — the record agrees with itself
# --------------------------------------------------------------------------- #

class TestDocumentedClaims(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_no_file_hardcodes_the_plugins_always_on_cost(self):
        """README said ~557/587 tokens, SKILL.md said ~425/455 — and SKILL.md is
        the one loaded into the model. Two hardcoded numbers is how they came to
        disagree."""
        for name in ("README.md", "skills/ledger/SKILL.md"):
            with self.subTest(name=name):
                for stale in ("425", "455", "557", "587"):
                    self.assertNotIn(stale, self.read(name))

    def test_both_files_point_at_the_measuring_commands(self):
        for name in ("README.md", "skills/ledger/SKILL.md"):
            with self.subTest(name=name):
                text = self.read(name)
                self.assertIn("ctx budget", text)
                self.assertIn("claude plugin details ctx", text)

    def test_every_slash_command_referenced_in_docs_exists(self):
        """`/ctx:budget` was referenced in SKILL.md and is CLI-only. A slash
        command that does not exist is a dead end the model will walk into."""
        import re as _re
        shipped = {p.stem for p in (ROOT / "commands").glob("*.md")}
        for name in ("README.md", "skills/ledger/SKILL.md",
                     "agents/verifier.md", "agents/unit-runner.md"):
            for referenced in _re.findall(r"/ctx:([a-z-]+)", self.read(name)):
                with self.subTest(doc=name, command=referenced):
                    self.assertIn(referenced, shipped)

    def test_the_readme_does_not_assert_a_test_count(self):
        """A number in prose goes stale on the next commit; CI counts."""
        self.assertIsNone(
            re.search(r"\b\d{2,4} tests\b", self.read("README.md")),
            "let CI assert the count, not the README",
        )

    def test_the_tests_directory_is_importable(self):
        """`python -m unittest discover -s tests -t .` is what people reach for
        first, and it raised ImportError without this."""
        self.assertTrue((ROOT / "tests/__init__.py").is_file())

    def test_ci_runs_both_discovery_forms(self):
        text = self.read(".github/workflows/ci.yml")
        self.assertIn("unittest discover", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
