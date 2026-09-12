"""The read-modify-write that spans the done-gate, and the writes it erased.

`ctx unit <name> --status done` parses the unit file, runs the whole verify
suite — up to 240 seconds for a single `cmd` check — and then writes the
document it parsed *before all that* back whole. Anything that landed in the
window is gone. The write that matters is `worktree._record_fork_point`'s
`base_branch`: it is the only record of which branch a unit forked from, and
`worktree.merge` refuses to land anywhere else. Erase it and the merge-target
guard the whole of `worktree.py` is built around is disarmed — silently, with
no error anywhere, and the next `ctx merge` lands the branch on whatever HEAD
happens to be.

`ctx start --worktree` has the same shape and is worse: there the erasing write
is not a race at all. `dispatch.prepare` parses the units, `prepare_worktrees`
then writes `base_branch` into those same files through its own fresh read, and
the `status: running` loop writes the stale parse back over it. One process,
every single time.

**Every control here is deterministic.** The interleaved write is driven to the
exact point between the gate's read and its write by a rendezvous on marker
files — the verify command under test *is* the rendezvous: it announces that
the gate is running and then blocks until the interleaved write is provably on
disk. No assertion depends on a duration, so load changes only how long the
handshake takes. The idiom is `tests/test_ledger_locks.py`'s, deliberately
reused rather than reinvented.

Each fix carries its positive control, and both controls are live tests rather
than something that was true once on a laptop: they patch `cli._set_unit_status`
back to its pre-fix body (`unit.set(...)` on the object parsed at t0) and assert
`base_branch` is destroyed.
"""

import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    cli, frontmatter, lock, plan as plan_mod, state, worktree as wt,
)
from support import OK, Fixture  # noqa: E402
from test_ledger_locks import LockTrace  # noqa: E402


REPO = str(Path(__file__).resolve().parent.parent)

# A ceiling on every rendezvous, so a worker that died takes the test down with
# a message instead of hanging the suite. Not a timing assumption: nothing is
# asserted to happen before it, only that the test does not wait forever.
DEADLINE = 120.0
POLL = 0.005


def naive_set_unit_status(layout, slug, units, status):
    """`cli._set_unit_status` as it was before this unit: no re-read, no lock.

    The whole of the defect in four lines. Used as the positive control, so the
    claim "this window erased writes" is asserted by the suite on every run
    rather than remembered from a terminal somebody has since closed.
    """
    for unit in units:
        unit.set(status=status)
    return units


class GateWindowFixture(Fixture):
    """A plan with one unit whose gate can be held open on demand."""

    SLUG = "auth"
    UNIT = "01-api"

    def setUp(self):
        super().setUp()
        self.runtime = self.layout.runtime
        self.runtime.mkdir(parents=True, exist_ok=True)

    # -- the unit ---------------------------------------------------------- #

    def make_unit(self, checks, tier="subagent", name=None, slug=None,
                  **extra_meta):
        name = name or self.UNIT
        slug = slug or self.SLUG
        directory = plan_mod.units_dir(self.layout, slug)
        directory.mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": slug, "tier": tier,
            "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
            "forbid": [], "budget_tokens": 1000, "status": "pending", "wave": 1,
            "verify": checks,
        }
        meta.update(extra_meta)
        path = directory / f"{name}.md"
        frontmatter.Document(
            meta,
            f"## Objective\nBuild {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(path)
        self.trust(checks)
        return path

    def meta_on_disk(self, name=None, slug=None):
        path = (plan_mod.units_dir(self.layout, slug or self.SLUG)
                / f"{name or self.UNIT}.md")
        return frontmatter.read(path).meta

    # -- the rendezvous ---------------------------------------------------- #

    def slow_gate_check(self):
        """A verify `cmd` that holds the gate open until the interleaved write
        has provably landed — and that refuses if the plan lock is held while
        it runs.

        Two things at once, both load-bearing. It makes the gate genuinely slow
        *by construction* rather than by sleeping, so the window under test is
        open for exactly as long as the interleaving needs and not one
        millisecond of guesswork. And because it executes as a real subprocess
        from inside `verify.run`, it is the only place that can answer "was the
        plan lock held while the checks ran?" honestly. Holding `plan-<slug>`
        across a 240-second gate would block every sibling in a concurrent
        wave, which is a worse defect than the one being fixed; if that ever
        starts happening, this check exits 1, the gate refuses, and the test
        that asserted `done` goes red.
        """
        script = self.root / "slow-gate.py"
        script.write_text(
            "import pathlib, sys, time\n"
            f"runtime = pathlib.Path({str(self.runtime)!r})\n"
            f"held = pathlib.Path({str(lock.path_for(self.layout, 'plan-' + self.SLUG))!r})\n"
            "if held.exists():\n"
            "    sys.exit('the plan lock was held while the gate ran')\n"
            "runtime.mkdir(parents=True, exist_ok=True)\n"
            "(runtime / 'gate-running').write_text('1')\n"
            f"deadline = time.monotonic() + {DEADLINE!r}\n"
            "while not (runtime / 'write-landed').exists():\n"
            "    if time.monotonic() > deadline:\n"
            "        sys.exit('timed out waiting for the interleaved write')\n"
            f"    time.sleep({POLL!r})\n",
            encoding="utf-8",
        )
        return {"kind": "cmd", "run": f'"{sys.executable}" "{script}"'}

    def interleaving_worker(self, changes):
        """A separate process that writes `changes` into the unit file at the
        moment the gate is between its read and its write.

        It blocks on `gate-running` rather than on a clock, confirms its write
        is on disk by re-reading the file, and only then opens `write-landed`
        to let the gate finish. So the run cannot pass by never having raced:
        if the write did not land, the marker never appears, the gate times out
        and the test fails with that message rather than a green tick.
        """
        script = self.root / "interleave.py"
        script.write_text(
            "import pathlib, sys, time\n"
            f"sys.path.insert(0, {REPO!r})\n"
            "from ctx import paths, plan as plan_mod\n"
            f"layout = paths.Layout({str(self.layout.root)!r})\n"
            f"runtime = pathlib.Path({str(self.runtime)!r})\n"
            f"deadline = time.monotonic() + {DEADLINE!r}\n"
            "while not (runtime / 'gate-running').exists():\n"
            "    if time.monotonic() > deadline:\n"
            "        sys.exit('timed out waiting for the gate to start')\n"
            f"    time.sleep({POLL!r})\n"
            f"unit = plan_mod.find_unit(layout, {self.SLUG!r}, {self.UNIT!r})\n"
            f"unit.set(**{changes!r})\n"
            f"again = plan_mod.find_unit(layout, {self.SLUG!r}, {self.UNIT!r})\n"
            f"for key, value in {changes!r}.items():\n"
            "    if again.doc.meta.get(key) != value:\n"
            "        sys.exit('the interleaved write never landed: %s' % key)\n"
            "(runtime / 'write-landed').write_text('1')\n",
            encoding="utf-8",
        )
        return subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def await_worker(self, worker):
        _out, err = worker.communicate(timeout=DEADLINE)
        self.assertEqual(worker.returncode, 0, err.decode())

    def assert_raced(self):
        """The half that makes this a control rather than a hope: both sides of
        the rendezvous actually happened."""
        self.assertTrue((self.runtime / "gate-running").is_file(),
                        "the gate never ran — nothing was interleaved with")
        self.assertTrue((self.runtime / "write-landed").is_file(),
                        "the interleaved write never landed — the control "
                        "proves nothing")


# --------------------------------------------------------------------------- #
# criteria 1-3 — `ctx unit --status done`
# --------------------------------------------------------------------------- #

class TestTheDoneTransitionPreservesConcurrentWrites(GateWindowFixture):

    def test_base_branch_written_during_the_gate_survives_the_done_transition(self):
        """Criterion 2, and the point of this unit.

        `base_branch` is written into the unit file while the gate is running.
        Afterwards the unit is `done` — the gate passed — *and* `base_branch` is
        still there. Nothing in this is timed: the gate does not finish until
        the write is confirmed on disk, and the write does not start until the
        gate has begun.
        """
        self.make_unit([self.slow_gate_check()])
        worker = self.interleaving_worker({"base_branch": "release/2.1"})
        code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                             "--status", "done")
        self.await_worker(worker)
        self.assert_raced()

        self.assertEqual(code, 0, out)
        meta = self.meta_on_disk()
        self.assertEqual(meta.get("status"), "done", out)
        self.assertEqual(
            meta.get("base_branch"), "release/2.1",
            "the gate window erased the fork point — `ctx merge` now has no "
            "merge target and will land the branch on whatever HEAD is on",
        )

    def test_without_the_re_read_that_write_is_destroyed(self):
        """The positive control. Same rendezvous, same everything, with
        `_set_unit_status` restored to its pre-fix body: `base_branch` is gone.

        If this ever passes, the fix has been removed and the test above is
        passing for some other reason."""
        self.make_unit([self.slow_gate_check()])
        worker = self.interleaving_worker({"base_branch": "release/2.1"})
        with unittest.mock.patch.object(cli, "_set_unit_status",
                                        naive_set_unit_status):
            code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                                 "--status", "done")
        self.await_worker(worker)
        self.assert_raced()

        self.assertEqual(code, 0, out)
        meta = self.meta_on_disk()
        self.assertEqual(meta.get("status"), "done")
        self.assertIsNone(
            meta.get("base_branch"),
            "the pre-fix code is supposed to lose this write — if it does not, "
            "this control is no longer controlling anything",
        )

    def test_the_gates_verdict_wins_over_a_concurrent_status_write(self):
        """Criterion 3, first half. Re-reading must not hand the decision to
        whoever wrote last. A sibling process sets `status: pending` inside the
        window; the gate decided `done`, and `done` is what is on disk."""
        self.make_unit([self.slow_gate_check()])
        worker = self.interleaving_worker(
            {"status": "pending", "base_branch": "release/2.1"})
        code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                             "--status", "done")
        self.await_worker(worker)
        self.assert_raced()

        self.assertEqual(code, 0, out)
        self.assertEqual(self.meta_on_disk().get("status"), "done",
                         "a concurrent writer overturned the gate's verdict")

    def test_and_every_other_field_that_writer_set_is_kept(self):
        """Criterion 3, second half. `status` is the only field the verdict
        owns. Everything else the other writer touched belongs to the other
        writer and survives."""
        self.make_unit([self.slow_gate_check()])
        worker = self.interleaving_worker(
            {"status": "pending", "base_branch": "release/2.1", "model": "opus"})
        code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                             "--status", "done")
        self.await_worker(worker)
        self.assert_raced()

        self.assertEqual(code, 0, out)
        meta = self.meta_on_disk()
        self.assertEqual(meta.get("base_branch"), "release/2.1")
        self.assertEqual(meta.get("model"), "opus")

    def test_a_refused_gate_still_writes_no_status_at_all(self):
        """The fix must not turn a refusal into a write. A failing gate leaves
        the unit exactly as it was."""
        failing = {"kind": "cmd",
                   "run": '"%s" -c "import sys; sys.exit(1)"' % sys.executable}
        self.make_unit([failing])
        code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                             "--status", "done")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.meta_on_disk().get("status"), "pending")


# --------------------------------------------------------------------------- #
# criterion 1 — where the lock is, and where it is not
# --------------------------------------------------------------------------- #

class TestTheLockSpansTheWriteAndNothingElse(GateWindowFixture):

    def test_the_done_path_never_takes_the_same_lock_twice(self):
        """`lock.held` is not re-entrant, and unit 06 put `plan-<slug>` inside
        `findings.Ledger.add` / `.set_status` / `.bump_round`, `phases.Ledger.add`
        and `phases.record`. A second acquisition of a lock this process already
        holds does not hang — it stalls for the whole five-second timeout and
        then fails open, which is no serialisation *and* a five-second pause:
        the worst of both. So the tell is an acquisition that came back False,
        and a run that took longer than a timeout. Both are checked.

        `_gate_before_done`'s success path calls `contract.seal_findings` and
        `findings.load`; neither reaches a locked mutator, and this asserts that
        rather than trusting today's call graph to stay that way.
        """
        self.make_unit([{"kind": "cmd", "run": OK}])
        trace = LockTrace()
        started = time.monotonic()
        with trace.patched():
            code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                                 "--status", "done")
        elapsed = time.monotonic() - started

        self.assertEqual(code, 0, out)
        self.assertIn(f"plan-{self.SLUG}", trace.names,
                      "the write was not serialised at all")
        self.assertTrue(all(trace.taken),
                        "an acquisition failed open — something is nested")
        for name in set(trace.names):
            self.assertEqual(trace.max_depth_for(name), 1, f"{name} nested")
        self.assertLess(elapsed, lock.LOCK_TIMEOUT,
                        "a whole timeout elapsed — something waited on itself")

    def test_the_plan_lock_is_taken_exactly_once(self):
        """One acquisition, around one read-modify-write. Two would mean the
        re-read and the write had drifted apart again."""
        self.make_unit([{"kind": "cmd", "run": OK}])
        trace = LockTrace()
        with trace.patched():
            self.assertEqual(
                self.cli("unit", self.UNIT, "--plan", self.SLUG,
                         "--status", "done")[0], 0)
        self.assertEqual(
            [n for n in trace.names if n == f"plan-{self.SLUG}"],
            [f"plan-{self.SLUG}"],
        )

    def test_the_gate_itself_runs_outside_the_lock(self):
        """Asserted by the checks themselves, from inside `verify.run`.

        `slow_gate_check` exits non-zero if `plan-<slug>.lock` exists while it
        is running, so a fix that simply wrapped the whole of `cmd_unit` in the
        lock — which would close the window and block every concurrent sibling
        for the length of the slowest command — turns this green tick red.
        """
        self.make_unit([self.slow_gate_check()])
        worker = self.interleaving_worker({"base_branch": "release/2.1"})
        code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                             "--status", "done")
        self.await_worker(worker)
        self.assertEqual(code, 0, out)
        self.assertNotIn("the plan lock was held while the gate ran", out)

    def test_a_lock_that_cannot_be_taken_still_writes(self):
        """`lock.held` fails open by design — a lock is a reason to risk a lost
        update, never a reason to break a session. The write still happens."""
        import contextlib

        @contextlib.contextmanager
        def refuses(layout, name):
            yield False

        self.make_unit([{"kind": "cmd", "run": OK}])
        with unittest.mock.patch.object(lock, "held", refuses):
            code, out = self.cli("unit", self.UNIT, "--plan", self.SLUG,
                                 "--status", "done")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.meta_on_disk().get("status"), "done")


# --------------------------------------------------------------------------- #
# criterion 4 — where the fix does *not* belong
# --------------------------------------------------------------------------- #

class TestUnitSetStaysAWholeDocumentWrite(GateWindowFixture):
    """`plan.Unit.set` has a dozen callers whose window is a few microseconds
    wide. Making it re-read and lock would put a lock acquisition on every one
    of them to fix two, and would hide the window rather than close it — the
    call site that spends four minutes between its read and its write is the
    thing that is wrong, not the method that writes."""

    def test_set_writes_the_document_it_was_given(self):
        self.make_unit([{"kind": "cmd", "run": OK}])
        unit = plan_mod.find_unit(self.layout, self.SLUG, self.UNIT)
        # Another writer, after `unit` was parsed.
        plan_mod.find_unit(self.layout, self.SLUG, self.UNIT).set(note="theirs")
        unit.set(status="running")
        meta = self.meta_on_disk()
        self.assertEqual(meta.get("status"), "running")
        self.assertIsNone(
            meta.get("note"),
            "`Unit.set` grew a re-read — the fix belongs at the call site with "
            "the long window, not in a method a dozen short-window callers use",
        )

    def test_set_takes_no_lock(self):
        self.make_unit([{"kind": "cmd", "run": OK}])
        unit = plan_mod.find_unit(self.layout, self.SLUG, self.UNIT)
        trace = LockTrace()
        with trace.patched():
            unit.set(status="running")
        self.assertEqual(trace.names, [])


# --------------------------------------------------------------------------- #
# criterion 5 — the same window at dispatch
# --------------------------------------------------------------------------- #

class TestDispatchDoesNotEraseTheForkPoint(GateWindowFixture):
    """`cli.py`'s other `unit.set(status="running")` had the identical
    exposure, and it was not even a race.

    `dispatch.prepare` parses the units. `prepare_worktrees` then calls
    `worktree.create` -> `_record_fork_point`, which re-reads each unit file and
    writes `base_branch` into it. The loop below then writes the parse from
    before all that back whole. Deterministic, in one process, on every single
    `ctx start --worktree`.
    """

    def setUp(self):
        super().setUp()
        self.git_init()

    def start(self):
        return self.cli("start", self.SLUG, "--worktree")

    def test_ctx_start_worktree_keeps_the_base_branch_it_just_recorded(self):
        self.make_unit([{"kind": "cmd", "run": OK}], tier="session")
        code, out = self.start()
        self.assertEqual(code, 0, out)

        meta = self.meta_on_disk()
        self.assertEqual(meta.get("status"), "running", out)
        self.assertEqual(
            meta.get("base_branch"), "main",
            "the dispatch write erased the fork point `prepare_worktrees` had "
            "just recorded, four lines earlier, in this same process",
        )

    def test_without_the_re_read_the_dispatch_write_destroys_it(self):
        """The positive control for criterion 5. No rendezvous needed: the two
        writes are ordered by the code itself."""
        self.make_unit([{"kind": "cmd", "run": OK}], tier="session")
        with unittest.mock.patch.object(cli, "_set_unit_status",
                                        naive_set_unit_status):
            code, out = self.start()
        self.assertEqual(code, 0, out)
        meta = self.meta_on_disk()
        self.assertEqual(meta.get("status"), "running")
        self.assertIsNone(
            meta.get("base_branch"),
            "the pre-fix dispatch path is supposed to lose this — if it does "
            "not, this control is no longer controlling anything",
        )

    def test_the_fork_point_survives_a_second_dispatch(self):
        """Idempotence: re-running `ctx start` must not lose it either."""
        self.make_unit([{"kind": "cmd", "run": OK}], tier="session")
        self.assertEqual(self.start()[0], 0)
        self.assertEqual(self.start()[0], 0)
        self.assertEqual(self.meta_on_disk().get("base_branch"), "main")


# --------------------------------------------------------------------------- #
# criteria 15-16 — the worktree call site, and the flag its refusal names
# --------------------------------------------------------------------------- #

class TestWorktreeRemovalResolvesFromTheCli(GateWindowFixture):
    """Unit 03 taught `worktree.remove` to refuse an ambiguous name and to tell
    the user to "pass --plan to say which one to discard". No such flag existed:
    the subparser defined `action`, `name` and `--force` and nothing else, so
    the one message that said what to do could not be acted on."""

    def setUp(self):
        super().setUp()
        self.git_init()

    def dispatched(self, slug, name):
        self.make_unit([{"kind": "cmd", "run": OK}], tier="session",
                       name=name, slug=slug)
        path, branch, created, error = wt.create(self.layout, slug, name)
        self.assertEqual(error, "")
        self.assertTrue(created)
        return path, branch

    def branch_exists(self, branch):
        return subprocess.run(
            ["git", "rev-parse", "--verify", branch], cwd=str(self.root),
            capture_output=True, text=True,
        ).returncode == 0

    def test_the_flag_exists_and_is_spelled_as_ctx_merge_spells_it(self):
        parser = cli.build_parser()
        parsed = parser.parse_args(
            ["worktree", "remove", "01-api", "--plan", "plan-b"])
        self.assertEqual(parsed.plan, "plan-b")

    def test_two_plans_one_name_is_refused_with_no_active_plan(self):
        """Criterion 15, second half: the ambiguity refusal stays reachable.
        Nothing is removed and no branch is deleted."""
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        b_path, b_branch = self.dispatched("plan-b", "01-api")
        self.assertEqual(self.cli("drop")[0], 0)  # no active plan

        code, out = self.cli("worktree", "remove", "01-api", "--force")
        self.assertEqual(code, 1, out)
        self.assertIn("2 plans have a worktree named 01-api", out)
        self.assertIn("plan-a", out)
        self.assertIn("plan-b", out)
        self.assertIn("--plan", out)
        self.assertTrue(a_path.is_dir())
        self.assertTrue(b_path.is_dir())
        self.assertTrue(self.branch_exists(a_branch))
        self.assertTrue(self.branch_exists(b_branch))

    def test_plan_resolves_that_ambiguity_and_removes_only_that_plans_tree(self):
        """Criterion 16. The flag the refusal names now does what it says."""
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        b_path, b_branch = self.dispatched("plan-b", "01-api")
        self.assertEqual(self.cli("drop")[0], 0)

        code, out = self.cli("worktree", "remove", "01-api",
                             "--plan", "plan-b", "--force")
        self.assertEqual(code, 0, out)
        self.assertFalse(b_path.exists(), out)
        self.assertFalse(self.branch_exists(b_branch))
        self.assertTrue(a_path.is_dir(), "plan-a's worktree was destroyed")
        self.assertTrue(self.branch_exists(a_branch),
                        "plan-a's branch was deleted")

    def test_the_active_plan_answers_without_the_ambiguity_scan(self):
        """Criterion 15, first half. Two plans share the name, `--plan` is not
        given, and the active plan is one of them — that is an answer, so the
        scan never has to refuse."""
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        b_path, b_branch = self.dispatched("plan-b", "01-api")
        state.update(self.layout, plan="plan-a")

        code, out = self.cli("worktree", "remove", "01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertFalse(a_path.exists(), out)
        self.assertFalse(self.branch_exists(a_branch))
        self.assertTrue(b_path.is_dir(), "plan-b's worktree was destroyed")
        self.assertTrue(self.branch_exists(b_branch))

    def test_an_active_plan_that_has_no_such_tree_does_not_answer(self):
        """The guard on the shortcut. An active plan is only an answer when it
        actually has a tree by that name — otherwise it would turn a resolvable
        removal into a path that does not exist, and the honest answer is the
        one the scan gives."""
        a_path, a_branch = self.dispatched("plan-a", "01-api")
        self.make_unit([{"kind": "cmd", "run": OK}], tier="session",
                       name="02-cli", slug="plan-b")
        state.update(self.layout, plan="plan-b")

        code, out = self.cli("worktree", "remove", "01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertFalse(a_path.exists(), out)
        self.assertFalse(self.branch_exists(a_branch))


# --------------------------------------------------------------------------- #
# criterion 17 — the documented path
# --------------------------------------------------------------------------- #

class TestTheReadmeDocumentsThePathThatExists(unittest.TestCase):

    def readme(self):
        return (Path(__file__).resolve().parent.parent
                / "README.md").read_text(encoding="utf-8")

    def test_no_flat_worktree_path_is_documented(self):
        """`.ctx/runtime/worktrees/<unit>` has not existed since unit 03
        partitioned worktrees by plan. A README that tells the reader to `cd`
        into it sends them to a directory that is not there."""
        text = self.readme()
        self.assertNotIn("cd .ctx/runtime/worktrees/03-rotate", text)
        self.assertIn("cd .ctx/runtime/worktrees/auth-rotation/03-rotate", text)

    def test_the_documented_path_is_the_one_path_for_builds(self):
        """Not spelled from memory: derived from the function that builds it,
        so the two cannot drift."""
        from ctx import paths
        layout = paths.Layout(Path("/project/.ctx"))
        built = wt.path_for(layout, "auth-rotation", "03-rotate")
        # Compare in posix form. `str(built)` uses the platform separator, so
        # splitting on a literal "/.ctx/" found nothing on Windows and raised
        # IndexError before it could assert anything.
        tail = built.as_posix().split("/.ctx/", 1)[1]
        self.assertIn(f".ctx/{tail}", self.readme())


if __name__ == "__main__":
    unittest.main()
