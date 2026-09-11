"""One named lock, for every read-modify-write that a wave can run twice.

`state.locked` already held a correct `O_EXCL` lock: it was the only writer in
the ledger that survived eight concurrent processes without losing an update.
But it was welded to one file, so the unit file, the findings and phases ledgers
and the telemetry rotate each had to grow their own copy of the same retry loop —
four more chances to get the stale-reclaim or the release wrong. This is that
loop, named, so there is one of it.

The lock is a file under gitignored `.ctx/runtime/locks/`, created with
`O_CREAT | O_EXCL`, which is atomic on POSIX and on Windows. Holding it is
advisory: nothing stops a writer that never asks. It serialises the writers that
do, which is all a cooperating set of `ctx` processes needs.

Two rules are load-bearing:

* **It fails open.** A lock that cannot be taken is a reason to risk a lost
  update, never a reason to break a session — the same bargain every hook makes.
  `held` yields False in that case instead of raising, so a caller that cares can
  look and a caller that does not can ignore it.
* **A holder only ever unlinks its own lock.** Each holder writes a token into
  the file it created and re-reads it on release. Without that, a holder whose
  lock was reclaimed as stale would delete the *next* holder's lock on the way
  out and leave two writers believing they were alone.
"""

import contextlib
import errno
import os
import time
import uuid

# Five seconds is the general default: long enough to outlast any single
# read-modify-write in this codebase, short enough that a session never appears
# to hang on a lock a dead process left behind.
LOCK_TIMEOUT = 5.0

# Older than this and the lock is rubble from a killed process, not a holder.
# It must stay comfortably above the longest legitimate hold.
LOCK_STALE_SECONDS = 120.0

# `state` keeps the limits it was tuned to. Five seconds was not enough on
# Windows under a wave — CI lost one increment in eighty — where creating and
# unlinking a file costs far more than it does on a POSIX filesystem.
_LIMITS = {"state": (30.0, 60.0)}

# `state.lock` predates this module and sits directly in `runtime/`. Moving it
# would be a rename with no benefit and one cost: a process still running the
# old code would take a different file and serialise against nobody.
_LEGACY_NAMES = {"state": "state.lock"}

LOCKS_DIRNAME = "locks"
_POLL = 0.02
_MAX_SLUG = 80
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def limits(name):
    """`(timeout, stale_seconds)` for one lock name."""
    return _LIMITS.get(str(name), (LOCK_TIMEOUT, LOCK_STALE_SECONDS))


def slugify(name):
    """A filename that cannot leave the locks directory.

    Callers pass logical keys — `f"plan-{slug}"` — and a plan slug reaches the
    ledger from a filename, a CLI argument or a frontmatter field. Anything
    outside `[A-Za-z0-9_-]` becomes a dash, so `..`, `/`, `\\`, a drive letter
    and a NUL all collapse into something that is only ever a name. Two distinct
    keys can collide into one slug; the cost of that is serialising a little more
    than strictly necessary, which is the safe direction.
    """
    text = str(name)
    cleaned = "".join(char if char in _SAFE else "-" for char in text)
    cleaned = cleaned.strip("-")[:_MAX_SLUG].strip("-")
    return cleaned or "unnamed"


def path_for(layout, name):
    """Where the lock for `name` lives. Creates nothing."""
    legacy = _LEGACY_NAMES.get(str(name))
    if legacy:
        return layout.runtime / legacy
    return layout.runtime / LOCKS_DIRNAME / (slugify(name) + ".lock")


def _acquire(path, timeout, stale):
    """Take the lock, or return `(None, None)` having failed open."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            reclaimed = False
            try:
                if time.time() - path.stat().st_mtime > stale:
                    path.unlink()
                    reclaimed = True
            except OSError:
                pass  # it vanished or cannot be stat'd — fall through and retry
            if time.monotonic() >= deadline:
                return None, None
            if not reclaimed:
                time.sleep(_POLL)  # a reclaim retries at once; a live lock waits
            continue
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EROFS, errno.EPERM):
                return None, None  # read-only checkout: nothing to serialise
            return None, None
        token = ("%d %s" % (os.getpid(), uuid.uuid4().hex)).encode("ascii")
        try:
            os.write(handle, token)
        except OSError:
            token = b""  # unwritable token: still ours, just unprovable
        return handle, token


def _release(handle, path, token):
    """Close, then unlink only if the file on disk is still the one we made."""
    try:
        os.close(handle)
    except OSError:
        pass
    try:
        if path.read_bytes() != token:
            return  # reclaimed as stale and retaken — not ours to delete
    except OSError:
        return
    try:
        path.unlink()
    except OSError:
        pass


@contextlib.contextmanager
def held(layout, name):
    """Serialise one read-modify-write under `.ctx/runtime/locks/<name>.lock`.

    `name` is slugified; callers pass a logical key such as f"plan-{slug}" or
    "telemetry". Yields True when the lock was actually taken and False when it
    was not — callers that must know may check, and callers that only want
    best-effort serialisation may ignore it.

    Fails open, exactly as `state.locked` does today: a lock that cannot be
    taken is a reason to risk a lost update, never a reason to break a session.
    EACCES and EROFS break immediately (a read-only checkout has nothing to
    serialise against).
    """
    timeout, stale = limits(name)
    path = path_for(layout, name)
    handle = token = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # cannot even make the directory: proceed unserialised
    else:
        handle, token = _acquire(path, timeout, stale)
    try:
        yield handle is not None
    finally:
        if handle is not None:
            _release(handle, path, token)
