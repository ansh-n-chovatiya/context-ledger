"""A transient `EACCES` is contention, not a read-only checkout.

`_acquire` used to give up the moment `os.open(O_CREAT|O_EXCL)` raised anything
other than `FileExistsError`:

    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EROFS, errno.EPERM):
            return None, None  # read-only checkout: nothing to serialise
        return None, None

That reading is right on POSIX, where `EACCES` means the directory cannot be
written and never will be. On Windows it is also what `os.open` raises against a
file another holder is *unlinking* — a delete-pending sharing violation, which
is precisely what contention looks like. So a waiter that collided with a
releaser failed open instantly, and the caller went on to read-modify-write with
no lock at all.

Two CI jobs caught it, both on `windows-latest · python 3.9`:

  * `test_audit_wave2` lost one increment in eighty — `79 != 80`.
  * `test_plan_lock` asserted `taken` and got False: "failed open under
    contention".

The timeout was never the cause, which is why raising it never fixed it:
`test_plan_lock` already sets `LOCK_TIMEOUT = 60.0` inside its workers, against
well under a second of genuinely held time. Nothing waited sixty seconds. The
waiter never waited at all.

`os.access` on the parent directory tells the two cases apart in one call: a
read-only checkout still fails open at once, and a transient collision now waits
out its deadline like any other contention.

Every test here forces the error rather than racing for it. Racing is what
produced a suite that passes on a laptop and fails on a CI runner.
"""

import errno
import os
import unittest

from ctx import lock as lock_mod

from support import Fixture


class RaisesOnce:
    """Make the next `os.open` raise `EACCES`, exactly as Windows does.

    Patching `os.open` inside `lock` rather than the whole process keeps the
    blast radius to the call under test.
    """

    def __init__(self, times=1, err=errno.EACCES):
        self.times = times
        self.err = err
        self.calls = 0
        self._real = None

    def __enter__(self):
        self._real = lock_mod.os.open

        def fake(path, flags, mode=0o777):
            self.calls += 1
            if self.calls <= self.times:
                raise PermissionError(self.err, "simulated sharing violation")
            return self._real(path, flags, mode)

        lock_mod.os.open = fake
        return self

    def __exit__(self, *exc):
        lock_mod.os.open = self._real
        return False


class TestATransientDenialIsWaitedOut(Fixture):

    def test_the_lock_is_still_taken_after_a_sharing_violation(self):
        """The Windows failure, forced: one collision must not lose the lock."""
        with RaisesOnce(times=1) as forced:
            with lock_mod.held(self.layout, "demo") as taken:
                self.assertTrue(taken, "failed open on a transient EACCES")
        self.assertEqual(forced.calls, 2, "it did not retry after the denial")

    def test_it_survives_several_collisions_in_a_row(self):
        with RaisesOnce(times=4):
            with lock_mod.held(self.layout, "demo") as taken:
                self.assertTrue(taken)

    def test_the_retry_is_bounded_by_the_deadline(self):
        """It waits, but it does not wait forever: a denial that never clears
        still fails open once the timeout is spent, as it always did."""
        self.layout.runtime.mkdir(parents=True, exist_ok=True)
        real_limits = lock_mod.limits
        lock_mod.limits = lambda name: (0.15, lock_mod.LOCK_STALE_SECONDS)
        try:
            with RaisesOnce(times=10_000):
                with lock_mod.held(self.layout, "demo") as taken:
                    self.assertFalse(taken, "a permanent denial must fail open")
        finally:
            lock_mod.limits = real_limits


class TestAReadOnlyCheckoutStillFailsOpenAtOnce(Fixture):
    """The behaviour the original branch existed to provide, kept.

    Without this control the fix above could simply be "retry on everything",
    which would make a read-only checkout pay a full timeout on every call.
    """

    def test_an_unwritable_directory_does_not_wait(self):
        calls = {"n": 0}
        real_access = lock_mod.os.access

        def fake_access(path, mode):
            calls["n"] += 1
            return False

        lock_mod.os.access = fake_access
        try:
            with RaisesOnce(times=10_000) as forced:
                with lock_mod.held(self.layout, "demo") as taken:
                    self.assertFalse(taken)
            self.assertEqual(forced.calls, 1,
                             "it retried against an unwritable directory")
            self.assertGreaterEqual(calls["n"], 1,
                                    "writability was never consulted")
        finally:
            lock_mod.os.access = real_access

    def test_a_read_only_filesystem_still_fails_open_at_once(self):
        with RaisesOnce(times=10_000, err=errno.EROFS) as forced:
            with lock_mod.held(self.layout, "demo") as taken:
                self.assertFalse(taken)
        self.assertEqual(forced.calls, 1, "EROFS is permanent; it must not retry")


class TestTheForcingItselfWorks(Fixture):
    """A control on the control. Without it every assertion above could be
    passing because the patch never fired."""

    def test_the_patch_really_raises(self):
        with RaisesOnce(times=1) as forced:
            with self.assertRaises(PermissionError):
                lock_mod.os.open(str(self.layout.root / "x"), os.O_CREAT)
        self.assertEqual(forced.calls, 1)

    def test_without_the_patch_the_lock_is_taken(self):
        with lock_mod.held(self.layout, "demo") as taken:
            self.assertTrue(taken)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
