"""The routes *around* the gate, rather than the gate itself.

Wave 1 made the done-gate hard to walk through. These are the four doors beside
it, all found by using the tool on itself:

**Re-sealing an edited contract.** The seal is what makes "the contract changed
after dispatch" detectable, and `ctx start --rebaseline` re-recorded it. A
dispatched runner holds Bash, so it could edit its own `verify` block and then
re-seal the edit as the promise. `--rebaseline` now retakes the *review
baseline* only and refuses when the promise itself moved; `--reseal` is the
distinct, journalled door for accepting a new contract.

**No seal at all.** The seal exists only if the orchestrator dispatched through
`ctx start`. A direct Task call left the unit unsealed, and nothing said so —
not the board, not the gate, which then had no contract, no baseline and no
dispatch commit to check anything against and reported that as a pass.

**Committing hid the edit.** `verify.changed_files` shells out to `git status`,
which by construction only ever sees the working tree. `git commit` therefore
erased an out-of-scope change from the one check meant to police it. The gate
now diffs from the commit recorded in the dispatch seal, so committing hides
nothing — and a seal pointing at a commit that was amended or rebased away
fails loudly rather than passing by default.

**Siblings failed each other.** Concurrent `subagent` units share one working
tree, so a sibling's write to its own declared path sat in `git status` while
this unit's gate ran, and failed it. The standing workaround was a manual `git
stash` dance, which has cost time in two separate sessions. The scope check is
now widened by exactly what concurrency costs it: the `owns` of the siblings
running *alongside* this unit, and nothing else. The tests that matter here are
the ones proving it was not simply switched off — a path nobody declared, a
sibling that is already `done`, and a unit in another wave are all still
violations.

Everything in this file uses a real git repository, because every one of these
findings is about what git can and cannot see.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    contract as contract_mod, frontmatter, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
DIFF = [{"kind": "diff"}]


class GateBypassFixture(Fixture):
    """A real repository, a plan whose units can be dispatched, worked and gated."""

    slug = "routes"

    def setUp(self):
        super().setUp()
        self.git_init()

    # -- building a plan --------------------------------------------------- #

    def unit(self, name="01-api", *, owns=None, checks=None, wave=1,
             depends_on=(), body=CRITERIA):
        checks = list(DIFF if checks is None else checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": list(depends_on),
                "owns": list(owns or [f"src/{name}.py"]), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "wave": wave, "verify": checks,
            },
            body,
        ).write(directory / f"{name}.md")
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return directory / f"{name}.md"

    def edit(self, name="01-api", body=None, **changes):
        """Edit a unit file the way a runner holding `Write` would."""
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        if body is not None:
            doc.body = body
        doc.write(path)
        return path

    # -- driving it -------------------------------------------------------- #

    def start(self, *extra):
        return self.cli("start", *extra)

    def dispatch(self, *extra):
        code, out = self.start(*extra)
        self.assertEqual(code, 0, out)
        return out

    def done(self, name="01-api", *extra):
        return self.cli("unit", name, "--status", "done", *extra)

    def find(self, name="01-api"):
        return plan_mod.find_unit(self.layout, self.slug, name)

    def status_of(self, name="01-api"):
        return self.find(name).status

    def seal(self, name="01-api"):
        return contract_mod.load_seal(self.layout, self.slug, name)

    def journal_text(self):
        return "\n".join(path.read_text(encoding="utf-8")
                         for path in sorted(self.layout.journal.glob("*.md")))

    # -- git --------------------------------------------------------------- #

    def git(self, *args):
        completed = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True,
            env=dict(self._env, GIT_CONFIG_GLOBAL="/dev/null",
                     GIT_CONFIG_SYSTEM="/dev/null",
                     GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
                     GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com"),
        )
        self.assertEqual(completed.returncode, 0,
                         f"git {' '.join(args)}: {completed.stderr}")
        return completed.stdout.strip()

    def commit(self, message="work"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# 1 & 2 — --rebaseline refuses a changed contract, --reseal accepts one
# --------------------------------------------------------------------------- #

class TestRebaselineWillNotReseal(GateBypassFixture):
    """Finding 1. A baseline retake is not a re-seal, and the runner that would
    most like it to be is the one that just edited its own contract."""

    def refused(self, **changes):
        self.unit()
        self.dispatch()
        before = self.seal()["digest"]
        self.edit(**changes)
        code, out = self.start("--rebaseline", "01-api")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.seal()["digest"], before,
                         "the seal is exactly what must not move")
        return out

    def test_a_changed_verify_block_is_refused_and_named(self):
        out = self.refused(verify=[{"kind": "cmd", "run": OK}])
        self.assertIn("REFUSED", out)
        self.assertIn("contract changed: verify", out)

    def test_a_widened_owns_is_refused_and_named(self):
        out = self.refused(owns=["src/01-api.py", "src/anything.py"])
        self.assertIn("contract changed: owns", out)

    def test_a_trimmed_criteria_body_is_refused_and_named(self):
        self.unit(body="## Objective\nDo it.\n\n## Acceptance criteria\n"
                       "1. the parser round-trips\n2. the CLI reports it\n")
        self.dispatch()
        self.edit(body="## Objective\nDo it.\n\n## Acceptance criteria\n"
                       "1. the parser round-trips\n")
        code, out = self.start("--rebaseline", "01-api")
        self.assertEqual(code, 2, out)
        self.assertIn("contract changed: acceptance criteria", out)

    def test_the_refusal_names_reseal_as_the_route_that_would_work(self):
        out = self.refused(owns=["src/01-api.py", "src/anything.py"])
        self.assertIn("ctx start --reseal 01-api", out)

    def test_a_refused_rebaseline_dispatches_nothing(self):
        out = self.refused(owns=["src/01-api.py", "src/anything.py"])
        self.assertIn("Nothing was dispatched", out)
        self.assertNotIn("Dispatch these", out)

    def test_the_refusal_is_journalled_with_the_field_that_moved(self):
        self.refused(owns=["src/01-api.py", "src/anything.py"])
        text = self.journal_text()
        self.assertIn("--rebaseline refused", text)
        self.assertIn("01-api", text)
        self.assertIn("owns", text)

    def test_an_unchanged_contract_is_still_rebaselined(self):
        """The positive control. A retake is a real need — a crashed session,
        a snapshot taken over half-finished work — and refusing every retake
        would be a fix that removed the feature."""
        self.unit()
        self.dispatch()
        self.write("src/01-api.py", "x = 2\n")
        code, out = self.start("--rebaseline", "01-api")
        self.assertEqual(code, 0, out)
        self.assertIn("re-baselined 01-api", out)

    def test_flipping_status_alone_does_not_block_a_retake(self):
        """`status:` is the one field a runner is supposed to move, so it is
        outside the digest — and a retake must not trip on it."""
        self.unit()
        self.dispatch()
        self.edit(status="running")
        code, out = self.start("--rebaseline", "01-api")
        self.assertEqual(code, 0, out)


class TestReseal(GateBypassFixture):
    """Finding 1, the other half: accepting a changed contract is a planning
    decision, so it has its own flag and leaves its own trail."""

    def changed(self):
        self.unit()
        self.dispatch()
        self.edit(owns=["src/01-api.py", "src/extra.py"])
        return self.seal()["digest"]

    def test_reseal_accepts_the_contract_as_it_now_stands(self):
        before = self.changed()
        code, out = self.start("--reseal", "01-api")
        self.assertEqual(code, 0, out)
        self.assertNotEqual(self.seal()["digest"], before, "re-sealed")
        self.assertEqual(self.seal()["digest"], contract_mod.digest(self.find()),
                         "against the unit exactly as it reads now")

    def test_a_resealed_unit_can_then_be_marked_done(self):
        self.changed()
        self.dispatch("--reseal", "01-api")
        self.write("src/01-api.py", "x = 1\n")
        code, out = self.done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_the_journal_records_which_fields_changed(self):
        self.changed()
        self.dispatch("--reseal", "01-api")
        text = self.journal_text()
        self.assertIn("re-sealed deliberately (--reseal)", text)
        self.assertIn("01-api", text)
        self.assertIn("owns", text, "which field moved is the point of the entry")

    def test_the_terminal_says_so_too(self):
        self.changed()
        out = self.dispatch("--reseal", "01-api")
        self.assertIn("re-sealed 01-api", out)
        self.assertIn("owns", out)

    def test_reseal_is_never_implied_by_rebaseline(self):
        """The two flags are one keystroke apart in intent and a world apart in
        consequence, so this is asserted directly rather than inferred."""
        before = self.changed()
        self.assertEqual(self.start("--rebaseline", "01-api")[0], 2)
        self.assertEqual(self.seal()["digest"], before)
        self.assertEqual(self.start("--reseal", "01-api")[0], 0)
        self.assertNotEqual(self.seal()["digest"], before)

    def test_a_name_that_is_not_in_the_wave_is_said_out_loud(self):
        self.unit()
        self.dispatch()
        out = self.dispatch("--reseal", "99-nope")
        self.assertIn("99-nope", out)
        self.assertIn("not a unit of wave 1", out)


# --------------------------------------------------------------------------- #
# 3 & 4 — a unit that was never sealed
# --------------------------------------------------------------------------- #

class TestUnsealedUnits(GateBypassFixture):
    """Finding 2. `ctx start` is the only thing that writes a seal, so a unit
    without one was dispatched around the ledger — by a direct Task call."""

    def wave_one_dispatched(self):
        """Wave 1 out through `ctx start`; `02-late` in wave 2, never started —
        which is the state a direct Task call leaves behind."""
        self.unit("01-api", owns=["src/api.py"], wave=1)
        self.unit("02-late", owns=["src/late.py"], wave=2, depends_on=["01-api"])
        self.dispatch()
        return self.find("02-late")

    def test_done_is_refused_for_a_unit_with_no_seal(self):
        self.wave_one_dispatched()
        code, out = self.done("02-late")
        self.assertEqual(code, 1, out)
        self.assertIn("no dispatch seal", out)
        self.assertNotEqual(self.status_of("02-late"), "done")

    def test_the_refusal_names_ctx_start_as_the_route_that_records_one(self):
        self.wave_one_dispatched()
        _code, out = self.done("02-late")
        self.assertIn("ctx start", out)

    def test_the_refusal_says_what_is_missing_rather_than_only_that_it_is(self):
        self.wave_one_dispatched()
        _code, out = self.done("02-late")
        for missing in ("contract", "review baseline", "commit"):
            self.assertIn(missing, out)

    def test_force_still_overrides_it(self):
        self.wave_one_dispatched()
        code, out = self.done("02-late", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("02-late"), "done")

    def test_the_refusal_is_journalled(self):
        self.wave_one_dispatched()
        self.done("02-late")
        self.assertIn("done refused (no dispatch seal)", self.journal_text())

    def test_a_plan_that_has_never_sealed_anything_is_not_refused(self):
        """The upgrade case, and the reason this is scoped to plans that seal
        at all: a plan dispatched before seals existed has none, and failing
        closed there bricks every in-flight plan the moment this ships."""
        self.unit("01-api", owns=["src/api.py"])
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertIn("no dispatch baseline", out)

    def test_a_sealed_unit_is_unaffected(self):
        """The positive control: the refusal is a guard, not a new blanket no."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "x = 1\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)


class TestUnsealedIsVisibleOnTheBoard(GateBypassFixture):
    """Criterion 4. The gap has to be visible *before* the gate, or the first
    anyone hears of it is a refusal at the end of the work."""

    def dispatched_pair(self):
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"])
        self.dispatch()
        # Stands in for the unit the orchestrator sent out with a direct Task
        # call: its siblings went through `ctx start`, it did not, so the wave
        # is dispatched and this one has nothing recorded against it.
        contract_mod.discard(self.layout, self.slug, "02-store")

    def test_the_wave_board_calls_it_unsealed_rather_than_pending(self):
        self.dispatched_pair()
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        line = next(line for line in out.splitlines() if "02-store" in line)
        self.assertIn("unsealed", line)

    def test_the_sealed_sibling_reads_normally(self):
        self.dispatched_pair()
        _code, out = self.cli("status")
        line = next(line for line in out.splitlines() if "01-api" in line)
        self.assertNotIn("unsealed", line)
        self.assertIn("in flight", line)

    def test_the_board_says_what_to_do_about_it(self):
        self.dispatched_pair()
        _code, out = self.cli("status")
        self.assertIn("ctx start", out)

    def test_the_unit_listing_flags_it_too(self):
        """`ctx unit` with no name prints the same list from another code path."""
        self.dispatched_pair()
        _code, out = self.cli("unit")
        self.assertIn("unsealed", out)

    def test_a_wave_that_has_not_gone_out_is_still_plainly_pending(self):
        """The control against noise: every unit is unsealed before dispatch,
        and flagging those would make the word mean nothing."""
        self.unit("01-api", owns=["src/api.py"], wave=1)
        self.unit("02-late", owns=["src/late.py"], wave=2, depends_on=["01-api"])
        _code, out = self.cli("status")
        self.assertNotIn("unsealed", out)


# --------------------------------------------------------------------------- #
# 5 & 6 — committing no longer hides an out-of-scope edit
# --------------------------------------------------------------------------- #

class TestCommittingDoesNotHideAnEdit(GateBypassFixture):
    """Finding 3. `git status` sees the working tree and nothing else, so
    `git commit` used to erase a scope violation from the check that exists to
    catch it."""

    def worked(self, *, stray=True, commit=True):
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "def api():\n    return 1\n")
        if stray:
            self.write("src/nobody.py", "sneaky = True\n")
        if commit:
            self.commit("the unit's work")
        return self.find("01-api")

    def test_a_committed_out_of_scope_edit_still_fails_the_gate(self):
        """The whole finding in one test: the edit is *committed*, so
        `git status` is clean and every check that reads it sees nothing."""
        self.worked()
        self.assertEqual(
            self.git("status", "--porcelain", "--untracked-files=all"), "",
            "the working tree really is clean — otherwise this proves nothing",
        )
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("changed outside owned scope", out)
        self.assertIn("src/nobody.py", out)
        self.assertNotEqual(self.status_of("01-api"), "done")

    def test_the_same_edit_left_uncommitted_fails_as_it_always_did(self):
        self.worked(commit=False)
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("src/nobody.py", out)

    def test_a_committed_in_scope_edit_passes(self):
        """The positive control: reading history must not turn every commit
        into a violation."""
        self.worked(stray=False, commit=True)
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-api"), "done")

    def test_the_dispatch_seal_records_the_commit_it_started_from(self):
        head = self.head()
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.assertEqual(self.seal("01-api")["commit"], head)

    def test_work_committed_and_then_reverted_is_not_a_violation(self):
        """A tree diff, not a walk of every commit: what a unit put back is not
        something it changed."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/nobody.py", "temporary = True\n")
        self.commit("a detour")
        (self.root / "src/nobody.py").unlink()
        self.commit("and back again")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)


class TestAVanishedDispatchCommit(GateBypassFixture):
    """Criterion 6. History can move under a running unit — an amend, a rebase,
    a reset. The diff then has no honest answer, and a check with no honest
    answer must not sign the work off."""

    def rewritten(self):
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        dispatched_at = self.seal("01-api")["commit"]
        self.write("src/api.py", "x = 1\n")
        self.git("add", "-A")
        # The commit the seal names stops being part of this history.
        self.git("commit", "-q", "--amend", "-m", "rewritten")
        return dispatched_at

    def test_the_gate_fails_and_names_the_commit_it_cannot_find(self):
        dispatched_at = self.rewritten()
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn(dispatched_at, out, "the missing SHA is the diagnosis")
        self.assertNotEqual(self.status_of("01-api"), "done")

    def test_it_fails_rather_than_erroring_into_a_pass(self):
        """An ERROR beside one passing check is a warning the gate walks past.
        A dispatch point that no longer exists is not infrastructure noise: it
        is the check being unable to establish what the unit did, which is a
        refusal."""
        self.rewritten()
        _code, out = self.done("01-api")
        self.assertIn("FAIL", out.upper())
        self.assertIn("amended, rebased or", out)

    def test_it_does_not_crash(self):
        """Exit 2 would mean an unhandled exception reached `main`."""
        self.rewritten()
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("failed:", out)

    def test_the_refusal_names_the_way_out(self):
        self.rewritten()
        _code, out = self.done("01-api")
        self.assertIn("--reseal", out)

    def test_reseal_re_records_the_dispatch_point(self):
        """And the way out really is a way out."""
        self.rewritten()
        self.dispatch("--reseal", "01-api")
        self.assertEqual(self.seal("01-api")["commit"], self.head())
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)

    def test_ordinary_commits_on_top_keep_the_gate_passing(self):
        """The control: committing normally is not history moving."""
        self.unit("01-api", owns=["src/api.py"])
        self.dispatch()
        self.write("src/api.py", "x = 1\n")
        self.commit("work")
        self.write("src/api.py", "x = 2\n")
        self.commit("more work")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- #
# 7 & 8 — a concurrent sibling is not this unit's scope violation
# --------------------------------------------------------------------------- #

class TestConcurrentSiblings(GateBypassFixture):
    """Finding 4. Two units of one wave share one working tree. Checking this
    unit's delta against its own `owns` alone made every sibling's legitimate
    write a violation, and the workaround was to `git stash` the sibling's work
    around the gate."""

    def wave_of_two(self):
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"])
        self.dispatch()

    def test_a_siblings_dirty_file_is_not_this_units_violation(self):
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")   # the sibling, mid-flight
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-api"), "done")

    def test_a_siblings_committed_file_is_not_this_units_violation_either(self):
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        self.commit("both units so far")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)

    def test_a_path_nobody_in_the_wave_owns_is_still_a_violation(self):
        """Asserted in the same wave as the clean case above, so a scope
        expansion that excused everything would fail this file rather than
        pass it."""
        self.wave_of_two()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")
        self.write("src/nobody.py", "c = 1\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("src/nobody.py", out)
        self.assertNotIn("src/store.py", out, "the sibling is not the complaint")

    def test_a_done_siblings_path_is_not_excused_once_it_is_actually_committed(self):
        """Criterion 8, narrowed by `07-wave-scope-after-done`.

        The original claim was that a gated sibling is finished, so a later
        change to its paths belongs in a report. Half of that was wrong in
        practice: `ctx unit --status done` runs well before anyone commits, so
        the sibling's own reviewed writes are still sitting dirty in the shared
        tree, and every later unit in the wave failed its `diff` check on them.
        A wave gated one unit at a time deadlocked on itself.

        So the exemption now tracks the work rather than the status: a `done`
        sibling's `owns` stays excused only while it is uncommitted. Once the
        work has landed, this test's original claim holds again exactly as it
        did — which is what is asserted here.
        """
        self.wave_of_two()
        self.write("src/store.py", "b = 1\n")
        self.assertEqual(self.done("02-store")[0], 0, "gated first")
        self.assertEqual(self.status_of("02-store"), "done")
        self.git("add", "-A")
        self.git("commit", "-qm", "land 02-store's work")

        self.write("src/api.py", "a = 1\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("src/store.py", out)

    def test_a_unit_in_another_wave_never_excuses_a_path(self):
        """Criterion 8. A later wave is not running now. Excusing its `owns`
        would degrade the check to "anything any unit in the plan ever
        claimed"."""
        self.unit("01-api", owns=["src/api.py"], wave=1)
        self.unit("02-later", owns=["src/later.py"], wave=2,
                  depends_on=["01-api"])
        self.dispatch()
        self.write("src/api.py", "a = 1\n")
        self.write("src/later.py", "b = 1\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("src/later.py", out)

    def test_an_earlier_waves_owner_does_not_excuse_a_path_either(self):
        self.unit("01-first", owns=["src/first.py"], wave=1)
        self.unit("02-api", owns=["src/api.py"], wave=2, depends_on=["01-first"])
        self.dispatch()
        self.write("src/first.py", "a = 1\n")
        self.assertEqual(self.done("01-first")[0], 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "wave 1 landed")

        self.dispatch()  # wave 2
        self.write("src/api.py", "b = 1\n")
        self.write("src/first.py", "a = 2\n")
        code, out = self.done("02-api")
        self.assertEqual(code, 1, out)
        self.assertIn("src/first.py", out)


class TestTheOtherGateRunnersAgreeWithIt(GateBypassFixture):
    """`ctx verify` and `ctx ci` run the same checks over the same tree. If they
    scoped the `diff` kind differently from `ctx unit --status done`, the manual
    gate would report a violation the real one does not — which is how a wave
    learns to stop trusting its own tools."""

    def wave_of_two(self):
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"])
        self.dispatch()
        self.write("src/api.py", "a = 1\n")
        self.write("src/store.py", "b = 1\n")

    def test_ctx_verify_does_not_fail_a_unit_for_its_siblings_work(self):
        self.wave_of_two()
        self.assertEqual(self.cli("unit", "01-api")[0], 0, "focus it")
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)

    def test_ctx_verify_still_fails_on_a_path_nobody_owns(self):
        self.wave_of_two()
        self.write("src/nobody.py", "c = 1\n")
        self.cli("unit", "01-api")
        code, out = self.cli("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("src/nobody.py", out)

    def test_ctx_verify_sees_a_committed_out_of_scope_edit(self):
        self.wave_of_two()
        self.write("src/nobody.py", "c = 1\n")
        self.commit("hidden in history")
        self.cli("unit", "01-api")
        code, out = self.cli("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("src/nobody.py", out)

    def test_ci_does_not_report_a_wave_in_flight_as_violations(self):
        self.wave_of_two()
        code, out = self.cli("ci", "--plan", self.slug)
        self.assertEqual(code, 0, out)
        self.assertNotIn("changed outside owned scope", out)


class TestNoStashIsNeededToGateAConcurrentWave(GateBypassFixture):
    """Criterion 10, which is the finding stated as the thing a person does.

    Two units, disjoint `owns`, both with work in the tree. Gating one used to
    require stashing the other's changes, gating, and unstashing — a manual
    dance around a check that was wrong, performed under exactly the conditions
    (a wave in flight, several agents writing) where losing a stash is easiest.
    """

    def test_two_dirty_units_and_one_gate_with_no_stash_anywhere(self):
        self.unit("01-api", owns=["src/api.py"])
        self.unit("02-store", owns=["src/store.py"])
        self.dispatch()

        self.write("src/api.py", "def api():\n    return 1\n")
        self.write("src/store.py", "def store():\n    return 2\n")
        dirty = self.git("status", "--porcelain", "--untracked-files=all")
        self.assertIn("src/api.py", dirty)
        self.assertIn("src/store.py", dirty, "both units are dirty at once")

        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("01-api"), "done")

        still_dirty = self.git("status", "--porcelain", "--untracked-files=all")
        self.assertIn("src/store.py", still_dirty,
                      "and the sibling's work was never touched to get there")


# --------------------------------------------------------------------------- #
# 9 — a refusal is not a success
# --------------------------------------------------------------------------- #

class TestRefusalsExitNonZero(GateBypassFixture):
    """Finding 9. `return 0` on a refusal is invisible to every caller that is
    a script, and it slipped past the previous wave's allowlist removal because
    it never raised at all."""

    def test_start_with_nothing_ready_to_dispatch_exits_non_zero(self):
        self.unit("01-a", owns=["src/x.py"])
        self.unit("02-b", owns=["src/x.py"])   # the same path: a collision
        code, out = self.start()
        self.assertNotEqual(code, 0, out)
        self.assertIn("nothing was started", out)
        self.assertNotIn("Dispatch these", out)

    def test_the_reason_survives_the_non_zero_exit(self):
        self.unit("01-a", owns=["src/x.py"])
        self.unit("02-b", owns=["src/x.py"])
        _code, _out, err = self.cli_streams("start")
        self.assertIn("both own", err, "the reason is the output, on stderr")

    def test_a_wave_that_can_be_dispatched_still_exits_zero(self):
        self.unit("01-a", owns=["src/x.py"])
        self.assertEqual(self.start()[0], 0)

    def test_load_with_a_name_that_does_not_resolve_exits_non_zero(self):
        code, out = self.cli("load", "nothing-by-that-name")
        self.assertNotEqual(code, 0, out)
        self.assertIn("no context named", out)

    def test_a_refused_load_puts_nothing_on_stdout(self):
        """`ctx load x > brief.md` used to write the refusal into brief.md."""
        _code, out, err = self.cli_streams("load", "nothing-by-that-name")
        self.assertEqual(out, "")
        self.assertIn("no context named", err)

    def test_a_load_that_does_resolve_still_exits_zero(self):
        self.assertEqual(self.cli("save", "auth-notes")[0], 0)
        code, out = self.cli("load", "auth-notes")
        self.assertEqual(code, 0, out)
        self.assertIn("auth-notes", out)

    def test_merge_with_a_name_that_does_not_resolve_exits_non_zero(self):
        self.unit("01-api", owns=["src/api.py"])
        code, out = self.cli("merge", "99-nope")
        self.assertNotEqual(code, 0, out)
        self.assertIn("99-nope", out)


if __name__ == "__main__":
    unittest.main()
