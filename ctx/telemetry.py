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

One kind of record here is not a measurement at all. Hook durations and
briefing sizes are observed; *spend* is reported. A CLI cannot see what a model
spent — the tokens are billed in a process this one never touches — so the only
honest way to get the number is for whoever ran the wave to hand it over, which
is what `ctx telemetry --spend` does. That makes the spend data partial by
construction: it covers exactly the units somebody remembered to report and no
others. Every surface that prints it therefore prints `SPEND_NOTICE` with it,
and a unit nobody reported reads as *unreported*, never as zero. The point of
collecting it is to check the complexity weights against something other than
the reasoning that produced them, and an unlabelled partial number would be
used for that as though it were complete — worse than having no number at all.

Records live in `.ctx/runtime/`, which is gitignored: this is machine-local
measurement, not project history.
"""

import json
import os

from . import atomic, lock, log

FILENAME = "telemetry.jsonl"

# The logical key `lock.held` slugifies. One lock for the one file.
LOCK_NAME = "telemetry"

MAX_BYTES = 256 * 1024
KEEP_LINES = 400

#: The `event` name a reported-spend record carries. It shares the one jsonl
#: with the timing records — same rotation, same lock, same fail-open bargain —
#: and is told apart by this name rather than by a second file, because a
#: second file would need a second rotate and a second lock for no gain.
SPEND_EVENT = "spend"

#: Printed by every surface that shows a spend figure, without exception. The
#: labelling is load-bearing, not decorative: these numbers exist to be
#: compared against `complexity.score`, and a partial sample read as a full
#: measurement would retune the weights towards whichever units happened to
#: get reported. Kept here, as one constant, so "every surface" is a thing a
#: test can check rather than a habit each call site has to remember.
SPEND_NOTICE = (
    "reported spend is self-reported and incomplete: it covers only units "
    "someone ran `ctx telemetry --spend` for.",
    "a unit with no figure is unreported, not zero — do not read these totals "
    "as a measurement of the wave.",
)


def path_for(layout):
    return layout.runtime / FILENAME


def enabled(config):
    section = (config or {}).get("telemetry")
    if not isinstance(section, dict):
        return True
    return bool(section.get("enabled", True))


def record(layout, event, ms, **fields):
    """Append one measurement. Silent on any failure — never break a hook.

    Returns True if the line reached the file and False if anything at all
    stopped it. Callers in the hot path ignore it, which is why it is a return
    value and not an exception: the guarantee that this never raises is the
    whole reason the module can sit in front of every hook. `record_spend` is
    the one caller that looks, because a person typing `ctx telemetry --spend`
    is owed the truth about whether their number was kept.

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
    except (TypeError, ValueError) as exc:
        # Not a disk problem: a caller handed us a field that will not
        # serialise. Reported at `warn` rather than `error` because the
        # measurement is lost but nothing on disk is wrong.
        log.warn("telemetry.record", log.describe(exc), event=event)
        return False
    # Resolved before the `try`, so the handler below can name the file
    # without calling anything that could raise inside an except block.
    target = None
    try:
        target = path_for(layout)
        layout.runtime.mkdir(parents=True, exist_ok=True)
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
    except (OSError, TypeError, ValueError) as exc:
        # The swallow the module's contract requires, no longer indistinguish-
        # able from success: a read-only mount, a full disk and a permissions
        # error each say so here, and the hook still returns normally.
        log.failure("telemetry.record", exc, event=event, path=target)
        return False
    return True


def record_spend(layout, unit, tokens):
    """Record what a unit actually cost, as reported by whoever ran it.

    Returns True if the number reached the file. Like every other write here
    it never raises — a lost spend record is a lost data point, not a failed
    command — but unlike a hook timing, somebody typed this one on purpose and
    is owed an answer, so the outcome comes back as a bool.

    Takes the `telemetry` lock, because it goes through `record`, which holds
    it across the rotate and the append. `lock.held` is **not re-entrant**:
    calling this from inside a span that already holds the `telemetry` lock
    would deadlock or (given `lock.held` fails open) silently lose the very
    record it was asked to keep. There is no caller inside such a span today
    and there must not be one added — the CLI command is the only caller, and
    it runs with nothing else held.

    `tokens` is validated rather than coerced-and-hoped: a spend of "lots" or
    of -1 is not a smaller measurement, it is an absent one, and writing it
    would put a number into the complexity-weight comparison that means
    nothing.
    """
    name = str(unit or "").strip()
    if not name:
        return False
    try:
        count = int(tokens)
    except (TypeError, ValueError):
        return False
    if count < 0:
        return False
    return record(layout, SPEND_EVENT, 0, unit=name, tokens=count)


def spend_by_unit(layout, limit=400):
    """`{unit: {"tokens": int, "reports": int}}` over the reported spend.

    `reports` is carried alongside the total because one unit can be reported
    more than once — a second round of the same unit is a second cost, and
    summing them into a single figure with no count would hide that a "12,000"
    is two reports of 6,000. It is also the only way a reader can tell a unit
    that was reported as costing zero from one that was reported twice.

    A unit that was never reported is simply absent from this mapping. That
    absence is the data — see `SPEND_NOTICE` — so callers render it as
    "unreported" and never fill it in with a zero.
    """
    totals = {}
    for entry in read(layout, limit):
        if str(entry.get("event") or "") != SPEND_EVENT:
            continue
        name = entry.get("unit")
        tokens = entry.get("tokens")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(tokens, bool) or not isinstance(tokens, int):
            continue
        bucket = totals.setdefault(name, {"tokens": 0, "reports": 0})
        bucket["tokens"] += tokens
        bucket["reports"] += 1
    return totals


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
    except OSError as exc:
        log.failure("telemetry.rotate", exc, path=target)


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
    except OSError as exc:
        # An unreadable file and an empty one both render as "no measurements
        # yet" to every caller. Only the log can tell them apart.
        log.failure("telemetry.read", exc, path=target)
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
        # Reported spend is not a duration. Left in, it would show up as an
        # event with a 0.0ms median in a table about how slow hooks are, which
        # is both meaningless and the kind of row that gets misread as "spend
        # is free". `spend_by_unit` is where it is legible instead.
        if event == SPEND_EVENT:
            continue
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
