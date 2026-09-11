"""The named lock: what it serialises, and proof that the proof is not vacuous.

`state.locked` was the one writer in the ledger that survived a wave. These
tests pin the generalised form — `lock.held(layout, name)` — against the three
things that make a lock worth having and are all easy to get wrong:

* under real contention it loses nothing (and, run without it, the same workload
  demonstrably does lose updates — otherwise the first claim proves nothing);
* a lock reclaimed as stale is not deleted by the holder it was taken from, so
  the reclaim cannot leave two writers each believing they are alone;
* a name is a name, never a path, so a plan slug cannot write outside
  `runtime/locks/`.
"""

import contextlib
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import lock, state  # noqa: E402
from support import Fixture  # noqa: E402


REPO = str(Path(__file__).resolve().parent.parent)

# Eight processes, forty increments each. The count is only reachable if every
# read-modify-write saw the previous one.
WORKERS = 8
ROUNDS = 40
EXPECTED = WORKERS * ROUNDS

# A window between the read and the write, so an unserialised run collides for
# certain rather than for luck. It is also what makes the serialised run a real
# test: without it the whole body is one `write_text` and the operating system
# hides the bug.
WINDOW = 0.01

_WORKER = """
import json, sys, time
sys.path.insert(0, {repo!r})
from ctx import lock, paths

layout = paths.Layout({root!r})
counter = layout.runtime / "counter.json"


def bump():
    try:
        total = json.loads(counter.read_text()) if counter.is_file() else 0
    except ValueError:
        total = 0   # a half-written file: the unserialised run tears it too
    time.sleep({window!r})
    counter.write_text(json.dumps(total + 1))


for _ in range({rounds}):
{body}
"""

_LOCKED_BODY = """    with lock.held(layout, {name!r}) as taken:
        assert taken, "failed open under contention"
        bump()
"""

_UNLOCKED_BODY = """    bump()
"""


class TestContention(Fixture):
    """Criteria 3 and 5 — the same workload, with the lock and without it."""

    def _run(self, body):
        counter = self.layout.runtime / "counter.json"
        if counter.exists():
            counter.unlink()
        self.layout.runtime.mkdir(parents=True, exist_ok=True)
        script = _WORKER.format(
            repo=REPO, root=str(self.layout.root), rounds=ROUNDS,
            window=WINDOW, body=body,
        )
        workers = [
            subprocess.Popen([sys.executable, "-c", script],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(WORKERS)
        ]
        for worker in workers:
            _out, err = worker.communicate(timeout=120)
            self.assertEqual(worker.returncode, 0, err.decode())
        return json.loads(counter.read_text())

    def test_the_lock_loses_no_updates_under_eight_way_contention(self):
        total = self._run(_LOCKED_BODY.format(name="plan-demo"))
        self.assertEqual(total, EXPECTED)

    def test_the_same_workload_unlocked_does_not_serialise(self):
        """The statistical half of the control: the unlocked run does not land
        on a correct count.

        It asserts inequality, not `assertLess`, and the direction matters. An
        unlocked run can finish *above* EXPECTED as well as below, because the
        write is not atomic either: two workers both open with truncation, and a
        shorter value written over a longer one leaves the previous value's
        trailing digits behind — 45 landing on 320 reads back as 450. This test
        asserted `< EXPECTED` and went red at 322 under load, which is the
        control reporting the bug it exists to find as a test failure.

        The deterministic proof of the lost update is the test below; this one
        only says the unserialised workload is not a correct serialisation.
        """
        total = self._run(_UNLOCKED_BODY)
        self.assertNotEqual(
            total, EXPECTED,
            "the unlocked workload produced a correct count, so the locked "
            "test above is asserting that a race happened not to fire today",
        )

    def test_without_the_lock_one_writer_erases_the_other(self):
        """The deterministic control. No timing, no luck, no load sensitivity.

        Two workers are held at a rendezvous until both have read the same
        value, so the lost update is forced rather than raced. Both then write
        `value + 1`. Serialised that is 2; unserialised the second save erases
        the first and it is 1.
        """
        counter = self.layout.runtime / "counter.json"
        self.layout.runtime.mkdir(parents=True, exist_ok=True)
        counter.write_text(json.dumps(0))
        gate = self.layout.runtime / "rendezvous"
        gate.mkdir()

        script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, {repo!r})

counter = Path({counter!r})
gate = Path({gate!r})
me = {me!r}

value = json.loads(counter.read_text())
(gate / ("read-" + me)).write_text("")

deadline = time.time() + 30
while time.time() < deadline:
    if len(list(gate.glob("read-*"))) == 2:
        break
    time.sleep(0.01)
else:
    raise SystemExit("timed out waiting for the other reader")

counter.write_text(json.dumps(value + 1))
"""
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", script.format(
                    repo=REPO, counter=str(counter), gate=str(gate), me=name)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for name in ("a", "b")
        ]
        for worker in workers:
            _out, err = worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0, err.decode())

        # Assert the race actually happened before asserting its outcome, so
        # the test cannot pass by never having raced.
        self.assertEqual(
            sorted(path.name for path in gate.glob("read-*")),
            ["read-a", "read-b"],
            "both workers must have read before either wrote",
        )
        self.assertEqual(
            json.loads(counter.read_text()), 1,
            "both writers saw 0 and both wrote 1, so one update must be lost — "
            "if this is 2 the unserialised path is somehow serialising",
        )

    def test_a_plan_name_locks_under_runtime_locks(self):
        with lock.held(self.layout, "plan-demo") as taken:
            self.assertTrue(taken)
            path = lock.path_for(self.layout, "plan-demo")
            self.assertEqual(path, self.layout.runtime / "locks" / "plan-demo.lock")
            self.assertTrue(path.is_file())
        self.assertFalse(path.exists())


class TestStateDelegates(Fixture):
    """Criterion 2 — `state.locked` is now one caller of the general lock, and
    keeps the file it always had. A process still running the older code must
    contend with us, not take a different path and serialise against nobody."""

    def test_state_still_locks_the_file_it_always_did(self):
        with state.locked(self.layout) as taken:
            self.assertTrue(taken)
            self.assertTrue((self.layout.runtime / "state.lock").is_file())
            self.assertFalse((self.layout.runtime / "locks").exists())

    def test_state_keeps_its_own_generous_limits(self):
        self.assertEqual(
            (state.LOCK_TIMEOUT, state.LOCK_STALE_SECONDS), lock.limits("state")
        )
        self.assertGreater(state.LOCK_TIMEOUT, lock.LOCK_TIMEOUT)


class TestFailsOpen(Fixture):
    """A lock that cannot be taken is a lost update risked, never a broken
    session — the same bargain `state.locked` made."""

    def test_a_contended_lock_yields_false_rather_than_raising(self):
        with unittest.mock.patch.object(lock, "LOCK_TIMEOUT", 0.05):
            with lock.held(self.layout, "busy") as first:
                self.assertTrue(first)
                with lock.held(self.layout, "busy") as second:
                    self.assertFalse(second)

    def test_the_loser_does_not_unlink_the_winners_lock(self):
        path = lock.path_for(self.layout, "busy")
        with unittest.mock.patch.object(lock, "LOCK_TIMEOUT", 0.05):
            with lock.held(self.layout, "busy"):
                with lock.held(self.layout, "busy") as second:
                    self.assertFalse(second)
                self.assertTrue(path.is_file())
        self.assertFalse(path.exists())

    def test_the_lock_is_released_even_when_the_body_raises(self):
        path = lock.path_for(self.layout, "boom")
        with self.assertRaises(ValueError):
            with lock.held(self.layout, "boom"):
                raise ValueError("boom")
        self.assertFalse(path.exists())


class TestStaleReclaim(Fixture):
    """Criterion 4 — the reclaim, and the release that must not follow it.

    A lock older than `LOCK_STALE_SECONDS` is rubble from a killed process, so
    the next caller takes it. That is deliberate, and it opens the one window
    this lock cannot close: the original holder may be alive after all. What it
    *can* close is the damage — the late holder must not unlink a lock file it
    no longer owns, because that would hand a third caller the lock while the
    second still believes it holds it, and then the reclaim has produced two
    writers rather than one.

    The window is forced here by ageing the file's mtime, which is the only way
    to reproduce it without a two-minute test.
    """

    def test_an_aged_lock_is_reclaimed_and_the_first_holder_does_not_delete_it(self):
        path = lock.path_for(self.layout, "aging")
        stack = contextlib.ExitStack()
        self.assertTrue(stack.enter_context(lock.held(self.layout, "aging")))
        first_token = path.read_bytes()

        os.utime(path, (0, 0))  # far older than LOCK_STALE_SECONDS

        reclaimer = lock.held(self.layout, "aging")
        self.assertTrue(reclaimer.__enter__(), "a stale lock must be reclaimable")
        second_token = path.read_bytes()
        self.assertNotEqual(second_token, first_token, "a new holder, a new lock")

        stack.close()  # the original holder finally releases
        self.assertTrue(path.is_file(), "it deleted a lock it no longer owned")
        self.assertEqual(path.read_bytes(), second_token)

        reclaimer.__exit__(None, None, None)
        self.assertFalse(path.exists())

    def test_a_fresh_lock_is_not_reclaimed(self):
        """The other half: if ageing were not required, the reclaim would just
        be a second holder, always."""
        with unittest.mock.patch.object(lock, "LOCK_TIMEOUT", 0.05):
            with lock.held(self.layout, "fresh"):
                with lock.held(self.layout, "fresh") as second:
                    self.assertFalse(second)


class TestNameIsNeverAPath(Fixture):
    """Criterion 6 — a plan slug reaches the ledger from a filename, a CLI
    argument and a frontmatter field, so it is untrusted input."""

    escapes = (
        "../../escape",
        "..",
        "../../../etc/passwd",
        "plan/../../../../tmp/evil",
        "sub/dir/name",
        "C:\\Windows\\evil",
        "  ",
        "",
    )

    def test_no_name_escapes_the_locks_directory(self):
        locks = (self.layout.runtime / "locks").resolve()
        for name in self.escapes:
            path = lock.path_for(self.layout, name)
            with self.subTest(name=name):
                self.assertEqual(path.parent.resolve(), locks)
                self.assertNotIn("..", path.name)
                self.assertNotIn("/", path.name)
                self.assertNotIn("\\", path.name)

    def test_an_escaping_name_writes_only_inside_the_locks_directory(self):
        outside = self.layout.runtime.parent / "escape"
        with lock.held(self.layout, "../../escape") as taken:
            self.assertTrue(taken)
            written = sorted(p.name for p in (self.layout.runtime / "locks").iterdir())
            self.assertEqual(written, ["escape.lock"])
            self.assertFalse(outside.exists())
            self.assertFalse((self.layout.root.parent / "escape").exists())

    def test_two_plans_do_not_share_one_lock(self):
        first = lock.path_for(self.layout, "plan-auth")
        second = lock.path_for(self.layout, "plan-billing")
        self.assertNotEqual(first, second)


class TestLocksAreNotWork(Fixture):
    """Criterion 7 — locks live under gitignored `runtime/`. A lock that showed
    up in `git status` would be indistinguishable from the user's work, and the
    gate's scope checks read exactly that."""

    def test_a_lock_leaves_the_tree_clean(self):
        self.git_init()
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"),
                         "", "the fixture must start clean or this proves nothing")
        with lock.held(self.layout, "plan-demo") as taken:
            self.assertTrue(taken)
            self.assertTrue(lock.path_for(self.layout, "plan-demo").is_file())
            self.assertEqual(
                self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(
            self.git("status", "--porcelain", "--untracked-files=all"), "")

    def git(self, *args):
        completed = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True,
            env=dict(self._env, GIT_CONFIG_GLOBAL="/dev/null",
                     GIT_CONFIG_SYSTEM="/dev/null"),
        )
        self.assertEqual(completed.returncode, 0,
                         f"git {' '.join(args)}: {completed.stderr}")
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
