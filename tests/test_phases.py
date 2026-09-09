"""Phase gates — `ctx.phases`.

The whole point of this module is that "no fix before a failing reproduction"
must be something the code refuses on, not something a document asks for. So
the tests here are organised around the refusals: what exactly does not
satisfy each bug phase, and does the gate actually close when it should not
be open. A test suite that only exercised the happy path would prove the
mechanism exists without proving it works — the sense in which `reproduce`
is satisfied is *inverted* from a normal check (a passing command fails the
phase), and that is exactly the kind of thing that is easy to get backwards
without a test that fails the moment it is.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, phases, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class PhasesFixture(Fixture):
    """A plan directory holding units this module can gate. No spec gate is
    needed: these tests read/write units and phase ledgers directly, they
    never go through `ctx start`."""

    slug = "widget-repair"

    def unit(self, name, *, kind="", reproduction="", phases_field=None,
             owns=("src/x.py",), body="## Objective\nDo it.\n"):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
            "budget_tokens": 45000, "status": "pending", "verify": list(CHECK),
        }
        if kind:
            meta["kind"] = kind
        if reproduction:
            meta["reproduction"] = reproduction
        if phases_field is not None:
            meta["phases"] = phases_field
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(meta, body).write(path)
        return plan_mod.find_unit(self.layout, self.slug, name)

    def record(self, unit, phase, **kw):
        return phases.record(self.layout, self.slug, unit, phase, **kw)

    def ledger(self, unit):
        return phases.load(self.layout, self.slug, unit.name)


# --------------------------------------------------------------------------- #
# for_unit: the general mechanism and the bug preset
# --------------------------------------------------------------------------- #

class TestForUnit(PhasesFixture):
    def test_bug_kind_gets_the_four_phase_preset(self):
        unit = self.unit("01-crash", kind="bug", reproduction=OK)
        names = [p.name for p in phases.for_unit(unit)]
        self.assertEqual(names, list(phases.BUG))
        self.assertEqual(list(phases.BUG), ["reproduce", "locate", "fix", "guard"])

    def test_bug_preset_wins_over_a_declared_phases_list(self):
        """A bug unit cannot opt out of the preset by declaring its own
        `phases:` — that would let a bug unit dodge the whole mechanism this
        module exists to enforce by spelling its phases differently."""
        unit = self.unit(
            "01-crash", kind="bug", reproduction=OK,
            phases_field=["design", "build"],
        )
        names = [p.name for p in phases.for_unit(unit)]
        self.assertEqual(names, list(phases.BUG))

    def test_non_bug_unit_uses_its_declared_phases_in_order(self):
        unit = self.unit("01-feature", phases_field=["design", "build", "ship"])
        names = [p.name for p in phases.for_unit(unit)]
        self.assertEqual(names, ["design", "build", "ship"])

    def test_no_phases_declared_is_an_empty_list(self):
        unit = self.unit("01-plain")
        self.assertEqual(phases.for_unit(unit), [])

    def test_guard_carries_a_rubric_check_for_the_verifier(self):
        """Criterion 5: `guard` is a `rubric` check routed to the existing
        verifier agent — meaning the Phase object carries exactly the check
        shape `ctx.verify` already knows how to run and label."""
        unit = self.unit("01-crash", kind="bug", reproduction=OK)
        guard = [p for p in phases.for_unit(unit) if p.name == "guard"][0]
        self.assertEqual(guard.check.get("kind"), "rubric")
        self.assertTrue(guard.check.get("about"))

    def test_non_bug_phase_named_guard_carries_no_check(self):
        """The general mechanism must not smuggle in bug semantics just
        because a phase happens to be named the same as a preset phase."""
        unit = self.unit("01-feature", phases_field=["build", "guard"])
        guard = [p for p in phases.for_unit(unit) if p.name == "guard"][0]
        self.assertEqual(guard.check, {})


# --------------------------------------------------------------------------- #
# Criterion 1 — reproduce is satisfied only by a recorded non-zero exit
# --------------------------------------------------------------------------- #

class TestReproducePhase(PhasesFixture):
    def test_reproduce_is_always_open(self):
        unit = self.unit("01-crash", kind="bug", reproduction=OK)
        ok, why = phases.can_enter(unit, self.ledger(unit), "reproduce")
        self.assertTrue(ok, why)

    def test_zero_exit_does_not_satisfy_reproduce(self):
        """The single most important assertion in this module: a reproduction
        command that *passes* has reproduced nothing, and must not unlock
        `locate`."""
        unit = self.unit("01-crash", kind="bug", reproduction=OK)
        ok, _why = self.record(unit, "reproduce", command=OK, exit_code=0)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "locate")
        self.assertFalse(allowed)
        self.assertIn("reproduce", why)

    def test_non_zero_exit_satisfies_reproduce(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        ok, _why = self.record(unit, "reproduce", command="fails", exit_code=1)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "locate")
        self.assertTrue(allowed, why)

    def test_no_recorded_run_does_not_satisfy_reproduce(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        allowed, why = phases.can_enter(unit, self.ledger(unit), "locate")
        self.assertFalse(allowed)
        self.assertIn("reproduce", why)
        self.assertIn("not been recorded", why)


# --------------------------------------------------------------------------- #
# Criterion 3 — locate needs file:line evidence, not a bare assertion
# --------------------------------------------------------------------------- #

class TestLocatePhase(PhasesFixture):
    def _reproduced(self, unit):
        self.record(unit, "reproduce", command=unit.reproduction, exit_code=1)

    def test_bare_assertion_does_not_satisfy_locate(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._reproduced(unit)
        ok, _why = self.record(unit, "locate", evidence="it's somewhere in auth")
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "fix")
        self.assertFalse(allowed)
        self.assertIn("locate", why)

    def test_file_line_evidence_satisfies_locate(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._reproduced(unit)
        ok, _why = self.record(
            unit, "locate", evidence="off-by-one in ctx/auth.py:42"
        )
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "fix")
        self.assertTrue(allowed, why)


# --------------------------------------------------------------------------- #
# Criterion 2 — fix is locked until both reproduce and locate are recorded,
# and the refusal names which is missing
# --------------------------------------------------------------------------- #

class TestFixLocked(PhasesFixture):
    def test_fix_refused_when_neither_is_recorded(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        allowed, why = phases.can_enter(unit, self.ledger(unit), "fix")
        self.assertFalse(allowed)
        self.assertIn("reproduce", why)
        self.assertIn("locate", why)

    def test_fix_refused_when_only_reproduce_is_recorded(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self.record(unit, "reproduce", command="fails", exit_code=1)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "fix")
        self.assertFalse(allowed)
        self.assertIn("locate", why)
        self.assertNotIn("reproduce (", why)

    def test_fix_refused_when_only_locate_is_recorded(self):
        """A hand-edited ledger, not one built through `record` — `can_enter`
        has to compute correctly from whatever a ledger holds, not just the
        states its own gate would ever construct. This is also the only way
        to reach "locate recorded, reproduce not" at all: `record` refuses
        to enter `locate` itself until `reproduce` is satisfied, so this
        state cannot arise through the normal path."""
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        ledger = self.ledger(unit)
        ledger.add("locate", evidence="ctx/auth.py:42")
        allowed, why = phases.can_enter(unit, ledger, "fix")
        self.assertFalse(allowed)
        self.assertIn("reproduce", why)
        self.assertNotIn("locate (", why)

    def test_fix_opens_once_both_are_recorded(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self.record(unit, "reproduce", command="fails", exit_code=1)
        self.record(unit, "locate", evidence="ctx/auth.py:42")
        allowed, why = phases.can_enter(unit, self.ledger(unit), "fix")
        self.assertTrue(allowed, why)

    def test_record_itself_refuses_to_persist_a_locked_fix(self):
        """The gate is enforced at the write, not just advised at the read —
        `record` must refuse even if a caller never called `can_enter`."""
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        ok, why = self.record(unit, "fix", command="fails", exit_code=0)
        self.assertFalse(ok)
        self.assertIsInstance(why, str)
        self.assertIn("fix", why)
        # Nothing was written for a refused phase.
        self.assertEqual(self.ledger(unit).for_phase("fix"), [])


# --------------------------------------------------------------------------- #
# Criterion 4 — after fix, the SAME reproduction command must be recorded
# exiting zero
# --------------------------------------------------------------------------- #

class TestFixSameCommand(PhasesFixture):
    def _unlocked_for_fix(self, unit):
        self.record(unit, "reproduce", command=unit.reproduction, exit_code=1)
        self.record(unit, "locate", evidence="ctx/auth.py:42")

    def test_guard_locked_before_any_fix_recorded(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._unlocked_for_fix(unit)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "guard")
        self.assertFalse(allowed)
        self.assertIn("guard", why)

    def test_a_fix_attempt_that_still_fails_does_not_open_guard(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._unlocked_for_fix(unit)
        ok, _ = self.record(unit, "fix", command="fails", exit_code=1)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "guard")
        self.assertFalse(allowed, why)

    def test_a_different_command_exiting_zero_does_not_open_guard(self):
        """Recording some other command's success must not count — that would
        let a fix swap in an easier check that never reproduced the bug."""
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._unlocked_for_fix(unit)
        ok, _ = self.record(unit, "fix", command="some other command", exit_code=0)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "guard")
        self.assertFalse(allowed, why)

    def test_same_command_exiting_zero_opens_guard(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self._unlocked_for_fix(unit)
        ok, _ = self.record(unit, "fix", command="fails", exit_code=0)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "guard")
        self.assertTrue(allowed, why)

    def test_whitespace_differences_in_the_command_still_count_as_the_same(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails now")
        self.record(unit, "reproduce", command="fails now", exit_code=1)
        self.record(unit, "locate", evidence="ctx/auth.py:42")
        ok, _ = self.record(unit, "fix", command="fails   now", exit_code=0)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "guard")
        self.assertTrue(allowed, why)


# --------------------------------------------------------------------------- #
# Criterion 6 — a non-bug unit's declared phases gate in order, with no bug
# semantics attached
# --------------------------------------------------------------------------- #

class TestGenericPhaseOrder(PhasesFixture):
    def test_first_declared_phase_is_always_open(self):
        unit = self.unit("01-feature", phases_field=["design", "build", "ship"])
        allowed, why = phases.can_enter(unit, self.ledger(unit), "design")
        self.assertTrue(allowed, why)

    def test_later_phase_locked_until_earlier_ones_are_recorded(self):
        unit = self.unit("01-feature", phases_field=["design", "build", "ship"])
        allowed, why = phases.can_enter(unit, self.ledger(unit), "build")
        self.assertFalse(allowed)
        self.assertIn("design", why)

    def test_any_record_at_all_unlocks_the_next_phase(self):
        """No bug semantics: a phase named `reproduce` in a non-bug unit's
        declared list is satisfied by a plain zero-exit record — the
        inversion from criterion 1 is a `kind: bug` behaviour only."""
        unit = self.unit(
            "01-feature", phases_field=["reproduce", "ship"],
        )
        ok, _ = self.record(unit, "reproduce", command=OK, exit_code=0)
        self.assertTrue(ok)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "ship")
        self.assertTrue(allowed, why)

    def test_phase_not_declared_is_refused(self):
        unit = self.unit("01-feature", phases_field=["design", "build"])
        allowed, why = phases.can_enter(unit, self.ledger(unit), "ship")
        self.assertFalse(allowed)
        self.assertIn("ship", why)

    def test_gating_holds_through_the_whole_declared_order(self):
        unit = self.unit("01-feature", phases_field=["design", "build", "ship"])
        self.record(unit, "design", note="sketched it")
        allowed, why = phases.can_enter(unit, self.ledger(unit), "build")
        self.assertTrue(allowed, why)
        allowed, why = phases.can_enter(unit, self.ledger(unit), "ship")
        self.assertFalse(allowed, why)
        self.record(unit, "build", note="built it")
        allowed, why = phases.can_enter(unit, self.ledger(unit), "ship")
        self.assertTrue(allowed, why)


# --------------------------------------------------------------------------- #
# persistence: entries round-trip through the file, evidence included
# --------------------------------------------------------------------------- #

class TestLedgerPersistence(PhasesFixture):
    def test_missing_ledger_file_is_an_empty_ledger_not_an_error(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        ledger = self.ledger(unit)
        self.assertEqual(ledger.entries, [])

    def test_recorded_entries_survive_a_reload(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        self.record(unit, "reproduce", command="fails", exit_code=1,
                    evidence="ctx/auth.py:42\ntraceback here")
        reloaded = self.ledger(unit)
        entries = reloaded.for_phase("reproduce")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].exit_code, 1)
        self.assertEqual(entries[0].command, "fails")
        self.assertIn("ctx/auth.py:42", entries[0].evidence)

    def test_evidence_containing_a_fence_does_not_truncate_the_entry(self):
        """The same hazard `findings` guards against: evidence can be command
        output or a diff excerpt containing a ``` line, and the ledger must
        not corrupt itself on content it is asked to hold. A plain non-bug
        phase is used here on purpose — this test is about the persistence
        format, not about phase gating."""
        unit = self.unit("01-feature", phases_field=["step"])
        evidence = "before\n```\nembedded fence\n```\nctx/auth.py:42\nafter"
        self.record(unit, "step", evidence=evidence)
        reloaded = self.ledger(unit)
        entries = reloaded.for_phase("step")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].evidence, evidence)

    def test_ledger_file_is_never_written_into_frontmatter(self):
        """Evidence with newlines must live in the body — frontmatter is
        YAML, and a multi-line scalar there does not round-trip. Storing it
        in the body instead is what makes the reload assertions above pass at
        all, so this pins the choice itself, not just its consequence."""
        unit = self.unit("01-feature", phases_field=["step"])
        self.record(unit, "step", evidence="line one\nline two\nctx/auth.py:9")
        path = phases.path_for(self.layout, self.slug, unit.name)
        text = path.read_text(encoding="utf-8")
        head, _, tail = text.partition("---\n")[2].partition("\n---\n")
        self.assertNotIn("line one", head)
        self.assertIn("line one", tail)


# --------------------------------------------------------------------------- #
# criterion 12 — no file outside `owns` is modified
# --------------------------------------------------------------------------- #

class TestScope(PhasesFixture):
    def test_recording_a_phase_does_not_touch_the_unit_file(self):
        unit = self.unit("01-crash", kind="bug", reproduction="fails")
        before = unit.path.read_text(encoding="utf-8")
        self.record(unit, "reproduce", command="fails", exit_code=1)
        after = unit.path.read_text(encoding="utf-8")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
