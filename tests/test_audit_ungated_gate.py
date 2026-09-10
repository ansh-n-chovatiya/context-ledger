"""Ungated is not done, and a runner may not forge its own contract.

Two audit P0s, both landing in the `--status done` transition.

**An unrunnable check scored as a passing check.** `verdict_of` ranks ERROR
above PASS, but the gate then printed a warning and marked the unit `done`
anyway. Combined with `PROFILES["code"] = []` — the default profile carries only
`cmd` checks, and every `cmd` check ERRORs on a machine that has never run `ctx
trust` — a freshly cloned repository could take every unit in a plan to `done`
with zero checks executed.

**A runner could forge its own contract.** The `unit-runner` agent holds `Write`
and `Edit`, and `verify.is_ledger` exempts everything under `.ctx/` from the
`diff` scope check — deliberately, because a concurrent wave writes to the
ledger constantly. So a runner that could not make the tests pass could delete
the failing `verify:` entry, trim an acceptance criterion, or delete the
blocking findings raised against it, and nothing compared the result to what was
dispatched.

Every test here carries a positive control: a refusal that fires for everything
is worth no more than one that fires for nothing, so each mechanism is also
shown *not* refusing the legitimate version of the same action.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, contract as contract_mod, findings as findings_mod,
    frontmatter, hooks, plan as plan_mod, trust as trust_mod, work,
)
from support import OK, Fixture  # noqa: E402


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class GateFixture(Fixture):
    """A one-unit plan that can be dispatched, forged and gated."""

    slug = "auth"

    def unit(self, name="01-api", checks=None, *, owns=None, reads=(),
             body=CRITERIA, accept=True):
        checks = [{"kind": "cmd", "run": OK}] if checks is None else list(checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [],
                "owns": list(owns or [f"src/{name}.py"]), "reads": list(reads),
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "wave": 1, "verify": checks,
            },
            body,
        ).write(path)
        if accept:
            self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return path

    def forget_trust(self):
        """Empty the trust store — a fresh clone on a second machine.

        `ctx init` accepts the checks it proposes, so the fixture starts
        trusted. A cloned repository does not.
        """
        path = trust_mod.path_for(self.layout)
        if path.is_file():
            path.unlink()

    def dispatch(self):
        code, out = self.cli("start", "--no-worktree")
        self.assertEqual(code, 0, out)
        return out

    def meta_of(self, name="01-api"):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        return frontmatter.read(path).meta

    def status_of(self, name="01-api"):
        return self.meta_of(name)["status"]

    def raw_of(self, name="01-api"):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        return path.read_text(encoding="utf-8")

    def edit(self, name="01-api", body=None, **changes):
        """Edit a unit file the way a runner with `Write` would."""
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        if body is not None:
            doc.body = body
        doc.write(path)
        return path

    def journal_text(self):
        return "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(self.layout.journal.glob("*.md"))
        )

    def mark_done(self, name="01-api", *extra):
        return self.cli("unit", name, "--status", "done", *extra)


# --------------------------------------------------------------------------- #
# 1, 2, 3 — an unrunnable check is not a passing check
# --------------------------------------------------------------------------- #

class TestUngatedIsNotDone(GateFixture):

    def test_an_all_error_gate_refuses_the_done_transition(self):
        """Criterion 1. The gate used to print `warning: no check could run …
        so this is not blocking` and write `status: done`."""
        self.unit(accept=False)
        self.forget_trust()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.status_of(), "pending", "nothing was written")
        self.assertIn("refusing", out)
        # names the configuration problem …
        self.assertIn("ctx trust", out)
        self.assertIn("configuration problem", out)
        # … and says what that means for the transition.
        self.assertIn("ungated is not done", out)

    def test_the_same_unit_reaches_done_once_the_commands_are_accepted(self):
        """Positive control for criterion 1: the refusal is about the checks
        not running, not about this unit."""
        self.unit(accept=False)
        self.forget_trust()
        self.assertEqual(self.mark_done()[0], 1)
        self.trust([{"kind": "cmd", "run": OK}])
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_a_freshly_cloned_default_plan_cannot_reach_done(self):
        """Criterion 2: the default configuration, end to end.

        `PROFILES["code"]` is empty, so a `code` plan carries only the `cmd`
        checks `ctx init` wrote — and on a machine that never accepted them,
        every one of them ERRORs. This is not an exotic path; it is `git clone`
        followed by `/ctx:start`.
        """
        self.assertEqual(config_mod.PROFILES["code"], [],
                         "the premise: the code profile adds no non-cmd check")
        checks = [{"kind": "cmd", "run": OK}]
        self.assertTrue(all(c["kind"] == "cmd" for c in checks),
                        "so a `code` unit's whole gate is `cmd` checks")
        for name in ("01-api", "02-store"):
            self.unit(name, checks, owns=[f"src/{name}.py"])
        self.forget_trust()  # the second laptop, or CI
        self.assertFalse(trust_mod.load(self.layout),
                         "an empty trust store is the whole point")
        self.dispatch()
        for name in ("01-api", "02-store"):
            code, out = self.mark_done(name)
            self.assertEqual(code, 1, out)
            self.assertIn("status: pending", self.raw_of(name),
                          "the unit file still says pending on disk")
        self.assertEqual(plan_mod.next_wave(self.layout, self.slug), 1,
                         "and the plan did not advance")

    def test_the_same_cloned_plan_completes_once_this_machine_accepts_it(self):
        """Positive control for criterion 2: `ctx trust` is the missing step,
        and the refusal names it. Nothing about the plan itself was wrong."""
        checks = [{"kind": "cmd", "run": OK}]
        for name in ("01-api", "02-store"):
            self.unit(name, checks, owns=[f"src/{name}.py"])
        self.forget_trust()
        self.dispatch()
        self.assertEqual(self.mark_done("01-api")[0], 1)
        self.trust(checks)  # what `ctx trust` does
        for name in ("01-api", "02-store"):
            code, out = self.mark_done(name)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.status_of(name), "done")
        self.assertIsNone(plan_mod.next_wave(self.layout, self.slug),
                          "and now the plan is complete")

    def test_one_passing_check_beside_an_error_still_completes(self):
        """Criterion 3. The refusal is for *no check reached PASS*, never for
        *any check errored* — that would brick a project the moment one tool
        was missing."""
        self.write("src/01-api.py", "x = 1\n")
        checks = [
            {"kind": "exists", "path": "src/01-api.py"},
            {"kind": "cmd", "run": OK},
        ]
        self.unit(checks=checks, accept=False)
        self.forget_trust()  # the `cmd` errors; the `exists` still passes
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")
        self.assertIn("not every check could run", out)
        self.assertNotIn("ungated is not done", out)

    def test_the_control_for_that_is_the_same_unit_with_no_passing_check(self):
        """Positive control for criterion 3: drop the one check that passed and
        the same gate refuses."""
        self.write("src/01-api.py", "x = 1\n")
        self.unit(checks=[{"kind": "cmd", "run": OK}], accept=False)
        self.forget_trust()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("ungated is not done", out)


# --------------------------------------------------------------------------- #
# 4 — --force is the escape hatch, and it is loud
# --------------------------------------------------------------------------- #

class TestForceOverride(GateFixture):

    def test_force_completes_and_records_what_it_overrode(self):
        """Criterion 4."""
        self.unit(accept=False)
        self.forget_trust()
        code, out = self.mark_done("01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")
        self.assertIn("--force overrode the done-gate", out)
        journal = self.journal_text()
        self.assertIn("--force overrode the gate", journal)
        self.assertIn("no check could run", journal,
                      "the journal records *why* the gate refused")

    def test_force_over_a_forged_contract_names_the_forgery_in_the_journal(self):
        self.unit()
        self.dispatch()
        self.edit(verify=[])
        code, out = self.mark_done("01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertIn("contract edited after dispatch", self.journal_text())

    def test_an_unforced_refusal_is_journalled_as_a_refusal(self):
        """Positive control: the journal distinguishes overridden from refused."""
        self.unit(accept=False)
        self.forget_trust()
        self.assertEqual(self.mark_done()[0], 1)
        journal = self.journal_text()
        self.assertIn("done refused (no check could run)", journal)
        self.assertNotIn("--force", journal)

    def test_a_forced_done_that_would_have_passed_says_so(self):
        """Positive control: `--force` does not invent a refusal."""
        self.unit()
        code, out = self.mark_done("01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertNotIn("overrode the done-gate", out)
        self.assertIn("the gate passed anyway", self.journal_text())


# --------------------------------------------------------------------------- #
# 5, 6 — what must *not* change: the Stop hook and a bare `ctx verify`
# --------------------------------------------------------------------------- #

class TestErrorStaysNonBlockingElsewhere(GateFixture):

    def arm(self):
        """Focus the unit so it is the active work the Stop hook sees."""
        self.unit(accept=False)
        self.forget_trust()
        self.assertEqual(self.cli("unit", "01-api")[0], 0)
        return work.active(self.layout)

    def test_the_stop_hook_returns_empty_for_an_all_error_result(self):
        """Criterion 5. A session must never be bricked by infrastructure: a
        project whose toolchain is not installed still gets to end its turn."""
        item = self.arm()
        self.assertIsNotNone(item)
        config = config_mod.load(self.layout)
        decision = hooks.on_stop(self.layout, config, self.payload())
        self.assertEqual(decision, "", "an unrunnable check may not block a session")
        self.assertIn("incomplete (a check could not run)", self.journal_text())
        self.assertNotIn("gate | 01-api | pass", self.journal_text())
        self.assertNotEqual(self.status_of(), "done",
                            "and it certainly did not sign the work off")

    def test_the_stop_hook_still_blocks_a_real_failure(self):
        """Positive control for criterion 5: ERROR is special, FAIL is not."""
        self.unit(checks=[{"kind": "cmd", "run": '"%s" -c "raise SystemExit(1)"'
                                                 % sys.executable}])
        self.assertEqual(self.cli("unit", "01-api")[0], 0)
        config = config_mod.load(self.layout)
        decision = hooks.on_stop(self.layout, config, self.payload())
        self.assertIsInstance(decision, dict)
        self.assertEqual(decision["decision"], "block")

    def test_a_bare_ctx_verify_reports_error_and_refuses_nothing(self):
        """Criterion 6. `ctx verify` is a report, not a transition."""
        self.arm()
        code, out = self.cli("verify")
        self.assertEqual(code, 2, "2 is `nothing could run`, distinct from a failure")
        self.assertIn("ERROR", out)
        self.assertIn("ctx.yaml problem", out)
        self.assertNotIn("refusing", out)
        self.assertNotIn("ungated is not done", out)
        self.assertNotEqual(self.status_of(), "done", "a report completes nothing")

    def test_a_bare_ctx_verify_still_passes_a_runnable_unit(self):
        """Positive control for criterion 6."""
        self.unit()
        self.assertEqual(self.cli("unit", "01-api")[0], 0)
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)


# --------------------------------------------------------------------------- #
# 7, 9, 10 — the contract cannot be rewritten after dispatch
# --------------------------------------------------------------------------- #

class TestContractCannotBeForged(GateFixture):

    def test_deleting_the_failing_verify_entry_is_refused(self):
        """Criterion 7, `verify:`. The cheapest forgery there is: the unit
        cannot pass its check, so it deletes the check."""
        failing = {"kind": "cmd", "run": '"%s" -c "raise SystemExit(1)"'
                                         % sys.executable}
        self.unit(checks=[failing])
        self.dispatch()
        self.assertEqual(self.mark_done()[0], 1, "it genuinely fails first")
        self.edit(verify=[{"kind": "cmd", "run": OK}])
        self.trust([{"kind": "cmd", "run": OK}])
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("contract changed after", out)
        self.assertIn("changed: verify", out)
        self.assertEqual(self.status_of(), "pending")

    def test_widening_owns_after_dispatch_is_refused(self):
        """Criterion 7, `owns:`. `owns` is what every sibling unit's isolation
        rests on, so a unit that widens it mid-wave has changed the promise the
        wave was planned against."""
        self.unit(owns=["src/api.py"])
        self.dispatch()
        self.edit(owns=["src/api.py", "src/store.py"])
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("changed: owns", out)
        self.assertEqual(self.status_of(), "pending")

    def test_trimming_an_acceptance_criterion_is_refused(self):
        """Criterion 7, the criteria body."""
        self.unit(body="## Objective\nDo it.\n\n## Acceptance criteria\n"
                       "1. the parser round-trips\n2. the CLI reports it\n")
        self.dispatch()
        self.edit(body="## Objective\nDo it.\n\n## Acceptance criteria\n"
                       "1. the parser round-trips\n")
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("changed: acceptance criteria", out)
        self.assertEqual(self.status_of(), "pending")

    def test_appending_a_sign_off_to_verified_is_refused(self):
        """`verified: [rubric, human]` was the other named forgery: judged
        checks pass on the mere presence of their kind in `recorded`."""
        self.unit(checks=[{"kind": "rubric", "about": "the work is sound"}])
        self.dispatch()
        self.edit(verified=["rubric"])
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("changed: verified", out)

    def test_a_unit_that_only_did_its_work_passes_its_gate(self):
        """Criterion 9, and the positive control for all of criterion 7: the
        comparison must not fire on an honest unit."""
        self.unit()
        self.dispatch()
        self.write("src/01-api.py", "def api():\n    return 1\n")
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")
        self.assertNotIn("contract changed", out)

    def test_flipping_only_the_status_field_is_not_a_violation(self):
        """Criterion 9. `status:` is the one field the runner is *supposed* to
        move; hashing the whole file would refuse the transition the gate
        exists to guard."""
        self.unit()
        self.dispatch()
        path = self.edit(status="running")
        self.assertEqual(frontmatter.read(path).meta["status"], "running")
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_ledger_bookkeeping_during_the_gate_is_not_a_violation(self):
        """Criterion 9. Journal appends, `state.json` and telemetry all write
        inside `.ctx/` while the gate runs. If those counted, no unit could ever
        pass."""
        self.unit()
        self.dispatch()
        self.assertEqual(self.cli("unit", "01-api")[0], 0)   # state.json + journal
        self.assertEqual(self.cli("verify")[0], 0)           # a gate run + journal
        self.assertEqual(self.cli("journal", "note", "01-api",
                                  "--note", "working")[0], 0)
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_reordering_owns_is_not_a_forgery(self):
        """The digest tracks the promise, not the formatting. A digest that
        trips on a re-ordered list teaches people to reach for `--force`."""
        self.unit(owns=["src/a.py", "src/b.py"])
        self.dispatch()
        self.edit(owns=["src/b.py", "src/a.py"])
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)

    def test_a_unit_with_no_baseline_is_reported_not_refused(self):
        """Criterion 10. A plan dispatched before this shipped, or one whose
        snapshot failed, has nothing to compare against. Failing closed here
        would brick every in-flight plan on upgrade."""
        self.unit()  # never dispatched: no seal, no snapshot
        self.assertIsNone(contract_mod.baseline(
            self.layout, self.slug,
            plan_mod.find_unit(self.layout, self.slug, "01-api"),
        ))
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertIn("no dispatch baseline", out)
        self.assertEqual(self.status_of(), "done")

    def test_a_forged_unit_with_no_baseline_is_also_not_refused(self):
        """The uncomfortable half of criterion 10, stated out loud: without a
        baseline the forgery is invisible, and that is the price of not
        bricking in-flight plans. The unit above proves the note appears; this
        one proves the fall-through really does fall through."""
        self.unit(checks=[{"kind": "cmd", "run": OK}])
        self.edit(owns=["src/anything.py"], verify=[{"kind": "cmd", "run": OK}])
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertIn("no dispatch baseline", out)

    def test_losing_the_seal_after_dispatch_reverts_to_no_baseline(self):
        """Positive control for criterion 10's mechanism: the fall-through is
        keyed on the baseline being absent, not on the unit being clean."""
        self.unit()
        self.dispatch()
        unit = plan_mod.find_unit(self.layout, self.slug, "01-api")
        self.assertIsNotNone(contract_mod.baseline(self.layout, self.slug, unit))
        self.edit(owns=["src/elsewhere.py"])
        self.assertEqual(self.mark_done()[0], 1, "with a baseline it is refused")
        contract_mod.discard(self.layout, self.slug, "01-api")
        self.layout.runtime.joinpath("snapshots").exists() and self.rmtree(
            self.layout.runtime / "snapshots"
        )
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertIn("no dispatch baseline", out)


# --------------------------------------------------------------------------- #
# 8 — findings cannot be deleted or downgraded behind ctx's back
# --------------------------------------------------------------------------- #

class TestFindingsCannotBeErased(GateFixture):

    def raise_finding(self, severity="critical", summary="the parser drops rows"):
        code, out = self.cli(
            "findings", "01-api", "--add", severity, "--summary", summary,
            "--where", "ctx/parse.py:40",
        )
        self.assertEqual(code, 0, out)
        return findings_mod.path_for(self.layout, self.slug, "01-api")

    def test_deleting_the_findings_file_is_refused(self):
        """Criterion 8."""
        self.unit()
        self.dispatch()
        path = self.raise_finding()
        path.unlink()
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("finding [1]", out)
        self.assertIn("deleted", out)
        self.assertEqual(self.status_of(), "pending")

    def test_downgrading_a_critical_finding_in_place_is_refused(self):
        self.unit()
        self.dispatch()
        path = self.raise_finding()
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "critical / open", "minor / open"),
            encoding="utf-8",
        )
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("downgraded", out)
        self.assertEqual(self.status_of(), "pending")

    def test_closing_a_finding_by_hand_is_refused(self):
        self.unit()
        self.dispatch()
        path = self.raise_finding()
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "critical / open", "critical / addressed"),
            encoding="utf-8",
        )
        ledger = findings_mod.load(self.layout, self.slug, "01-api")
        self.assertEqual(ledger.findings[0].status, "addressed", "the edit landed")
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("without `ctx findings --set`", out)

    def test_closing_a_finding_through_ctx_is_the_legitimate_path(self):
        """Positive control for criterion 8: the mechanism must not stand in
        the way of a finding being properly addressed."""
        self.unit()
        self.dispatch()
        self.raise_finding()
        code, out = self.cli("findings", "01-api", "--set", "1",
                             "--status", "addressed", "--evidence", "fixed in r2")
        self.assertEqual(code, 0, out)
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_a_hand_written_finding_ctx_has_seen_cannot_be_deleted(self):
        """A reviewer that writes the ledger directly is covered too, from the
        moment any ctx command reads it: observation seals, and the seal never
        weakens."""
        self.unit()
        self.dispatch()
        path = findings_mod.path_for(self.layout, self.slug, "01-api")
        ledger = findings_mod.load(self.layout, self.slug, "01-api")
        ledger.add("critical", "hand written by the reviewer")
        self.cli("findings", "01-api")  # ctx observes it, and seals it
        path.unlink()
        self.cli("findings", "01-api")  # and cannot unsee it
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("finding [1]", out)

    def test_a_minor_finding_is_not_part_of_the_contract(self):
        """Positive control: only blocking severities are sealed against
        deletion — a minor note never blocked anything, so tidying one away is
        not forgery."""
        self.unit()
        self.dispatch()
        path = self.raise_finding("minor", "spelling")
        path.unlink()
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")


# --------------------------------------------------------------------------- #
# the digest itself
# --------------------------------------------------------------------------- #

class TestDigestSurface(GateFixture):

    def unit_object(self, name="01-api"):
        return plan_mod.find_unit(self.layout, self.slug, name)

    def test_the_digest_covers_the_promise_and_not_the_status(self):
        self.unit()
        before = contract_mod.digest(self.unit_object())
        self.edit(status="running", budget_tokens=99999, model="haiku")
        self.assertEqual(contract_mod.digest(self.unit_object()), before,
                         "status, budget and model are not the promise")
        for change in ({"verify": []}, {"owns": ["x"]}, {"forbid": ["y"]},
                       {"depends_on": ["00-x"]}, {"reads": ["z"]},
                       {"verified": ["rubric"]}):
            self.unit()  # rewrite the file from scratch each time
            baseline = contract_mod.digest(self.unit_object())
            self.edit(**change)
            self.assertNotEqual(
                contract_mod.digest(self.unit_object()), baseline,
                f"{list(change)[0]} must be part of the digest",
            )

    def test_compare_names_every_field_that_moved(self):
        self.unit(owns=["src/a.py"])
        self.dispatch()
        self.edit(owns=["src/b.py"], forbid=["src/c.py"])
        ok, changed = contract_mod.compare(
            self.layout, self.slug, self.unit_object()
        )
        self.assertFalse(ok)
        self.assertIn("owns", changed)
        self.assertIn("forbid", changed)

    def test_the_seal_is_written_at_dispatch_and_is_machine_local(self):
        self.unit()
        self.dispatch()
        path = contract_mod.seal_path(self.layout, self.slug, "01-api")
        self.assertTrue(path.is_file())
        self.assertIn(str(self.layout.runtime), str(path),
                      "runtime/ is gitignored: a seal that travels with the "
                      "repository is a seal the runner can rewrite and commit")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["digest"], contract_mod.digest(self.unit_object()))


if __name__ == "__main__":
    unittest.main()
