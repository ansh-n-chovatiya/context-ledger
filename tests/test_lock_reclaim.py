"""The stale-lock reclaim's double-holder path: reproduced, then closed.

`report.md` §3.3 recorded this as code-path only — "can theoretically hand the
lock to two holders and unlinks locks it does not own" — explicitly not
reproduced under 4-way timing contention. A race that only shows up if the
scheduler happens to pause a process between two adjacent syscalls does not
reproduce by racing harder; it reproduces by holding it there on purpose.

Every test below forces the exact interleaving rather than hoping for it,
using the same rendezvous idiom as `test_ledger_locks.py`: real subprocesses,
synchronised on marker files, never on a sleep. The vulnerable window is
narrow enough that even a rendezvous cannot pause a process *between two
syscalls inside one library call* — `path.stat()` and `path.unlink()` are one
line apart in the shipped code — so the delay is injected the only way that is
still honest about what it is testing: by monkeypatching the last pristine
read the reclaim itself makes (`Path.stat`, or `Path.read_bytes` for the fixed
shape's extra token check) inside the worker process, to hold there until the
other side has finished acting. This is the same technique `test_ledger_locks.py`
uses to widen `_rotate`'s replace window — a seam the code under test already
calls through, not a rewrite of the code under test.

* `ReclaimRaceTests.test_the_shipped_stat_then_unlink_hands_the_lock_to_two_holders`
  is the positive control: a faithful reconstruction of the code `report.md`
  cited (`stat` then `unlink`, no verification), run under the forced
  interleaving. It reproduces both halves of the claim — two processes each
  believe, at overlapping moments, that they are the lock's sole holder, and
  the second one's delete removes a file whose token was never its own.

* `test_the_fixed_reclaim_never_hands_the_lock_to_two_holders` is the same
  forced interleaving against `ctx.lock._reclaim` as shipped in this change.
  The delayed reclaimer's rename still succeeds — rename does not consult
  content — but its post-rename token comparison catches the mismatch,
  restores what it took, and reports no reclaim. Only one process ever
  becomes the holder, and nothing it did not own is deleted.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import lock  # noqa: E402
from support import Fixture  # noqa: E402


REPO = str(Path(__file__).resolve().parent.parent)
NAME = "reclaim-race"
STALE = 1.0  # small and explicit; deadline below is the only real limit
DEADLINE = 60.0
POLL = 0.005

# The rendezvous, worker side — the same idiom as test_ledger_locks.py's
# `_RENDEZVOUS`, duplicated rather than imported so this unit does not reach
# into a file it does not own.
_RENDEZVOUS = '''
def wait_for(*paths):
    deadline = time.monotonic() + {deadline!r}
    for path in paths:
        while not path.exists():
            assert time.monotonic() < deadline, "timed out waiting for %s" % path
            time.sleep({poll!r})
'''


# `role="b"` races the reclaim the moment it sees the stale lock, with no
# delay: it unlinks-or-renames the original stale file and immediately
# recreates its own, becoming a holder as fast as the shipped code allows.
#
# `role="c"` is the delayed reclaimer. It reads the original file's mtime
# first — genuinely stale, genuinely true at that instant — and is then held
# by a patched `Path.stat` until `b` has provably become the holder, before
# it is allowed to act on what it read. That is the exact gap `report.md`
# named: a decision made before the delete, acted on after the world moved.
_WORKER = '''
import os, sys, time
sys.path.insert(0, {repo!r})
from pathlib import Path
from ctx import lock, paths
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
runtime = layout.runtime
path = lock.path_for(layout, {name!r})
role = sys.argv[1]
mode = sys.argv[2]  # "shipped" or "fixed"


def old_reclaim(path, stale):
    """The reconstruction: `report.md`'s own description, stat then unlink,
    with no verification that what is deleted is what was inspected."""
    try:
        if time.time() - path.stat().st_mtime > stale:
            path.unlink()
            return True
    except OSError:
        pass
    return False


def become_holder():
    """The immediate next step after a successful reclaim in `_acquire`: try
    to create the fresh lock and write a token into it."""
    try:
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        (runtime / ("create-failed-%s" % role)).write_text("1")
        return
    token = ("owner-%s" % role).encode("ascii")
    os.write(handle, token)
    os.close(handle)
    (runtime / ("became-holder-%s" % role)).write_bytes(token)


reclaim = old_reclaim if mode == "shipped" else lambda p, s: lock._reclaim(p, s)

if role == "c":
    # `c` must form its staleness verdict — and, in the fixed shape, capture
    # the token it is about to act on — while the file is still the pristine
    # rubble seeded by the test, before `b` has touched anything. It is then
    # held at exactly the point immediately after that last pristine read
    # until `b` has finished its whole reclaim-and-recreate cycle, so what it
    # acts on next is stale information about a file that has since changed
    # underneath it — the exact gap `report.md` named. Which call is "the
    # last pristine read" differs by shape: the shipped code reads only
    # `stat()` before its unlink; the fixed one reads `stat()` and then the
    # token bytes before its rename. Both are calls the code under test
    # already makes; neither is a rewrite of it.
    real_stat = Path.stat
    real_read_bytes = Path.read_bytes

    def gated_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == path and mode == "shipped":
            (runtime / "c-snapshot-done").write_text("1")
            wait_for(runtime / "became-holder-b")
        return result

    def gated_read_bytes(self, *args, **kwargs):
        result = real_read_bytes(self, *args, **kwargs)
        if self == path and mode == "fixed":
            (runtime / "c-snapshot-done").write_text("1")
            wait_for(runtime / "became-holder-b")
        return result

    Path.stat = gated_stat
    Path.read_bytes = gated_read_bytes

if role == "b":
    # `b` may not touch the file until `c` has provably finished reading it —
    # otherwise which of the two ever sees the pristine file first is exactly
    # the scheduling luck this whole file exists to remove.
    wait_for(runtime / "c-snapshot-done")

reclaimed = reclaim(path, {stale!r})
(runtime / ("reclaimed-%s" % role)).write_text("1" if reclaimed else "0")
if reclaimed:
    become_holder()
'''


class ReclaimRaceTests(Fixture):
    """The verdict: reproduced against the shipped shape, closed by
    `ctx.lock._reclaim`."""

    def _seed_stale_lock(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original-holder-token")
        os.utime(path, (0, 0))  # far older than any stale threshold

    def _race(self, mode):
        path = lock.path_for(self.layout, NAME)
        self._seed_stale_lock(path)
        script = _WORKER.format(repo=REPO, root=str(self.layout.root),
                                name=NAME, deadline=DEADLINE, poll=POLL,
                                stale=STALE)
        workers = {
            role: subprocess.Popen(
                [sys.executable, "-c", script, role, mode],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for role in ("b", "c")
        }
        for role, worker in workers.items():
            _out, err = worker.communicate(timeout=DEADLINE)
            self.assertEqual(worker.returncode, 0,
                             f"{role} ({mode}): {err.decode()}")
        runtime = self.layout.runtime
        return {
            "reclaimed_b": (runtime / "reclaimed-b").read_text() == "1",
            "reclaimed_c": (runtime / "reclaimed-c").read_text() == "1",
            "became_holder_b": (runtime / "became-holder-b").exists(),
            "became_holder_c": (runtime / "became-holder-c").exists(),
        }

    def test_the_shipped_stat_then_unlink_hands_the_lock_to_two_holders(self):
        """The positive control. Deterministic: `c` cannot even attempt its
        unlink until `b` has provably already become the holder, so this does
        not depend on scheduling luck to fire.

        `b` reclaims the original stale file and becomes the holder. `c`,
        holding a staleness verdict formed before any of that happened, then
        unlinks whatever is at `path` now — `b`'s brand new lock, not the
        rubble it inspected — and becomes a second holder itself. Both
        `became-holder-*` markers existing is the double-holder claim, proven
        rather than assumed; the file this test's `path` variable never
        touches once `c` starts is the "unlinked a lock it does not own" half.
        """
        result = self._race("shipped")
        self.assertTrue(result["reclaimed_c"],
                        "the forced interleaving did not even reach c's "
                        "unlink — the control proves nothing")
        self.assertTrue(result["became_holder_b"],
                        "b never became a holder — nothing was raced")
        self.assertTrue(
            result["became_holder_c"],
            "c did not become a second holder — the shipped shape resisted "
            "the exact interleaving report.md described",
        )

    def test_the_fixed_reclaim_never_hands_the_lock_to_two_holders(self):
        """The same forced interleaving, against `ctx.lock._reclaim`.

        `c`'s rename still succeeds — a rename does not consult content, so it
        moves `b`'s fresh file off of `path` exactly as the naive version's
        unlink did. What differs is what `c` does next: it reads the token it
        captured, finds it does not match the token it saw before the rename,
        puts the file back, and reports no reclaim. `b` remains the only
        holder throughout, unaware anything happened.
        """
        result = self._race("fixed")
        self.assertTrue(result["became_holder_b"],
                        "b never became a holder — nothing was raced")
        self.assertFalse(
            result["reclaimed_c"],
            "c reported a reclaim it should have reverted — the fix did not "
            "close the window",
        )
        self.assertFalse(
            result["became_holder_c"],
            "c became a second holder — the exact double-holder report.md "
            "described",
        )
        path = lock.path_for(self.layout, NAME)
        self.assertTrue(path.is_file(), "the restore left no lock behind at all")
        self.assertEqual(path.read_bytes(), b"owner-b",
                         "the restored file is not the one b created")

    def test_a_reclaim_the_content_check_rejects_restores_the_live_lock(self):
        """The narrower, single-process shape of the same guarantee: a
        `_reclaim` call whose stale-and-token read is stale by the time the
        rename lands must put back exactly what it took, unedited."""
        path = lock.path_for(self.layout, "single-process")
        self._seed_stale_lock(path)
        real_read_bytes = Path.read_bytes

        def gated_read_bytes(self_path, *args, **kwargs):
            # `_reclaim` calls this once, right after judging the mtime
            # stale, to capture the token it is about to act on. Return that
            # original content, exactly as the real read would, but replace
            # the file underneath before `_reclaim` gets to its rename — the
            # gap a live holder recreating the lock would land in.
            result = real_read_bytes(self_path, *args, **kwargs)
            if self_path == path:
                path.unlink()
                path.write_bytes(b"live-holder-token")
            return result

        Path.read_bytes = gated_read_bytes
        try:
            reclaimed = lock._reclaim(path, STALE)
        finally:
            Path.read_bytes = real_read_bytes

        self.assertFalse(reclaimed, "a live holder's lock was reclaimed")
        self.assertTrue(path.is_file(), "the live holder's lock vanished")
        self.assertEqual(path.read_bytes(), b"live-holder-token",
                         "the restore did not put back the live holder's own token")


if __name__ == "__main__":
    unittest.main()
