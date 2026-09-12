"""`ctx/cli.py` wiring — the surfaces waves 1-3 built, made reachable from the
commands people actually type.

Four things earlier waves shipped and nothing dispatched: `dispatch.model_for`
had no caller recording what it picked, `telemetry.summarise`'s `by_role`
breakdown had nothing to group because nothing tagged a `role`, `findings.
Ledger.bump_round` grew a `config`/`model` pair that `cli.py` still called
without, and `phases.can_enter`'s carefully worded refusal had no command that
would ever print it. This file pins that the wiring, not just the modules
underneath it, actually works — the CLI is the only thing a user or an agent
ever runs.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, dispatch, findings as findings_mod, frontmatter,
    miniyaml, phases as phases_mod, plan as plan_mod, review as review_mod,
    telemetry,
)
from support import FAILS, OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class WiringFixture(Fixture):
    """A plan with hand-written unit files — no `ctx plan-unit` scaffolding,
    same shortcut `test_phases.py` and `test_dispatch.py` already take, since
    what matters here is what the CLI does with a unit already on disk."""

    slug = "wiring-plan"

    def unit(self, name, *, tier="subagent", owns=("src/x.py",), reads=(),
             depends_on=(), kind="", reproduction="", phases_field=None,
             budget=45000, model=None, body="## Objective\nDo it.\n"):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
            "depends_on": list(depends_on), "owns": list(owns), "reads": list(reads),
            "forbid": [], "budget_tokens": budget, "status": "pending",
            "verify": list(CHECK),
        }
        if kind:
            meta["kind"] = kind
        if reproduction:
            meta["reproduction"] = reproduction
        if phases_field is not None:
            meta["phases"] = phases_field
        if model:
            meta["model"] = model
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(meta, body).write(path)
        self.trust(CHECK)
        return plan_mod.find_unit(self.layout, self.slug, name)

    def overwrite_config(self, patch):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data.update(patch)
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        self.config = config_mod.load(self.layout)


# --------------------------------------------------------------------------- #
# criterion 1 — `cmd_start` records a dispatch event per unit
# --------------------------------------------------------------------------- #

class TestStartRecordsDispatchTelemetry(WiringFixture):
    def test_one_dispatch_event_per_unit_with_model_role_and_score(self):
        self.unit("01-small", owns=["src/a.py"], budget=1000)
        self.unit("02-big", owns=["src/b.py", "src/c.py"], budget=60000,
                   kind="bug", reproduction=OK)
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)

        records = [r for r in telemetry.read(self.layout) if r["event"] == "dispatch"]
        self.assertEqual(len(records), 2, records)
        by_role = telemetry.summarise(self.layout)
        dispatch_row = next(r for r in by_role if r["event"] == "dispatch")
        self.assertIn("runner", dispatch_row["by_role"])
        self.assertEqual(dispatch_row["by_role"]["runner"]["count"], 2)

        for record in records:
            self.assertEqual(record["role"], "runner")
            self.assertIn("model", record)
            self.assertIn("score", record)
            self.assertIsInstance(record["score"], (int, float))

        # The unit scored heavier (bigger budget, more owned paths, `kind:
        # bug`) must not report the same score as the light one — a wiring
        # bug that always recorded 0 or a constant would still pass a test
        # that only checked the fields existed.
        scores = sorted(r["score"] for r in records)
        self.assertLess(scores[0], scores[1])

    def test_recorded_model_matches_dispatch_model_for(self):
        unit = self.unit("01-solo", owns=["src/a.py"])
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)
        self.assertEqual(self.cli("start")[0], 0)
        record = next(r for r in telemetry.read(self.layout) if r["event"] == "dispatch")
        self.assertEqual(record["model"], dispatch.model_for(self.config, unit))


# --------------------------------------------------------------------------- #
# criterion 2 — the review telemetry event gains `model`/`role`
# --------------------------------------------------------------------------- #

class TestReviewTelemetryGainsModelAndRole(WiringFixture):
    def _reviewed(self, owns=("src/a.py",)):
        unit = self.unit("01-api", owns=owns)
        for path in owns:
            self.write(path, "before\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        for path in owns:
            self.write(path, "after\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        return unit, out

    def test_model_and_role_are_added_bytes_round_and_scope_are_not_disturbed(self):
        unit, out = self._reviewed()
        record = next(r for r in telemetry.read(self.layout) if r["event"] == "review")
        self.assertEqual(record["role"], "reviewer")
        expected_model = dispatch.model_for(
            self.config, role="reviewer",
            stats={"bytes": record["bytes"], "out_of_scope": record["out_of_scope"]},
            round=record["round"],
        )
        self.assertEqual(record["model"], expected_model)
        # Untouched fields, exactly as `07-review-telemetry` left them.
        self.assertEqual(record["round"], 1)
        self.assertEqual(record["out_of_scope"], 0)
        self.assertGreater(record["bytes"], 0)
        self.assertIn(record["model"], out)

    def test_a_scope_violation_is_still_reported_and_still_carries_a_model(self):
        unit = self.unit("01-scoped", owns=["src/a.py"])
        self.write("src/a.py", "before\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "after\n")
        self.write("src/stray.py", "not owned\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        record = next(r for r in telemetry.read(self.layout) if r["event"] == "review")
        self.assertEqual(record["out_of_scope"], 1)
        self.assertEqual(record["role"], "reviewer")
        self.assertIn("model", record)


# --------------------------------------------------------------------------- #
# criterion 3 — `ctx telemetry` reports spend per role
# --------------------------------------------------------------------------- #

class TestTelemetryReportsSpendPerRole(WiringFixture):
    def test_role_lines_appear_under_their_event(self):
        telemetry.record(self.layout, "dispatch", 10.0, role="runner", model="haiku")
        telemetry.record(self.layout, "dispatch", 30.0, role="runner", model="haiku")
        telemetry.record(self.layout, "review", 40.0, role="reviewer", model="opus")
        code, out = self.cli("telemetry")
        self.assertEqual(code, 0, out)
        self.assertIn("runner", out)
        self.assertIn("reviewer", out)
        lines = out.splitlines()
        dispatch_line = next(i for i, line in enumerate(lines)
                             if line.startswith("dispatch"))
        # The role breakdown for `dispatch` has to be a role that actually
        # fired under it (`runner`), and it has to sit under that event, not
        # merely appear somewhere in the output — printing every role's name
        # once at the bottom would satisfy a weaker assertion than this one.
        following = "\n".join(lines[dispatch_line:dispatch_line + 3])
        self.assertIn("runner", following)

    def test_no_telemetry_yet_does_not_crash_on_the_new_reporting(self):
        code, out = self.cli("telemetry")
        self.assertEqual(code, 0)
        self.assertIn("no telemetry recorded", out)


# --------------------------------------------------------------------------- #
# criterion 4 — phase commands advance and refuse per `phases.can_enter`
# --------------------------------------------------------------------------- #

class TestPhaseCommand(WiringFixture):
    def test_a_unit_with_no_declared_phases_has_nothing_to_gate(self):
        self.unit("01-plain")
        code, out = self.cli("phase", "01-plain", "--plan", self.slug)
        self.assertEqual(code, 0)
        self.assertIn("declares no phases", out)

    def test_listing_shows_open_and_locked_state_without_writing_anything(self):
        unit = self.unit("01-bug", kind="bug", reproduction=OK)
        code, out = self.cli("phase", "01-bug", "--plan", self.slug)
        self.assertEqual(code, 0)
        self.assertIn("reproduce", out)
        self.assertIn("open", out)
        ledger = phases_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(ledger.entries, [], "listing must not record anything")

    def test_bug_unit_refused_entering_fix_with_no_failing_reproduction_recorded(self):
        """The headline case: `fix` must not open before `reproduce` has a
        recorded run that actually failed, and the refusal has to name that —
        verbatim, not paraphrased into a generic 'not allowed'."""
        unit = self.unit("01-bug", kind="bug", reproduction=OK)
        ledger = phases_mod.load(self.layout, self.slug, unit.name)
        _ok, expected_why = phases_mod.can_enter(unit, ledger, "fix")
        code, out = self.cli(
            "phase", "01-bug", "fix", "--plan", self.slug,
            "--command", OK, "--exit-code", "0",
        )
        self.assertEqual(code, 1)
        self.assertIn(expected_why, out)
        # And nothing was written — a refused phase command must not leave a
        # partial entry behind for the next attempt to trip over.
        self.assertEqual(phases_mod.load(self.layout, self.slug, unit.name).entries, [])

    def test_a_zero_exit_reproduce_does_not_satisfy_it_and_locate_stays_locked(self):
        unit = self.unit("01-bug", kind="bug", reproduction=OK)
        code, out = self.cli(
            "phase", "01-bug", "reproduce", "--plan", self.slug,
            "--command", OK, "--exit-code", "0",
        )
        self.assertEqual(code, 0, out)  # `reproduce` itself is always open
        code, out = self.cli("phase", "01-bug", "locate", "--plan", self.slug,
                              "--evidence", "src/x.py:1")
        self.assertEqual(code, 1)
        self.assertIn("exited zero", out)
        # `unit` was bound above and never read: the ledger assertion its
        # sibling test makes was missing here. The refused `locate` must leave
        # nothing behind, so the zero-exit `reproduce` is the only entry — a
        # `locate` that recorded itself *and* returned 1 would satisfy every
        # assertion above and unlock `fix` on the next run.
        ledger = phases_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual([e.phase for e in ledger.entries], ["reproduce"])

    def test_full_bug_flow_advances_through_reproduce_locate_fix_guard(self):
        unit = self.unit("01-bug", kind="bug", reproduction=OK)

        code, out = self.cli(
            "phase", "01-bug", "reproduce", "--plan", self.slug,
            "--command", FAILS, "--exit-code", "1", "--evidence", "boom",
        )
        self.assertEqual(code, 0, out)

        code, out = self.cli(
            "phase", "01-bug", "locate", "--plan", self.slug,
            "--evidence", "src/x.py:42 - stale comparison",
        )
        self.assertEqual(code, 0, out)

        # Same reproduction command as `unit.reproduction`, exiting zero.
        code, out = self.cli(
            "phase", "01-bug", "fix", "--plan", self.slug,
            "--command", OK, "--exit-code", "0",
        )
        self.assertEqual(code, 0, out)

        code, out = self.cli("phase", "01-bug", "--plan", self.slug)
        self.assertIn("judged on:", out)
        self.assertIn(phases_mod.GUARD_ABOUT, out)

        code, out = self.cli(
            "phase", "01-bug", "guard", "--plan", self.slug,
            "--exit-code", "0", "--evidence", "verifier: pass",
        )
        self.assertEqual(code, 0, out)

        ledger = phases_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(
            [e.phase for e in ledger.entries], ["reproduce", "locate", "fix", "guard"]
        )

    def test_a_non_bug_unit_with_its_own_declared_phases_gates_in_that_order(self):
        self.unit("01-feature", phases_field=["design", "build"])
        code, out = self.cli("phase", "01-feature", "build", "--plan", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("design", out)
        code, out = self.cli("phase", "01-feature", "design", "--plan", self.slug,
                              "--command", OK, "--exit-code", "0")
        self.assertEqual(code, 0, out)
        code, out = self.cli("phase", "01-feature", "build", "--plan", self.slug,
                              "--command", OK, "--exit-code", "0")
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- #
# round escalation is recorded and reaches `ctx findings`, not just the ledger
# --------------------------------------------------------------------------- #

class TestReviewRoundEscalation(WiringFixture):
    def _open_a_blocking_finding_in_round_1(self, unit):
        code, out = self.cli("findings", unit.name, "--plan", self.slug,
                              "--add", "critical", "--summary", "broken")
        self.assertEqual(code, 0, out)

    def test_escalation_is_disabled_by_default_and_bump_round_still_advances(self):
        unit = self.unit("01-api", owns=["src/a.py"])
        self.write("src/a.py", "before\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "after\n")
        self.assertEqual(self.cli("review", unit.name, "--plan", self.slug)[0], 0)
        self._open_a_blocking_finding_in_round_1(unit)
        self.write("src/a.py", "after again\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(ledger.round, 2)
        self.assertEqual(ledger.escalations, [])

    def test_escalation_enabled_records_the_move_and_surfaces_it_in_findings(self):
        self.overwrite_config({"models": {"escalate_on_failed_round": True}})
        unit = self.unit("01-api", owns=["src/a.py"])
        self.write("src/a.py", "before\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", "after\n")
        self.assertEqual(self.cli("review", unit.name, "--plan", self.slug)[0], 0)
        self._open_a_blocking_finding_in_round_1(unit)
        self.write("src/a.py", "after again\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        self.assertIn("escalated", out)

        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(len(ledger.escalations), 1)
        escalation = ledger.escalations[0]
        self.assertEqual(escalation.from_model, dispatch.model_for(self.config, unit))

        code, out = self.cli("findings", unit.name, "--plan", self.slug)
        self.assertIn("escalations:", out)
        self.assertIn(escalation.line(), out)


if __name__ == "__main__":
    unittest.main()
