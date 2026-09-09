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

from . import redact, snapshot

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
    stray = snapshot.out_of_scope(delta, unit.owns)
    # A path nobody declared has no captured "before", so its bytes were never
    # kept. Fill in the "after" side now that we know which paths those are —
    # otherwise the violation is named but never shown.
    if stray:
        head = snapshot.store_extra(layout, config, head_key, root, stray) or head

    text = _render(layout, config, unit, slug, delta, stray, base_key, head_key,
                   round_number)
    path = package_path(layout, slug, unit.name, round_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
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


def _render(layout, config, unit, slug, delta, stray, base_key, head_key, round_number):
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

    out += ["", "## Scope violations", ""]
    if stray:
        out += [
            "These paths changed and are **not** covered by the unit's `owns`. This",
            "was decided mechanically — no model judged it, and it is not open to",
            "argument. Report it as Critical.",
            "",
        ]
        out += [f"- {path}" for path in stray]
    else:
        out.append("None — every changed path is within the declared `owns`.")

    out += ["", "## Diff", ""]
    body = _diff_body(layout, delta, base_key, head_key)
    out.append(body)
    return redact.scrub("\n".join(out) + "\n", patterns)


def _diff_body(layout, delta, base_key, head_key):
    """Unified diffs for every changed path, bounded in total size."""
    chunks, used, omitted = [], 0, []
    for path in delta.changed:
        if snapshot.is_ledger(path):
            continue
        before = snapshot.stored_text(layout, base_key, path)
        after = snapshot.stored_text(layout, head_key, path)
        if before is None and after is None:
            # Neither side was stored: binary, over the size cap, or outside the
            # declared content set on both snapshots. Say which paths those are
            # rather than silently omitting them.
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

    if omitted:
        chunks.append(
            "**Not shown** (binary, over the size cap, or the package budget was "
            "spent): " + ", ".join(sorted(omitted)) + ". Read those paths directly "
            "if a finding depends on them, and say in your report that you did."
        )
    return "\n\n".join(chunks) if chunks else "(no textual change to show)"
