"""`ctx merge` is a done-transition too, and its preflight was the way around.

Two findings, both in `worktree.merge`'s step 4.

**The all-ERROR bypass survived here.** `ctx unit --status done` now refuses a
gate in which no result reached PASS: an ERROR means *this check did not run*,
and with `PROFILES["code"]` carrying only `cmd` checks, a machine that has never
run `ctx trust` errors every check. The merge preflight treated the same verdict
as a warning, merged, and then set `status="done"` — so on the default
configuration `ctx merge` took a unit to `done` with zero checks executed,
whatever the done-gate said.

**The PENDING refusal was a hollow guard.** Mutating `if verdict ==
verify.PENDING:` to `if False:` survived the entire suite: `PENDING` and
`sign-off` appear nowhere in `test_worktree.py` or `test_merge_safety.py`, while
FAIL and ERROR at the same call site are both covered. The judged half of the
gate — the half no command can decide — was guarded by code nobody was watching.
It was flagged as untested by `PRODUCTION-AUDIT.md` two minor versions before
this file existed.

Every refusal below is paired with a positive control: a guard that fires for
everything is worth no more than one that fires for nothing, so each is also
shown *not* firing on the legitimate version of the same merge.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    contract, frontmatter, plan as plan_mod, worktree as wt,
)
from support import OK, Fixture  # noqa: E402


class MergePreflightFixture(Fixture):
    """A real git repository with a one-unit plan, dispatched to a worktree.

    Deliberately *not* the fixture in `test_worktree.py`: that one accepts every
    check it writes, and half of what is pinned here is what happens on a ledger
    whose commands have never been accepted at all. `accept` is the switch.
    """

    slug = "auth"

    def setUp(self):
        super().setUp()
        self.git_init()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, check=True,
        )

    def unit(self, name="01-a", *, owns=("src/a.py",), checks=None,
             accept=None, **extra):
        """Write a unit file. `accept` is the checks to run `ctx trust` over —
        `None` means all of them, `()` means a ledger nobody has accepted."""
        checks = [{"kind": "cmd", "run": OK}] if checks is None else list(checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        self.unit_path = directory / f"{name}.md"
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "session",
            "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
            "budget_tokens": 1000, "status": "pending", "verify": checks,
        }
        meta.update(extra)
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(self.unit_path)
        self.trust(checks if accept is None else list(accept))
        return self.unit_path

    def plan_ready(self):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 0, out)
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")

    def dispatched(self, name="01-a", relative="src/a.py", **kwargs):
        """A unit with a worktree holding one commit, and a clean root tree."""
        self.unit(name, owns=(relative,), **kwargs)
        self.plan_ready()
        _path, branch, created, error = wt.create(self.layout, self.slug, name)
        self.assertEqual(error, "")
        self.assertTrue(created)
        tree = wt.path_for(self.layout, self.slug, name)
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", f"work {name}", cwd=tree)
        return branch

    def merge(self, name="01-a", **kwargs):
        return wt.merge(self.layout, self.config, self.slug, name, **kwargs)

    def seal(self, name="01-a"):
        """What `ctx start` records at dispatch, without the dispatch.

        `contract.seal` is the whole of what the two guards compare against: a
        seal for the unit (step 0) and the per-field digests inside it (step 1).
        """
        unit = plan_mod.find_unit(self.layout, self.slug, name)
        self.assertIsNotNone(unit, f"no unit {name}")
        return contract.seal(self.layout, self.config, self.slug, unit)

    def rewrite_meta(self, name="01-a", **fields):
        """Edit a unit's frontmatter after it was dispatched — the forgery."""
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(fields)
        doc.write(path)
        return path

    def branch_exists(self, branch):
        return subprocess.run(
            ["git", "rev-parse", "--verify", branch], cwd=str(self.root),
            capture_output=True, text=True,
        ).returncode == 0

    def assert_not_merged(self, branch, relative="src/a.py"):
        """The work did not land, the branch still holds it, and the unit file
        does not claim otherwise."""
        self.assertTrue(self.branch_exists(branch), "the branch was deleted")
        self.assertFalse(
            (self.root / relative).exists(), f"{relative} landed in the root tree"
        )
        self.assertTrue(
            wt.path_for(self.layout, self.slug, "01-a").exists(), "the worktree was removed"
        )
        text = self.unit_path.read_text(encoding="utf-8")
        self.assertNotIn("status: done", text)
        self.assertEqual(frontmatter.read(self.unit_path).meta["status"], "pending")

    def assert_merged(self, branch, relative="src/a.py"):
        self.assertFalse(self.branch_exists(branch), "the branch survived a merge")
        self.assertTrue((self.root / relative).is_file(), "the work did not land")
        self.assertEqual(frontmatter.read(self.unit_path).meta["status"], "done")


# --------------------------------------------------------------------------- #
# (a) an ungated merge is not a done merge
# --------------------------------------------------------------------------- #

class TestNoCheckRan(MergePreflightFixture):
    """Criteria 1-4: the all-ERROR verdict, on the configuration that produces
    it in the field."""

    UNTRUSTED = [
        {"kind": "cmd", "run": "ctx-lint --strict"},
        {"kind": "cmd", "run": "ctx-test --all"},
    ]

    def test_a_unit_whose_commands_were_never_accepted_cannot_be_merged(self):
        """Criteria 1 and 2. Default configuration, end to end: every check is
        `cmd`, the ledger has accepted nothing, so nothing runs."""
        branch = self.dispatched(checks=self.UNTRUSTED, accept=())

        ok, messages = self.merge()

        self.assertFalse(ok, messages)
        joined = "\n".join(messages)
        self.assertIn("not one check could run", joined)
        self.assertIn("ungated is not done", joined)
        self.assertIn("ctx trust", joined, "the configuration problem is named")
        self.assert_not_merged(branch)

    def test_the_same_unit_merges_once_its_commands_are_accepted(self):
        """Positive control for criteria 1 and 2: the refusal is about *nothing
        ran*, not about these checks."""
        branch = self.dispatched(checks=self.UNTRUSTED[:1] + [{"kind": "cmd", "run": OK}],
                                 accept=[{"kind": "cmd", "run": OK}])
        # One command is still unaccepted — but one reached PASS, so the gate is
        # not blind and the old warning behaviour applies.
        ok, messages = self.merge()

        self.assertTrue(ok, messages)
        self.assert_merged(branch)

    def test_an_error_beside_a_pass_still_merges_with_a_warning(self):
        """Criterion 3: the refusal is for *no check reached PASS*, never for
        *any check errored*. Widening it would block every merge on a machine
        missing one optional tool."""
        branch = self.dispatched(
            checks=[{"kind": "cmd", "run": OK}, {"kind": "cmd", "run": "ctx-lint"}],
            accept=[{"kind": "cmd", "run": OK}],
        )

        ok, messages = self.merge()

        self.assertTrue(ok, messages)
        joined = "\n".join(messages)
        self.assertIn("not every check could run", joined)
        self.assertIn("the gate signed nothing", joined)
        self.assert_merged(branch)

    def test_skip_gate_still_overrides_and_says_what_it_overrode(self):
        """Criterion 4: the escape hatch survives the fix — and an override that
        records nothing about what it stepped over is the trace that matters."""
        branch = self.dispatched(checks=self.UNTRUSTED, accept=())

        ok, messages = self.merge(skip_gate=True)

        self.assertTrue(ok, messages)
        joined = "\n".join(messages)
        self.assertIn("--skip-gate", joined)
        self.assertIn("2 verify check(s) were not run", joined)
        self.assertIn("ctx-lint --strict", joined, "the skipped check is named")
        self.assert_merged(branch)

    def test_a_gate_that_could_not_run_leaves_a_second_attempt_possible(self):
        """The refusal must be recoverable: accepting the commands and re-running
        is all it asks for, and the worktree is still there to merge from."""
        # Runnable commands this ledger has simply never accepted — the
        # `ctx trust` half of the ERROR, without the missing-binary half.
        checks = [{"kind": "cmd", "run": OK}, {"kind": "cmd", "run": self.py("x = 1")}]
        branch = self.dispatched(checks=checks, accept=())
        ok, messages = self.merge()
        self.assertFalse(ok, messages)

        self.trust(checks)
        ok, messages = self.merge()

        self.assertTrue(ok, messages)
        self.assert_merged(branch)


# --------------------------------------------------------------------------- #
# (b) the judged half — mutation survivor #2
# --------------------------------------------------------------------------- #

class TestPendingSignOff(MergePreflightFixture):
    """Criterion 5. Deleting `if verdict == verify.PENDING:` from the preflight
    must fail a test; before this class it failed none of 625."""

    RUBRIC = [
        {"kind": "cmd", "run": OK},
        {"kind": "rubric", "about": "the work reads as sound"},
    ]

    def test_an_unsigned_rubric_check_refuses_the_merge(self):
        """The mechanical half passes; the judged half nobody has signed is the
        whole reason this merge must not land."""
        branch = self.dispatched(checks=self.RUBRIC,
                                 accept=[{"kind": "cmd", "run": OK}])

        ok, messages = self.merge()

        self.assertFalse(ok, messages)
        joined = "\n".join(messages)
        self.assertIn("sign-off", joined, "the refusal must name sign-off")
        self.assertIn("01-a", joined)
        self.assert_not_merged(branch)

    def test_a_signed_rubric_check_merges(self):
        """Positive control for criterion 5: the guard is about the *unsigned*
        judged check, not about judged checks existing."""
        branch = self.dispatched(checks=self.RUBRIC, verified=["rubric"],
                                 accept=[{"kind": "cmd", "run": OK}])

        ok, messages = self.merge()

        self.assertTrue(ok, messages)
        self.assertIn("gate passed", "\n".join(messages))
        self.assert_merged(branch)

    def test_a_pending_verdict_beats_a_configuration_error(self):
        """A ledger that also accepted nothing must still be refused *for the
        sign-off*, not merged past it — PENDING outranks ERROR."""
        branch = self.dispatched(checks=self.RUBRIC, accept=())

        ok, messages = self.merge()

        self.assertFalse(ok, messages)
        self.assertIn("sign-off", "\n".join(messages))
        self.assert_not_merged(branch)


# --------------------------------------------------------------------------- #
# (c) the two guards that come before any check is worth running
# --------------------------------------------------------------------------- #

class TestTheGateGuardsApplyToMergeToo(MergePreflightFixture):
    """`ctx merge` is a done-transition, and it was the way around `gate_check`.

    `merge` reimplemented a *subset* of the done-gate: it called `verify.run`
    and handled FAIL/PENDING/blind-ERROR itself, but never called `gate_check`,
    whose only other caller is `ctx unit --status done`. Two refusals lived only
    there, and a merge walked past both:

    * **step 0, the dispatch seal.** A unit nothing dispatched has no recorded
      contract, no review baseline and no dispatch commit — three of the gate's
      four guarantees absent at once.
    * **step 1, contract-intact.** The `stray` check above is *not* this check.
      It validates the branch's diff against whatever `owns` says **now**; this
      one asks whether `owns` is still what was sealed. A unit that widens its
      own `owns` and then stays inside it passes the first and fails this.
    """

    def sibling(self, name="02-b", owns=("src/b.py",)):
        """A second unit in the plan, so this plan is one that seals at all."""
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        checks = [{"kind": "cmd", "run": OK}]
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "session",
             "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": "pending", "verify": checks},
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / f"{name}.md")
        return name

    # -- step 0 ------------------------------------------------------------- #

    def test_a_unit_with_no_dispatch_seal_cannot_be_merged(self):
        """Criterion 1. The siblings were dispatched through `ctx start`; this
        one was sent out around the ledger, and used to merge anyway."""
        self.sibling()
        branch = self.dispatched()
        self.seal("02-b")

        ok, messages = self.merge()

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("no dispatch seal", joined)
        self.assertIn("ctx start", joined, "it names what records one")
        self.assert_not_merged(branch)

    def test_the_same_unit_merges_once_it_has_one(self):
        """Positive control for criterion 1: the refusal is about the *missing*
        seal, not about sealing being involved."""
        self.sibling()
        branch = self.dispatched()
        self.seal("02-b")
        self.seal("01-a")

        ok, messages = self.merge()

        self.assertTrue(ok, "\n".join(messages))
        self.assert_merged(branch)

    def test_a_plan_that_seals_nothing_at_all_still_merges(self):
        """Scoped exactly as `gate_check` scopes it. A plan where *no* unit has
        a seal predates sealing; refusing there would brick every in-flight plan
        on upgrade, which is the reason `baseline` returning None is tolerated."""
        branch = self.dispatched()

        ok, messages = self.merge()

        self.assertTrue(ok, "\n".join(messages))
        self.assert_merged(branch)

    # -- step 1 ------------------------------------------------------------- #

    def test_a_contract_edited_after_dispatch_cannot_be_merged(self):
        """Criterion 2, and the case the `stray` check cannot see: `owns` is
        widened after dispatch, and the branch's diff then sits comfortably
        inside the widened version."""
        branch = self.dispatched()
        self.seal()
        self.rewrite_meta(owns=["src/a.py", "src/anything-else.py"])

        ok, messages = self.merge()

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("contract changed after it was dispatched", joined)
        self.assertIn("owns", joined, "the changed field is named")
        self.assertNotIn(
            "outside its `owns` scope", joined,
            "the diff *was* inside `owns` — that is the whole point",
        )
        self.assert_not_merged(branch)

    def test_editing_the_verify_block_after_dispatch_is_caught_too(self):
        """`owns` is not special: the promise is every sealed field. Swapping a
        failing check for a passing one after dispatch is the version of this
        that matters most."""
        branch = self.dispatched(
            checks=[{"kind": "cmd", "run": self.py("import sys; sys.exit(1)")}],
        )
        self.seal()
        self.trust([{"kind": "cmd", "run": OK}])
        self.rewrite_meta(verify=[{"kind": "cmd", "run": OK}])

        ok, messages = self.merge()

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("contract changed after it was dispatched", joined)
        self.assertIn("verify", joined)
        self.assert_not_merged(branch)

    def test_a_re_sealed_contract_merges(self):
        """Positive control for criterion 2: a widening that was re-recorded at
        dispatch time is a planning decision, not a forgery."""
        branch = self.dispatched()
        self.seal()
        self.rewrite_meta(owns=["src/a.py", "src/anything-else.py"])
        self.seal()

        ok, messages = self.merge()

        self.assertTrue(ok, "\n".join(messages))
        self.assert_merged(branch)

    def test_the_branch_diff_check_survives_as_a_second_guard(self):
        """Criterion 2's other half. Contract-intact is stricter than the
        `stray` check but does not replace it: a unit whose contract is
        untouched and whose branch wrote outside `owns` is still refused."""
        branch = self.dispatched()
        self.seal()
        tree = wt.path_for(self.layout, self.slug, "01-a")
        (tree / "src" / "elsewhere.py").write_text("nope\n", encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "stray", cwd=tree)

        ok, messages = self.merge()

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("outside its `owns` scope", joined)
        self.assertIn("src/elsewhere.py", joined)
        self.assert_not_merged(branch)

    # -- ordering ----------------------------------------------------------- #

    def test_the_guards_are_added_in_front_of_the_check_refusals(self):
        """Criterion 3. A sealed, intact unit whose checks fail is still refused
        for the checks — the two guards were put in front of that handling, not
        in place of it."""
        branch = self.dispatched(
            checks=[{"kind": "cmd", "run": self.py("import sys; sys.exit(1)")}],
        )
        self.seal()

        ok, messages = self.merge()

        joined = "\n".join(messages)
        self.assertFalse(ok, joined)
        self.assertIn("the done-gate failed", joined)
        self.assert_not_merged(branch)

    def test_skip_gate_overrides_the_guards_and_says_it_did(self):
        """`--skip-gate` is `merge`'s `--force`, and it overrides what
        `--force` overrides. An escape hatch that records nothing about what it
        stepped over is exactly the trace that matters later."""
        self.sibling()
        branch = self.dispatched()
        self.seal("02-b")

        ok, messages = self.merge(skip_gate=True)

        joined = "\n".join(messages)
        self.assertTrue(ok, joined)
        self.assertIn("dispatch-seal and contract-intact guards were skipped",
                      joined)
        self.assert_merged(branch)


# --------------------------------------------------------------------------- #
# (d) the done-write itself
# --------------------------------------------------------------------------- #

class TestTheDoneWriteTakesThePlanLock(MergePreflightFixture):
    """Criterion 4. `merge` ended in a bare `unit.set(status="done")`.

    A `Unit` is a whole document held in memory and `Unit.set` writes that whole
    document back, so its value is only as fresh as the moment it was parsed —
    here, before a gate that may have spent minutes running commands. Every
    field another writer added in that window was carried away with the stale
    copy. `_set_unit_status` exists for exactly this, and holds the plan lock
    across a re-read; `merge` was the one done-transition not using it.
    """

    def stamping_check(self):
        """A verify command that writes a new frontmatter key into the unit file
        while the gate is running — a stand-in for the concurrent writer
        (`worktree._record_fork_point`, a sibling process) that the bare
        `unit.set` erased."""
        script = self.layout.runtime / "stamp.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import pathlib\n"
            f"path = pathlib.Path({str(self.unit_path)!r})\n"
            "out, fences = [], 0\n"
            "for line in path.read_text(encoding='utf-8').splitlines(True):\n"
            "    if line.strip() == '---':\n"
            "        fences += 1\n"
            "        if fences == 2:\n"
            "            out.append('reviewer: alice\\n')\n"
            "    out.append(line)\n"
            "path.write_text(''.join(out), encoding='utf-8')\n",
            encoding="utf-8",
        )
        return f'"{sys.executable}" "{script}"'

    def test_a_field_written_during_the_gate_survives_the_done_write(self):
        branch = self.dispatched()
        check = {"kind": "cmd", "run": self.stamping_check()}
        self.trust([check])
        self.rewrite = frontmatter.read(self.unit_path)
        self.rewrite.meta["verify"] = [check]
        self.rewrite.write(self.unit_path)

        ok, messages = self.merge()

        self.assertTrue(ok, "\n".join(messages))
        meta = frontmatter.read(self.unit_path).meta
        self.assertEqual(meta["status"], "done", "the gate's verdict still wins")
        self.assertEqual(
            meta.get("reviewer"), "alice",
            "the write that landed during the gate was erased by the stale "
            "document `merge` had been holding since before it ran",
        )
        self.assert_merged(branch)

    def test_the_done_write_goes_through_set_unit_status(self):
        """The mechanism, named. The behaviour above is what matters, but it
        holds only because the plan lock spans the re-read — and a future edit
        that reverts to `unit.set` while keeping the field would be a silent
        loss of that lock."""
        from ctx import commands

        branch = self.dispatched()
        seen = []
        original = commands._set_unit_status

        def spy(layout, slug, units, status):
            seen.append((slug, [unit.name for unit in units], status))
            return original(layout, slug, units, status)

        commands._set_unit_status = spy
        try:
            ok, messages = self.merge()
        finally:
            commands._set_unit_status = original

        self.assertTrue(ok, "\n".join(messages))
        self.assertEqual(seen, [(self.slug, ["01-a"], "done")])
        self.assert_merged(branch)


if __name__ == "__main__":
    unittest.main()
