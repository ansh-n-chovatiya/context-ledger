"""Machine-local pointers: which level we are at and what is active.

Lives in `.ctx/runtime/` and is gitignored — it is the only part of the ledger
that is not meant to be reviewed. Writes are atomic so a killed session cannot
leave a half-written pointer that breaks the next SessionStart.
"""

import contextlib
import json
import os
import tempfile

from . import config as config_mod, lock as lock_mod

# A wave means several `ctx` processes writing this file at once. `os.replace`
# already made each *write* atomic, but load-then-save is not: two processes that
# both read before either wrote leave one of the updates gone, which is how
# attempt counts under-count and a `unit` claim disappears.
#
# The loop that prevents that now lives in `ctx.lock`, because four other
# ledgers need it too. The limits stay here, and stay generous, because giving
# up means taking the lost update the lock exists to prevent: five seconds was
# not enough on Windows under a wave — CI lost one increment in eighty — where
# creating and unlinking a file costs far more than it does on a POSIX
# filesystem. `lock.limits` is the one source of both numbers.
LOCK_NAME = "state"
LOCK_TIMEOUT, LOCK_STALE_SECONDS = lock_mod.limits(LOCK_NAME)

EMPTY = {
    "schema": config_mod.SCHEMA,
    "level": "0",
    "task": None,
    "spec": None,
    "plan": None,
    "unit": None,
    "attempts": {},
    "last_session": None,
}


def load(layout):
    data = dict(EMPTY)
    path = layout.state
    if path.is_file():
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(found, dict):
                data.update(found)
        except (OSError, ValueError):
            pass  # a corrupt pointer degrades to L0 rather than failing
    data["level"] = config_mod.normalise_level(data.get("level"))
    if not isinstance(data.get("attempts"), dict):
        data["attempts"] = {}
    return data


def save(layout, data):
    layout.runtime.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(layout.runtime), delete=False, suffix=".tmp"
    )
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, layout.state)
    return data


@contextlib.contextmanager
def locked(layout):
    """Hold the state lock for one read-modify-write, or give up and proceed.

    Failing open is deliberate and consistent with every other hook: a lock we
    cannot take is a reason to risk a lost update, never a reason to break a
    session. A lock left behind by a killed process is reclaimed once it is
    older than `LOCK_STALE_SECONDS`.

    The file stays at `.ctx/runtime/state.lock` rather than moving under
    `runtime/locks/` with the others: a process still running the older code
    would take the new path, serialise against nobody, and lose exactly the
    update this exists to keep.
    """
    with lock_mod.held(layout, LOCK_NAME) as taken:
        yield taken


def update(layout, **changes):
    with locked(layout):
        data = load(layout)
        data.update(changes)
        return save(layout, data)


def attempts(layout, key):
    return int(load(layout).get("attempts", {}).get(key, 0))


def bump_attempts(layout, key):
    with locked(layout):
        data = load(layout)
        counts = data.setdefault("attempts", {})
        counts[key] = int(counts.get(key, 0)) + 1
        save(layout, data)
        return counts[key]


def clear_attempts(layout, key=None):
    with locked(layout):
        data = load(layout)
        if key is None:
            data["attempts"] = {}
        else:
            data.get("attempts", {}).pop(key, None)
        return save(layout, data)


def set_nudge(layout, message):
    """Queue a one-shot correction for the next UserPromptSubmit."""
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        layout.nudge.write_text(message.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def take_nudge(layout):
    """Read and clear the pending nudge. Empty string when there is none."""
    path = layout.nudge
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        path.unlink()
    except OSError:
        pass
    return text
