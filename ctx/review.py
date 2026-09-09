"""The review package: one file a reviewer reads in a single call.

A reviewer needs to know what changed, against what it was supposed to change,
and whether anything changed that nobody declared. The usual way to answer that
is a commit range — which costs a commit, because the implementer has to commit
before it can be reviewed. The review protocol then dictates the project's git
history, and concurrent units interleave into a range nothing can untangle.

This builds the same answer from two content snapshots instead, so review costs
no commits at all and a project with no repository reviews identically. Three
things follow that a commit range cannot do: a whole wave can be reviewed
concurrently with each unit's diff staying clean; the `owns` contract is checked
mechanically rather than being asserted in prose nothing enforced; and the diff
survives the working tree moving on, because the bytes were captured.

The package is deliberately one file. Everything the reviewer needs is in it, so
the review costs one Read — and the diff never enters the orchestrator's own
context, which is what keeps a twenty-unit plan affordable.
"""

import difflib

from . import plan, redact, snapshot

REVIEW_SUBDIR = "reviews"

# Enough context to judge a hunk without re-reading the file. Ten lines is what
# a reviewer needs to see the function a change sits in; three is not.
DIFF_CONTEXT = 10

# A package larger than this is not read, it is skimmed — and a skimmed review
# is worse than an honest refusal, because it looks like one that happened.
MAX_PACKAGE_BYTES = 300_000


def review_dir(layout):
    return layout.runtime / REVIEW_SUBDIR


def package_path(layout, slug, unit_name, round_number=1):
    return review_dir(layout) / f"{slug}-{unit_name}-r{round_number}.md"


def before_key(slug, unit_name):
    return f"{slug}@{unit_name}@before"


def after_key(slug, unit_name, round_number=1):
    return f"{slug}@{unit_name}@r{round_number}"


def capture_before(layout, config, unit, slug, root):
    """Snapshot the tree before a unit is dispatched."""
    return snapshot.capture(
        layout, config, before_key(slug, unit.name), root,
        content_paths=list(unit.owns) + list(unit.reads),
    )


def wave_scope(layout, slug, unit):
    """(patterns, siblings) — the paths this unit's review may treat as declared.

    `snapshot.capture` fingerprints the whole project, on purpose: that is what
    makes a write to a path nobody declared visible at all. But `subagent`-tier
    units of one wave run in *one working tree*, several Task calls deep, so a
    sibling's write to its own declared path lands between this unit's before
    and after captures. Checking the delta against this unit's `owns` alone
    then reports that sibling's legitimate edit as a scope violation — and the
    package calls a violation mechanical and "not open to argument", which
    makes it Critical, which blocks the done-gate. Every wave of two or more
    units deadlocked its own gate on N−1 false Criticals.

    So the check is widened by exactly the amount concurrency costs it, and no
    more: this unit's `owns`, plus the `owns` of the other units in the *same
    wave* that are not `done`. Those are the units that may be writing to this
    tree right now. A path no unit in the wave declared is still a violation,
    which keeps the one mechanical protection this project has against a unit
    writing wherever it likes.

    Two exclusions are deliberate. A unit in a *different* wave is not running
    now — its `owns` is not an excuse for a path changing, or the check would
    degrade to "anything any unit in the plan ever claimed". A `done` unit has
    already been reviewed and its writes already landed; a later change to its
    paths is somebody else's, and belongs in a report.

    When the wave cannot be determined — no plan on disk, no `wave` field and
    no derivable one — this returns the unit's own `owns` unchanged. That is
    the pre-fix behaviour: noisy, but it fails towards reporting too much
    rather than towards silently excusing a stray write.
    """
    siblings = _concurrent_siblings(layout, slug, unit)
    patterns = list(unit.owns)
    for _name, owns in siblings:
        patterns.extend(owns)
    return patterns, siblings


def _concurrent_siblings(layout, slug, unit):
    """[(name, owns)] for the wave's other not-done units, sorted by name.

    The wave is read from the unit's own `wave:` field where it has one, which
    is what `ctx plan-check` writes back to disk and what the board and the
    dispatcher schedule from. Where it has none, the wave is derived from the
    dependency graph the same way `plan.waves` derives it for everything else —
    *not* by matching one missing `wave` against another, which would put every
    unscheduled unit in the plan into one enormous wave and hand the reviewed
    unit a free pass over paths nothing is concurrently writing.
    """
    units = plan.load_units(layout, slug)
    members = None
    if unit.wave is not None:
        members = [u for u in units if u.wave == unit.wave]
    else:
        grouped, _problems = plan.waves(units)
        for _level, candidates in sorted(grouped.items()):
            if any(u.name == unit.name for u in candidates):
                members = candidates
                break
    if not members:
        return []
    return [
        (u.name, list(u.owns))
        for u in sorted(members, key=lambda u: u.name)
        if u.name != unit.name and u.status != "done" and u.owns
    ]


def build(layout, config, unit, slug, root, round_number=1, previous=None):
    """Write the review package. Returns (path, stats, problem).

    `previous` names the snapshot to diff *from*. For the first round that is the
    pre-dispatch capture; for a fix round it is the snapshot the last review saw,
    so a re-review is scoped to the fix rather than re-litigating the whole unit.
    """
    base_key = previous or before_key(slug, unit.name)
    base = snapshot.load(layout, base_key)
    if base is None:
        return None, {}, (
            f"no snapshot to compare against — `ctx snapshot {unit.name} "
            "--phase before` must run before the work does, which is what "
            "`ctx start` does for you"
        )

    head_key = after_key(slug, unit.name, round_number)
    head = snapshot.capture(
        layout, config, head_key, root,
        content_paths=list(unit.owns) + list(unit.reads),
    )
    delta = snapshot.compare(base, head)
    # Not `unit.owns`: a wave shares one working tree, so a sibling's write to
    # its own declared path is in this delta and is not this unit's violation.
    # See `wave_scope` for why that widening is the smallest one that works.
    scope, siblings = wave_scope(layout, slug, unit)
    stray = snapshot.out_of_scope(delta, scope)
    # A path nobody declared has no captured "before", so its bytes were never
    # kept. Fill in the "after" side now that we know which paths those are —
    # otherwise the violation is named but never shown.
    if stray:
        head = snapshot.store_extra(layout, config, head_key, root, stray) or head

    text = _render(layout, config, unit, slug, delta, stray, base_key, head_key,
                   round_number, siblings)
    path = package_path(layout, slug, unit.name, round_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `newline=""` writes the string's own "\n" through untranslated. On
    # Windows the default would rewrite every one to "\r\n", so the file on
    # disk would be larger than the text that produced it — and `bytes` below
    # counts the text while `dispatch_stats` stats the file. Those two numbers
    # size the reviewer's model, so letting them disagree by one byte per line
    # would draw a dearer seat on Windows than on Linux for the same package.
    # `Path.write_text` only learned `newline` in 3.10 and this ships 3.8+.
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    stats = {
        "bytes": len(text.encode("utf-8")),
        "added": len(delta.added),
        "modified": len(delta.modified),
        "deleted": len(delta.deleted),
        "out_of_scope": len(stray),
        "truncated": delta.truncated,
        "round": round_number,
        "head_key": head_key,
    }
    return path, stats, ""


def dispatch_stats(layout, slug, unit, round_number=1):
    """Package bytes and out-of-scope count for a package `build()` already
    wrote — read back, not recomputed.

    `build()` works out both of these numbers while it captures the head
    snapshot and renders the package text, and hands them back in its `stats`
    dict — but only to the caller that ran it. Sizing the *reviewer's* model
    needs the same two numbers for a different reason: a package too large to
    read comfortably, or a unit with a real scope violation, wants a stronger
    reviewer than a two-line, in-scope fix does. Calling `build()` again to get
    them would re-walk the whole tree, re-store every blob under
    `content_paths`, and rewrite the package file a second time — all to
    answer a question the first `build()` already answered and left on disk.

    This reads that answer back instead: the package file's own size on disk
    for `bytes`, and the base/head manifests `build()` already stored —
    compared again, which is pure computation over data already captured, not
    a second capture — for `out_of_scope`. Nothing here writes anything.

    The scope those paths are judged against is `wave_scope`'s, exactly as in
    `build` — otherwise the number that sizes the reviewer would count a wave
    sibling's declared path as a violation the package itself does not report,
    and a routine wave would be routed to a stronger model on a fiction. That
    call re-reads the wave's unit files, which is a read of frontmatter already
    on disk, not a capture.

    Every key involved (`package_path`, `after_key`, `before_key`) is scoped to
    this `slug`/`unit.name`/`round_number`, which is what keeps two units
    reviewed in the same wave from ever reading each other's numbers — there is
    no shared or last-writer state anywhere in this function to contaminate.

    Returns `None` when no package has been built yet for this
    `(slug, unit, round_number)` — there is nothing on disk to size against.
    """
    path = package_path(layout, slug, unit.name, round_number)
    if not path.is_file():
        return None
    head = snapshot.load(layout, after_key(slug, unit.name, round_number))
    if head is None:
        return None
    base_key = (after_key(slug, unit.name, round_number - 1) if round_number > 1
                else before_key(slug, unit.name))
    base = snapshot.load(layout, base_key)
    delta = snapshot.compare(base, head)
    scope, _siblings = wave_scope(layout, slug, unit)
    stray = snapshot.out_of_scope(delta, scope)
    return {
        "bytes": path.stat().st_size,
        "out_of_scope": len(stray),
    }


def _render(layout, config, unit, slug, delta, stray, base_key, head_key,
            round_number, siblings=()):
    patterns = config.get("redact") or []
    out = [
        f"# Review package — unit `{unit.name}` of plan `{slug}`, round {round_number}",
        "",
        "Built by diffing two content snapshots, not a commit range: nothing here",
        "was committed, and the project may have no repository at all. This file is",
        "your complete view of the change — do not run git to re-derive it.",
        "",
        "## Objective",
        "",
        (unit.doc.section("objective") or "(none stated)").strip(),
        "",
        "## Acceptance criteria",
        "",
    ]
    criteria = unit.doc.list_items("acceptance criteria", "criteria")
    out += [f"{i}. {c}" for i, c in enumerate(criteria, 1)] or ["(none stated)"]

    interfaces = unit.doc.section("interfaces").strip()
    if interfaces:
        out += ["", "## Interfaces this unit publishes", "", interfaces]

    out += [
        "",
        "## Declared scope",
        "",
        f"- **owns**: {', '.join(unit.owns) or '(nothing declared)'}",
        f"- **reads**: {', '.join(unit.reads) or '(nothing)'}",
        f"- **forbid**: {', '.join(unit.forbid) or '(nothing)'}",
    ]
    # Only when the wave actually has other units running. A solo unit's
    # package must read exactly as it always did — there is no concurrency to
    # explain, and a line explaining one would be noise at best and a false
    # implication that something else touched this tree at worst.
    if siblings:
        out += [
            "- **running alongside** (same wave, not yet done, sharing this "
            "working tree): "
            + "; ".join(f"`{name}` owns {', '.join(owns)}"
                        for name, owns in siblings),
        ]
    out += [
        "",
        "## Files changed",
        "",
    ]
    # The ledger is rewritten by every `ctx` command, so listing it here is pure
    # noise in the section a reviewer reads first — and noise in a review is how
    # a reader learns to skim the part that matters.
    added = [p for p in delta.added if not snapshot.is_ledger(p)]
    modified = [p for p in delta.modified if not snapshot.is_ledger(p)]
    deleted = [p for p in delta.deleted if not snapshot.is_ledger(p)]
    if not (added or modified or deleted):
        out.append("(nothing changed outside the ledger's own bookkeeping)")
    for path in added:
        out.append(f"- added     {path}")
    for path in modified:
        out.append(f"- modified  {path}")
    for path in deleted:
        out.append(f"- deleted   {path}")
    if delta.truncated:
        out += [
            "",
            "**This snapshot hit the file cap**, so the listing is incomplete and",
            "deletions are not reported at all — a truncated snapshot cannot tell a",
            "deleted file from one past the cap. Treat absence as unknown, and raise",
            "`review.max_files` or widen `review.ignore` before trusting this section.",
        ]

    # The wording has to describe the check that actually ran. With siblings in
    # the wave the rule is no longer "this unit's `owns`, absolutely" — it is
    # "declared by *someone* running in this wave" — and a reviewer told the
    # stricter rule would either raise a finding the code did not make, or
    # learn that the section's prose is approximate. Both are worse than a
    # sentence more of text.
    out += ["", "## Scope violations", ""]
    if stray and siblings:
        out += [
            "These paths changed and are **not** covered by this unit's `owns`, nor",
            "by the `owns` of any other unit still running in this wave (listed under",
            "Declared scope). Nobody in the wave declared them. This was decided",
            "mechanically — no model judged it, and it is not open to argument.",
            "Report it as Critical.",
            "",
        ]
        out += [f"- {path}" for path in stray]
    elif stray:
        out += [
            "These paths changed and are **not** covered by the unit's `owns`. This",
            "was decided mechanically — no model judged it, and it is not open to",
            "argument. Report it as Critical.",
            "",
        ]
        out += [f"- {path}" for path in stray]
    elif siblings:
        out += [
            "None — every changed path is within this unit's declared `owns`, or",
            "within the declared `owns` of a unit running alongside it in this wave.",
            "A wave shares one working tree, so a sibling's write to its own declared",
            "path appears in this diff; it is that unit's to answer for, not this",
            "one's. A path nobody in the wave declared would still be listed here.",
        ]
    else:
        out.append("None — every changed path is within the declared `owns`.")

    out += ["", "## Diff", ""]
    body = _diff_body(layout, delta, base_key, head_key, siblings)
    out.append(body)
    return redact.scrub("\n".join(out) + "\n", patterns)


def _diff_body(layout, delta, base_key, head_key, siblings=()):
    """Unified diffs for every changed path, bounded in total size.

    `siblings` is the wave's other running units as `[(name, owns)]`. A path one
    of them declared has no bytes in either snapshot — this unit's captures only
    store what it declared, and `build` no longer treats a sibling's path as a
    violation worth filling in. Left to the generic "not shown" line it would be
    blamed on a size cap, which is false and sends a reviewer looking for a
    problem that is not there. It gets its own line, naming the unit whose work
    it is, so the reviewer knows to leave it to that unit's own package.
    """
    sibling_owns = [pattern for _name, owns in siblings for pattern in owns]
    chunks, used, omitted, elsewhere = [], 0, [], []
    for path in delta.changed:
        if snapshot.is_ledger(path):
            continue
        before = snapshot.stored_text(layout, base_key, path)
        after = snapshot.stored_text(layout, head_key, path)
        if before is None and after is None:
            # Neither side was stored: binary, over the size cap, or outside the
            # declared content set on both snapshots. Say which paths those are
            # rather than silently omitting them.
            if snapshot.covers(path, sibling_owns):
                elsewhere.append(path)
            else:
                omitted.append(path)
            continue
        chunk = "".join(difflib.unified_diff(
            (before or "").splitlines(True),
            (after or "").splitlines(True),
            fromfile=f"before/{path}" if before is not None else "/dev/null",
            tofile=f"after/{path}" if after is not None else "/dev/null",
            n=DIFF_CONTEXT,
        ))
        if not chunk:
            continue
        if used + len(chunk) > MAX_PACKAGE_BYTES:
            omitted.append(path)
            continue
        used += len(chunk)
        chunks.append("```diff\n" + chunk.rstrip("\n") + "\n```")

    if elsewhere:
        chunks.append(
            "**Another unit's work** (changed in this shared working tree by a unit "
            "running alongside this one, which declared it): "
            + ", ".join(sorted(elsewhere)) + ". Not shown and not yours to review — "
            "it arrives in that unit's own package."
        )
    if omitted:
        chunks.append(
            "**Not shown** (binary, over the size cap, or the package budget was "
            "spent): " + ", ".join(sorted(omitted)) + ". Read those paths directly "
            "if a finding depends on them, and say in your report that you did."
        )
    return "\n\n".join(chunks) if chunks else "(no textual change to show)"
