"""Gate tests — phases 3 and 4.

These are the assertions that make the two headline complaints actually fixable:
a vague request produces blocking questions rather than code, and incomplete work
cannot end its session. The subtler properties matter just as much, so they are
pinned here too: a configuration failure never blocks, the gate is bounded and
escalates, judged sign-offs do not outlive an edit, and expensive checks never
run once a cheap one has already failed.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, journal, spec as spec_mod, state, trust, verify, work,
)
from support import FAILS, OK, Fixture  # noqa: E402


def with_checks(layout, slug, checks, criteria=("first thing", "second thing")):
    """Point a task file at a specific set of verify checks."""
    trust.accept(layout, checks)
    path = layout.task_file(slug)
    doc = frontmatter.read(path)
    doc.meta["verify"] = list(checks)
    doc.body = (
        "## Objective\nDo the thing.\n\n## Acceptance criteria\n"
        + "".join(f"{i}. {c}\n" for i, c in enumerate(criteria, 1))
    )
    doc.write(path)
    return path


# --------------------------------------------------------------------------- #
# phase 3 — the ambiguity gate
# --------------------------------------------------------------------------- #

class TestAmbiguityGate(Fixture):
    def open_spec(self, slug="auth-rotation"):
        self.cli("spec", slug, "--intent", "Rotate signing keys without downtime.")
        return slug

    def test_spec_scaffolds_both_files_and_escalates(self):
        slug = self.open_spec()
        self.assertTrue(spec_mod.spec_path(self.layout, slug).is_file())
        self.assertTrue(spec_mod.questions_path(self.layout, slug).is_file())
        current = state.load(self.layout)
        self.assertEqual((current["level"], current["spec"]), ("2", slug))

    def test_blocking_questions_hold_the_gate_shut(self):
        slug = self.open_spec()
        ready, _ = spec_mod.ready(self.layout, slug)
        self.assertTrue(ready, "a spec with no questions starts ready")

        self.cli("question", slug, "Does invoice_v1 still receive traffic?")
        ready, blocking = spec_mod.ready(self.layout, slug)
        self.assertFalse(ready)
        self.assertEqual(len(blocking), 1)
        self.assertEqual(self.cli("spec-ready", slug)[0], 1, "Gate 1 must exit non-zero")

    def test_non_blocking_questions_do_not_hold_the_gate(self):
        slug = self.open_spec()
        self.cli("question", slug, "Any preference on log format?", "--non-blocking")
        ready, blocking = spec_mod.ready(self.layout, slug)
        self.assertTrue(ready)
        self.assertEqual(blocking, [])
        _blocking, non_blocking, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(len(non_blocking), 1)

    def test_resolving_opens_the_gate_and_leaves_an_audit_trail(self):
        slug = self.open_spec()
        self.cli("question", slug, "Does invoice_v1 still receive traffic?")
        code, _ = self.cli(
            "resolve", slug, "--question", "invoice_v1", "--answer", "No, read-only since July",
        )
        self.assertEqual(code, 0)

        ready, blocking = spec_mod.ready(self.layout, slug)
        self.assertTrue(ready, "resolving the last blocker opens the gate")
        self.assertEqual(blocking, [])
        self.assertEqual(self.cli("spec-ready", slug)[0], 0)

        body = spec_mod.questions_path(self.layout, slug).read_text(encoding="utf-8")
        self.assertIn("[x]", body)
        self.assertIn("No, read-only since July", body)
        self.assertRegex(body, r"\(\d{4}-\d{2}-\d{2}\)", "answers are dated")

        doc = frontmatter.read(spec_mod.spec_path(self.layout, slug))
        self.assertEqual(doc.meta["status"], "ready")

    def test_resolving_an_unknown_question_fails_loudly(self):
        slug = self.open_spec()
        self.cli("question", slug, "Real question?")
        code, out = self.cli(
            "resolve", slug, "--question", "nonexistent", "--answer", "x",
        )
        self.assertEqual(code, 1)
        self.assertIn("no open question", out)
        self.assertFalse(spec_mod.ready(self.layout, slug)[0], "gate stays shut")

    def test_duplicate_questions_are_not_added_twice(self):
        slug = self.open_spec()
        self.cli("question", slug, "Same question?")
        self.cli("question", slug, "Same question?")
        self.assertEqual(len(spec_mod.ready(self.layout, slug)[1]), 1)

    def test_multiple_blockers_need_all_resolved(self):
        slug = self.open_spec()
        self.cli("question", slug, "First blocker?", "Second blocker?")
        self.assertEqual(len(spec_mod.ready(self.layout, slug)[1]), 2)
        self.cli("resolve", slug, "--question", "First", "--answer", "a")
        self.assertFalse(spec_mod.ready(self.layout, slug)[0])
        self.cli("resolve", slug, "--question", "Second", "--answer", "b")
        self.assertTrue(spec_mod.ready(self.layout, slug)[0])

    def test_briefing_surfaces_the_blocked_state(self):
        from ctx import briefing

        slug = self.open_spec()
        self.cli("question", slug, "Does invoice_v1 still receive traffic?")
        text = briefing.build(self.layout, self.config, state.load(self.layout))
        self.assertIn("BLOCKED", text)
        self.assertIn("invoice_v1", text)

    def test_adrs_number_up_and_are_separate_files(self):
        first = spec_mod.write_decision(self.layout, "Use idempotency keys", "idempotency-keys")
        second = spec_mod.write_decision(self.layout, "Adopt worktrees", "adopt-worktrees")
        self.assertEqual(first.name, "0001-idempotency-keys.md")
        self.assertEqual(second.name, "0002-adopt-worktrees.md")
        self.assertEqual(spec_mod.next_adr_number(self.layout), 3)
        self.assertIn("Supersede with a new ADR", second.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# phase 4 — verify kinds
# --------------------------------------------------------------------------- #

class TestVerifyKinds(Fixture):
    def run_checks(self, checks, **kwargs):
        self.trust(checks)
        return verify.run(
            self.layout, self.config, checks, cwd=self.root, key="k", **kwargs
        )

    def test_cost_ordering_puts_cheap_checks_first(self):
        checks = [
            {"kind": "rubric"}, {"kind": "cmd", "run": OK},
            {"kind": "exists", "path": "."}, {"kind": "diff"},
        ]
        self.assertEqual(
            [c["kind"] for c in verify.ordered(checks)],
            ["diff", "exists", "cmd", "rubric"],
        )

    def test_malformed_checks_are_dropped_not_crashed_on(self):
        self.assertEqual(verify.ordered(["nonsense", {"kind": "bogus"}, None]), [])

    def test_passing_command(self):
        results, verdict = self.run_checks([{"kind": "cmd", "run": OK}])
        self.assertEqual(verdict, verify.PASS)
        self.assertEqual(results[0].status, verify.PASS)

    def test_failing_command_blocks_and_writes_a_full_log(self):
        results, verdict = self.run_checks(
            [{"kind": "cmd", "run": self.py(
                "import sys; print('boom-detail'); sys.exit(3)")}]
        )
        self.assertEqual(verdict, verify.FAIL)
        self.assertIn("exit 3", results[0].message)
        self.assertIn("boom-detail", results[0].message)
        logs = list(self.layout.verify_logs.glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("boom-detail", logs[0].read_text(encoding="utf-8"))

    def test_gate_feedback_is_truncated(self):
        results, _ = self.run_checks(
            [{"kind": "cmd", "run": self.py(
                "import sys; [print(f'line{i}') for i in range(1, 401)]; sys.exit(1)")}]
        )
        message = results[0].message
        self.assertIn("lines omitted", message)
        self.assertLess(len(message.splitlines()), 70, "feedback must stay bounded")
        # Head and tail both survive; the middle does not.
        self.assertIn("line1", message)
        self.assertIn("line400", message)
        self.assertNotIn("line200", message)

    def test_missing_binary_is_a_config_error_not_a_work_failure(self):
        results, verdict = self.run_checks(
            [{"kind": "cmd", "run": "definitely-not-a-real-binary-xyz"}]
        )
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertEqual(verdict, verify.ERROR, "a broken check must not block work")

    def test_absent_tool_that_exits_1_is_still_a_config_error(self):
        """`python3 -m pytest` with pytest absent exits 1, not 127.

        Found by a smoke run: the interpreter ran fine, so the exit code says
        nothing about the tool being missing. Classifying that as a work failure
        would block every session in a project whose toolchain isn't installed.
        """
        cases = [
            "python3 -c \"import sys; sys.stderr.write('No module named pytest\\n'); sys.exit(1)\"",
            "python3 -c \"import sys; sys.stderr.write('npm error Missing script: \\\\\"test\\\\\"\\n'); sys.exit(1)\"",
            "python3 -c \"import sys; sys.stderr.write('sh: mycli: command not found\\n'); sys.exit(2)\"",
        ]
        for command in cases:
            results, verdict = self.run_checks([{"kind": "cmd", "run": command}])
            self.assertEqual(results[0].status, verify.ERROR, command)
            self.assertIn("tool not available", results[0].message)
            self.assertEqual(verdict, verify.ERROR)

    def test_a_real_test_failure_is_still_a_work_failure(self):
        """The missing-tool heuristic must not swallow genuine failures."""
        command = (
            "python3 -c \"import sys; "
            "sys.stderr.write('AssertionError: expected token, got None\\n'); sys.exit(1)\""
        )
        results, verdict = self.run_checks([{"kind": "cmd", "run": command}])
        self.assertEqual(results[0].status, verify.FAIL)
        self.assertEqual(verdict, verify.FAIL)

    def test_missing_tool_detection_is_targeted(self):
        self.assertEqual(verify._missing_tool("No module named pytest"), "pytest")
        self.assertEqual(verify._missing_tool("2 failed, 1 passed"), "")
        self.assertEqual(verify._missing_tool(""), "")

    def test_timeout_is_a_config_error(self):
        config = dict(self.config, gate=dict(self.config["gate"], timeout_seconds=1))
        checks = [{"kind": "cmd", "run": self.py("import time; time.sleep(5)")}]
        self.trust(checks)
        results, verdict = verify.run(
            self.layout, config, checks, cwd=self.root, key="slow",
        )
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertIn("timed out", results[0].message)
        self.assertEqual(verdict, verify.ERROR)

    def test_exists_kind_with_content_matching(self):
        self.write("docs/guide.md", "# Guide\nstable API\n")
        results, verdict = self.run_checks(
            [{"kind": "exists", "path": "docs/guide.md", "matches": "stable API"}]
        )
        self.assertEqual(verdict, verify.PASS)

        results, verdict = self.run_checks(
            [{"kind": "exists", "path": "docs/guide.md", "matches": "absent phrase"}]
        )
        self.assertEqual(verdict, verify.FAIL)
        self.assertIn("does not match", results[0].message)

        results, verdict = self.run_checks([{"kind": "exists", "path": "docs/nope.md"}])
        self.assertEqual(verdict, verify.FAIL)

    def test_diff_kind_enforces_owned_scope(self):
        self.git_init()
        self.write("src/mine.py", "x = 1\n")
        results, verdict = self.run_checks([{"kind": "diff"}], owns=["src/mine.py"])
        self.assertEqual(verdict, verify.PASS, "editing an owned path is fine")

        self.write("src/theirs.py", "y = 2\n")
        results, verdict = self.run_checks([{"kind": "diff"}], owns=["src/mine.py"])
        self.assertEqual(verdict, verify.FAIL)
        self.assertIn("src/theirs.py", results[0].message)

    def test_diff_without_git_is_a_config_error(self):
        results, verdict = self.run_checks([{"kind": "diff"}], owns=["src/x.py"])
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertEqual(verdict, verify.ERROR)

    def test_expensive_checks_never_run_after_a_cheap_failure(self):
        self.git_init()
        self.write("src/stray.py", "z = 3\n")
        sentinel = self.root / "sentinel.txt"
        results, verdict = self.run_checks(
            [
                {"kind": "cmd", "run": f"touch {sentinel}"},
                {"kind": "diff"},
            ],
            owns=["src/allowed.py"],
        )
        self.assertEqual(verdict, verify.FAIL)
        self.assertEqual(len(results), 1, "short-circuited before the subprocess")
        self.assertFalse(sentinel.exists(), "the expensive check must not have run")

    def test_judged_checks_are_pending_until_recorded(self):
        _results, verdict = self.run_checks([{"kind": "rubric"}])
        self.assertEqual(verdict, verify.PENDING)
        _results, verdict = self.run_checks([{"kind": "rubric"}], recorded=["rubric"])
        self.assertEqual(verdict, verify.PASS)


# --------------------------------------------------------------------------- #
# phase 4 — the Stop hook
# --------------------------------------------------------------------------- #

class TestDoneGate(Fixture):
    def arm(self, checks, criteria=("first thing", "second thing")):
        self.cli("task", "gated")
        with_checks(self.layout, "gated", checks, criteria)
        return work.active(self.layout)

    def stop(self, **extra):
        code, out = self.run_hook("Stop", **extra)
        self.assertEqual(code, 0, "the gate must always exit 0; JSON carries the verdict")
        return json.loads(out) if out.strip() else None

    def test_l0_never_blocks(self):
        self.cli("task", "gated")
        with_checks(self.layout, "gated", [{"kind": "cmd", "run": FAILS}])
        self.cli("drop")
        self.assertIsNone(self.stop(), "L0 has no gate — that is what makes it free")

    def test_incomplete_work_cannot_end_its_session(self):
        self.arm([{"kind": "cmd", "run": self.py(
            "import sys; print('missing-criterion-2'); sys.exit(1)")}])
        decision = self.stop()
        self.assertEqual(decision["decision"], "block")
        reason = decision["reason"]
        self.assertIn("done-gate blocked", reason.lower())
        self.assertIn("missing-criterion-2", reason)
        self.assertIn("first thing", reason, "criteria are restated so it cannot drift")
        self.assertIn("attempt 1 of 3", reason)

    def test_passing_work_is_allowed_through_and_clears_attempts(self):
        self.arm([{"kind": "cmd", "run": FAILS}])
        self.stop()
        self.assertEqual(state.attempts(self.layout, "gated"), 1)

        with_checks(self.layout, "gated", [{"kind": "cmd", "run": OK}])
        self.assertIsNone(self.stop())
        self.assertEqual(state.attempts(self.layout, "gated"), 0)

    def test_gate_is_bounded_and_escalates_rather_than_grinding(self):
        self.arm([{"kind": "cmd", "run": "exit 1"}])
        for attempt in (1, 2, 3):
            decision = self.stop(stop_hook_active=attempt > 1)
            self.assertIsNotNone(decision, f"attempt {attempt} should block")
            self.assertIn(f"attempt {attempt} of 3", decision["reason"])

        self.assertIsNone(self.stop(stop_hook_active=True), "4th attempt must give up")
        doc = frontmatter.read(self.layout.task_file("gated"))
        self.assertEqual(doc.meta["status"], "verify_failed")
        nudge = state.take_nudge(self.layout)
        self.assertIn("failed 3 times", nudge)
        self.assertIn("tell the user", nudge)

    def test_configuration_failure_never_blocks(self):
        self.arm([{"kind": "cmd", "run": "definitely-not-a-real-binary-xyz"}])
        self.assertIsNone(self.stop(), "a broken check must not brick the project")
        self.assertEqual(state.attempts(self.layout, "gated"), 0)

    def test_env_override_disables_the_gate(self):
        self.arm([{"kind": "cmd", "run": "exit 1"}])
        os.environ["CTX_GATE"] = "off"
        try:
            self.assertIsNone(self.stop())
        finally:
            os.environ.pop("CTX_GATE")
        self.assertIsNotNone(self.stop(), "and re-enables when unset")

    def test_config_disable_disables_the_gate(self):
        from ctx import miniyaml

        self.arm([{"kind": "cmd", "run": "exit 1"}])
        self.assertIsNotNone(self.stop(), "blocks while enabled")
        state.clear_attempts(self.layout)

        # Edit the gate block specifically — `journal.enabled` is also `true`,
        # and a naive string replace would flip that one instead.
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data["gate"]["enabled"] = False
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        self.assertTrue(self.layout.config.read_text(encoding="utf-8").count("enabled: false") == 1)

        self.assertIsNone(self.stop())

    def test_no_checks_means_no_gate(self):
        self.arm([])
        self.assertIsNone(self.stop(), "an unconfigured gate cannot block")

    def test_pending_rubric_blocks_until_signed_off(self):
        self.arm([{"kind": "rubric", "about": "criteria met in spirit"}])
        decision = self.stop()
        self.assertIsNotNone(decision)
        self.assertIn("/ctx:verify", decision["reason"])

        self.cli("verify", "--sign-off", "rubric", "--note", "verifier said pass")
        self.assertIsNone(self.stop(), "a recorded sign-off satisfies the check")

    def test_a_sign_off_does_not_survive_a_later_edit(self):
        self.arm([{"kind": "rubric"}])
        self.cli("verify", "--sign-off", "rubric")
        self.assertIsNone(self.stop())

        # Any edit invalidates the judgement, because the code changed under it.
        self.run_hook(
            "PostToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.root / "src" / "changed.py")},
        )
        self.assertEqual(work.active(self.layout).recorded, [])
        self.assertIsNotNone(self.stop(), "gate re-closes after an edit")

    def test_gate_fails_open_on_internal_error(self):
        self.arm([{"kind": "cmd", "run": "exit 1"}])
        original = verify.run
        verify.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate exploded"))
        try:
            self.assertIsNone(self.stop(), "our own bug must not block the user")
        finally:
            verify.run = original
        self.assertIn("gate exploded", self.layout.errors.read_text(encoding="utf-8"))

    def test_outcome_is_journalled(self):
        self.arm([{"kind": "cmd", "run": "exit 1"}])
        self.stop()
        body = self.layout.journal_file(journal.today()).read_text(encoding="utf-8")
        self.assertIn("gate", body)
        self.assertIn("gated", body)


class TestVerifyCommand(Fixture):
    def test_reports_each_check_and_exit_code(self):
        self.cli("task", "gated")
        with_checks(
            self.layout, "gated",
            [{"kind": "exists", "path": "missing.txt"}, {"kind": "cmd", "run": OK}],
        )
        code, out = self.cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("missing.txt", out)

    def test_pass_reports_zero(self):
        self.cli("task", "gated")
        with_checks(self.layout, "gated", [{"kind": "cmd", "run": OK}])
        code, out = self.cli("verify")
        self.assertEqual(code, 0)
        self.assertIn("PASS", out)

    def test_exit_codes_distinguish_broken_work_from_broken_config(self):
        self.cli("task", "gated")
        with_checks(self.layout, "gated", [{"kind": "cmd", "run": "exit 1"}])
        self.assertEqual(self.cli("verify")[0], 1, "1 = a criterion failed")

        with_checks(self.layout, "gated", [{"kind": "cmd", "run": "no-such-binary-xyz"}])
        code, out = self.cli("verify")
        self.assertEqual(code, 2, "2 = nothing could run")
        self.assertIn("ctx.yaml problem", out)

    def test_says_so_when_nothing_is_active(self):
        # Exit 0, because there is no gate to fail — L0 has nothing to verify.
        # A non-zero exit here would abort the whole `/ctx:verify` slash command
        # before the prompt body could explain what to do instead.
        code, out = self.cli("verify")
        self.assertEqual(code, 0)
        self.assertIn("nothing active", out)
        self.assertNotIn("PASS", out)

    def test_warns_when_no_checks_are_configured(self):
        self.cli("task", "gated")
        with_checks(self.layout, "gated", [])
        code, out = self.cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("the gate cannot hold", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
