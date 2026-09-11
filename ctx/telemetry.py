"""Hook timing and briefing-size records.

The design promised the briefing budget would be *observable rather than
aspirational*. `ctx doctor` measures it on demand, which proves the cap holds
right now but says nothing about what real sessions actually paid. This records
that, so drift shows up as data rather than as a vague sense that things got
slower.

Two constraints shape the implementation. It sits in the hot path of every hook,
so it must be cheap — one append, no parsing. And it must never be the reason a
hook fails, so every operation swallows its own errors and returns.

"No locking" used to be part of that first constraint, and it was measurably
wrong. `_rotate` read the tail and then reopened the destination `"w"`, so every
append landing between the read and the rewrite was discarded: with twenty
concurrent records across a forced rotate, zero survived. A lock that costs one
`O_EXCL` create and one unlink per record is cheaper than losing the
measurement the module exists to take, so `record` now holds the `telemetry`
lock across the rotate *and* the append that follows it — one acquisition, not
two, because a lock taken twice around the two halves serialises nothing. The
rewrite itself goes through `atomic.write_text`, so a reader sees the old file
or the new one and never a truncated one.

The lock still fails open (that is `lock.held`'s whole bargain), so the second
constraint is untouched: a lock that cannot be taken risks a lost record, never
a broken hook.

Records live in `.ctx/runtime/`, which is gitignored: this is machine-local
measurement, not project history.
"""

import json
import os

from . import atomic, lock

FILENAME = "telemetry.jsonl"

# The logical key `lock.held` slugifies. One lock for the one file.
LOCK_NAME = "telemetry"

MAX_BYTES = 256 * 1024
KEEP_LINES = 400


def path_for(layout):
    return layout.runtime / FILENAME


def enabled(config):
    section = (config or {}).get("telemetry")
    if not isinstance(section, dict):
        return True
    return bool(section.get("enabled", True))


def record(layout, event, ms, **fields):
    """Append one measurement. Silent on any failure — never break a hook.

    `model` and `role` are ordinary entries in `**fields`, not dedicated
    parameters — the signature was already open, so giving them a name here
    would only pin a shape callers don't need pinned. Pass them like any
    other field; omit either and it is simply absent from the record rather
    than written as null. `summarise()` is what gives `role` in particular a
    reason to exist: it is the key its `by_role` breakdown groups on.
    """
    try:
        # Rendered before the lock: a record that cannot even be serialised is
        # not worth making twenty other processes wait for.
        payload = {"event": str(event), "ms": round(float(ms), 1)}
        payload.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(payload, sort_keys=True) + "\n"
    except (TypeError, ValueError):
        return
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        target = path_for(layout)
        # One acquisition, spanning the rotate's read-modify-write *and* this
        # append. Locking them separately would leave exactly the window the
        # lock is here to close: an append that lands after another process has
        # read the tail but before it has replaced the file is discarded by the
        # replace. `_rotate` therefore takes no lock of its own — it is only
        # ever called from inside this one, which also keeps the two lock sites
        # from nesting, and `lock.held` is not re-entrant.
        with lock.held(layout, LOCK_NAME):
            _rotate(target)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except (OSError, TypeError, ValueError):
        pass


def _rotate(target):
    """Trim to the most recent KEEP_LINES once the file gets large.

    Checked by size rather than line count so the common path is one stat call.

    Called only with the `telemetry` lock already held — see `record`. The
    rewrite is an atomic replace rather than a reopen-for-write, so even a
    writer that never asked for the lock sees a whole file: the old one or the
    trimmed one, never the half-second during which it is neither.
    """
    try:
        if not target.is_file() or target.stat().st_size <= MAX_BYTES:
            return
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()[-KEEP_LINES:]
        atomic.write_text(target, "".join(lines))
    except OSError:
        pass


def read(layout, limit=400):
    records = []
    target = path_for(layout)
    if not target.is_file():
        return records
    try:
        with target.open("rb") as handle:
            size = target.stat().st_size
            if size > MAX_BYTES:
                handle.seek(size - MAX_BYTES, os.SEEK_SET)
                handle.readline()
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return records
    for line in text.splitlines()[-limit:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            records.append(entry)
    return records


def summarise(layout, limit=400):
    """Per-event count, median and max duration, a role breakdown, and median
    briefing chars.

    `by_role` exists because a hook's overall median hides the number that
    actually matters for dispatch: model choice is made per role, not per
    event, so "review took 400ms on average" says nothing about whether the
    reviewer role in particular is slow. Recording `role` on `record()` was
    free (it already accepted arbitrary fields); this is what makes that data
    legible instead of just sitting in the jsonl unread.
    """
    grouped = {}
    for entry in read(layout, limit):
        event = str(entry.get("event") or "?")
        bucket = grouped.setdefault(event, {"ms": [], "chars": [], "roles": {}})
        has_ms = isinstance(entry.get("ms"), (int, float))
        if has_ms:
            bucket["ms"].append(float(entry["ms"]))
        if isinstance(entry.get("chars"), int):
            bucket["chars"].append(entry["chars"])
        role = entry.get("role")
        if has_ms and isinstance(role, str) and role:
            bucket["roles"].setdefault(role, []).append(float(entry["ms"]))

    rows = []
    for event in sorted(grouped):
        durations = sorted(grouped[event]["ms"])
        chars = sorted(grouped[event]["chars"])
        by_role = {}
        for role in sorted(grouped[event]["roles"]):
            role_ms = sorted(grouped[event]["roles"][role])
            by_role[role] = {"count": len(role_ms), "median_ms": _median(role_ms)}
        rows.append({
            "event": event,
            "count": len(durations),
            "median_ms": _median(durations),
            "max_ms": max(durations) if durations else 0.0,
            "median_chars": _median(chars) if chars else None,
            "by_role": by_role,
        })
    return rows


def _median(values):
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return (float(values[middle - 1]) + float(values[middle])) / 2
