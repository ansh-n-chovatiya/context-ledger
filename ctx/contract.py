"""What the unit promised at dispatch, hashed so it cannot be rewritten later.

The `unit-runner` agent holds `Write` and `Edit`, and `verify.is_ledger` exempts
everything under `.ctx/` from the `diff` scope check. That exemption is load
bearing — a wave of concurrent units writes to the ledger constantly, and
counting those writes as scope violations deadlocked every wave of two or more
units on false Criticals (`review.wave_scope`). So the runner's own unit file is,
by design, a file it may write to.

Which means a runner that cannot make the tests pass can edit the test instead:
delete the failing `verify:` entry, append `verified: [rubric, human]`, trim an
acceptance criterion, or delete the blocking findings a reviewer raised against
it. Nothing hashed or compared the contract, so all of that read as success.

The fix is not to narrow `is_ledger` — that re-creates the false Criticals. It is
to record, at dispatch, a digest of the fields that *constitute the promise*, and
to compare against it inside the done-gate.

Three decisions worth keeping:

**`status` is deliberately not in the digest.** Flipping `status:` is the one
edit the runner is supposed to make. Hashing the whole file would refuse the
transition it exists to guard.

**No baseline is not a violation.** A unit dispatched by an older ctx, or one
whose snapshot failed, has nothing to compare against. Failing closed there would
brick every in-flight plan the moment this shipped, so `baseline()` returns
`None` and the gate says so out loud and falls through.

**Findings are sealed as ctx observes them, and the seal never weakens.** A
finding recorded through `ctx findings --add` is written to the seal
authoritatively; a finding merely *read* by a `ctx` command can only add to the
seal or strengthen an entry, never drop one. That asymmetry is what makes
`--set … --status addressed` a legitimate close and a hand-edit of the findings
file a detectable deletion.
"""

import hashlib
import json
import os
import re
import time

from . import findings as findings_mod, frontmatter, review, snapshot

SCHEMA = 1
STORE_SUBDIR = "contracts"

# The promise. `status` is absent on purpose (see the module docstring); so are
# `budget_tokens`, `model` and `tier`, which say what the work costs and who
# runs it, not what it has to achieve.
FIELDS = ("verify", "owns", "reads", "forbid", "depends_on", "verified",
          "acceptance criteria")

# What each field is called in a refusal. The gate prints these to a human who
# has to go and put the file back.
LABELS = {
    "verify": "verify",
    "owns": "owns",
    "reads": "reads",
    "forbid": "forbid",
    "depends_on": "depends_on",
    "verified": "verified (recorded sign-offs)",
    "acceptance criteria": "acceptance criteria",
}


# --------------------------------------------------------------------------- #
# hashing the promise
# --------------------------------------------------------------------------- #

def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable(value):
    """A value rendered so that only a real change moves the hash."""
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _list(meta, key):
    """`Unit._list`'s rules, applied to a bare mapping.

    Sorted, because re-ordering `owns` is not a change to what the unit may
    write — and a digest that trips on formatting teaches people to pass
    `--force`.
    """
    value = meta.get(key) or []
    if isinstance(value, str):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        return []
    return sorted(str(item) for item in value if str(item).strip())


def _entries(meta, key):
    """A list whose entries may be scalars or mappings (`verify`, `reads`)."""
    value = meta.get(key) or []
    if isinstance(value, (str, dict)):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        return []
    return sorted(_stable(entry) for entry in value)


def _criteria(doc):
    """The acceptance-criteria section, whitespace-normalised.

    Re-wrapping a line is not forgery; deleting or rewording a criterion is.
    Blank lines and trailing whitespace go, everything else stays verbatim.
    """
    body = doc.section("acceptance criteria", "acceptance-criteria", "criteria")
    lines = [line.strip() for line in body.splitlines()]
    return "\n".join(line for line in lines if line)


def field_digests(doc):
    """`{field: hex digest}` for one parsed unit document."""
    meta = doc.meta if isinstance(doc.meta, dict) else {}
    return {
        "verify": _sha(_stable(_entries(meta, "verify"))),
        "owns": _sha(_stable(_list(meta, "owns"))),
        "reads": _sha(_stable(_entries(meta, "reads"))),
        "forbid": _sha(_stable(_list(meta, "forbid"))),
        "depends_on": _sha(_stable(_list(meta, "depends_on"))),
        "verified": _sha(_stable(_list(meta, "verified"))),
        "acceptance criteria": _sha(_criteria(doc)),
    }


def combine(fields):
    """One digest over the per-field digests, in a fixed order."""
    joined = "\n".join(f"{name}={fields.get(name, '')}" for name in FIELDS)
    return _sha(joined)


def digest(unit):
    """A stable hex digest of everything `unit` promised, except its status."""
    return combine(field_digests(unit.doc))


def digest_text(text):
    """The same digest, computed from a unit file's bytes."""
    return combine(field_digests(frontmatter.parse(text)))


# --------------------------------------------------------------------------- #
# the seal on disk
# --------------------------------------------------------------------------- #

def store_dir(layout):
    return layout.runtime / STORE_SUBDIR


def _safe(key):
    return re.sub(r"[^A-Za-z0-9._@-]", "-", str(key)) or "unnamed"


def seal_path(layout, slug, unit_name):
    return store_dir(layout) / f"{_safe(f'{slug}@{unit_name}')}.json"


def load_seal(layout, slug, unit_name):
    """The recorded seal, or None when this unit was never dispatched by a ctx
    that knew how to record one."""
    path = seal_path(layout, slug, unit_name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_seal(layout, slug, unit_name, data):
    path = seal_path(layout, slug, unit_name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        return None  # a seal must never be the reason a dispatch fails
    return data


def seal(layout, config, slug, unit, root=None):
    """Record what this unit promised, at the moment it is dispatched.

    Called beside `review.capture_before` for the same reason and at the same
    moment: the only point at which the tree is known to predate the work.

    Best-effort by construction. A dispatch that cannot write a seal still
    dispatches — the gate then reports that no baseline exists rather than
    refusing a unit for something the machine failed to record.
    """
    fields = field_digests(unit.doc)
    existing = load_seal(layout, slug, unit.name) or {}
    data = {
        "schema": SCHEMA,
        "slug": slug,
        "unit": unit.name,
        "path": layout.rel(unit.path),
        "sealed_at": time.time(),
        "digest": combine(fields),
        "fields": fields,
        # Findings survive a re-seal: a unit re-dispatched for a fix round is
        # still answerable to the findings that caused the round.
        "findings": existing.get("findings") or {},
    }
    _write_seal(layout, slug, unit.name, data)
    if root is not None:
        # The unit file is inside `.ctx/`, so `snapshot.capture` fingerprints it
        # — but it stores *bytes* only for `owns` + `reads`, which never covers
        # a unit's own file. Filling it in here gives `baseline` a second source
        # and lets a reviewer see the contract as it was dispatched.
        try:
            snapshot.store_extra(
                layout, config, review.before_key(slug, unit.name), root,
                [layout.rel(unit.path),
                 layout.rel(findings_mod.path_for(layout, slug, unit.name))],
            )
        except OSError:
            pass
    return data


# --------------------------------------------------------------------------- #
# comparing against it
# --------------------------------------------------------------------------- #

def _baseline_fields(layout, slug, unit):
    """`{field: digest}` as of dispatch, or None when there is no baseline."""
    recorded = load_seal(layout, slug, unit.name)
    if recorded and isinstance(recorded.get("fields"), dict):
        return recorded["fields"]
    # Older dispatches recorded no seal. If the `before` snapshot happens to
    # hold the unit's bytes, that is just as good a baseline.
    text = snapshot.stored_text(
        layout, review.before_key(slug, unit.name), layout.rel(unit.path)
    )
    if text is None:
        return None
    return field_digests(frontmatter.parse(text))


def baseline(layout, slug, unit):
    """The digest recorded at dispatch, or None when there is nothing to
    compare against."""
    fields = _baseline_fields(layout, slug, unit)
    return None if fields is None else combine(fields)


def compare(layout, slug, unit):
    """`(ok, changed)` — has this unit's contract survived since dispatch?

    `changed` names fields, and any finding that was deleted or downgraded
    behind ctx's back. With no baseline the answer is `(True, [])`: see
    `baseline`. Callers that want to say so out loud check `baseline(...) is
    None` themselves.
    """
    changed = []
    before = _baseline_fields(layout, slug, unit)
    if before is not None:
        now = field_digests(unit.doc)
        for name in FIELDS:
            if name in before and before[name] != now.get(name):
                changed.append(LABELS.get(name, name))
    changed += findings_drift(layout, slug, unit.name)
    return (not changed), changed


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #

def _rank(severity):
    """How blocking a severity is. Higher is harder to ignore."""
    order = {"critical": 3, "important": 2, "minor": 1}
    return order.get(str(severity).lower(), 0)


def _as_seal(finding):
    return {
        "severity": finding.severity,
        "status": finding.status,
        "summary": finding.summary,
    }


def _stronger(old, new):
    """The state a reviewer would be less happy to see erased."""
    if _rank(new.get("severity")) > _rank(old.get("severity")):
        return new
    if _rank(new.get("severity")) < _rank(old.get("severity")):
        return old
    if old.get("status") == "open" or new.get("status") != "open":
        return old
    return new


def seal_findings(layout, slug, unit_name, ledger, authoritative=False):
    """Record the findings against a unit as ctx currently sees them.

    `authoritative=True` is for the two commands that *are* the legitimate way
    to move a finding — `ctx findings --add` and `--set`. Everything else only
    observes: it may add an entry or strengthen one, never drop or soften one,
    so a hand-edit of the findings file cannot launder itself by running a
    read-only ctx command afterwards.
    """
    data = load_seal(layout, slug, unit_name)
    if data is None:
        # Nothing was sealed at dispatch, so there is no baseline for this unit
        # and inventing one here would refuse work for a change nobody can see.
        return None
    sealed = dict(data.get("findings") or {})
    for finding in ledger.findings:
        key = str(finding.id)
        fresh = _as_seal(finding)
        if authoritative or key not in sealed:
            sealed[key] = fresh
        else:
            sealed[key] = _stronger(sealed[key], fresh)
    data["findings"] = sealed
    return _write_seal(layout, slug, unit_name, data)


def findings_drift(layout, slug, unit_name):
    """Sealed findings that were deleted or downgraded behind ctx's back."""
    data = load_seal(layout, slug, unit_name)
    if data is None:
        return []
    sealed = data.get("findings") or {}
    if not sealed:
        return []
    ledger = findings_mod.load(layout, slug, unit_name)
    current = {str(f.id): f for f in ledger.findings}
    drift = []
    for key in sorted(sealed, key=lambda k: (len(k), k)):
        was = sealed[key]
        if _rank(was.get("severity")) < _rank("important"):
            continue  # a minor finding never blocked anything
        summary = str(was.get("summary") or "").strip()
        detail = f": {summary}" if summary else ""
        finding = current.get(key)
        if finding is None:
            drift.append(
                f"finding [{key}] ({was.get('severity')}) was deleted from the "
                f"findings file{detail}"
            )
            continue
        if _rank(finding.severity) < _rank(was.get("severity")):
            drift.append(
                f"finding [{key}] was downgraded from {was.get('severity')} to "
                f"{finding.severity}{detail}"
            )
        elif was.get("status") == "open" and finding.status != "open":
            drift.append(
                f"finding [{key}] was moved from open to {finding.status} "
                f"without `ctx findings --set`{detail}"
            )
    return drift


def discard(layout, slug, unit_name):
    """Forget a unit's seal. For tests and for `ctx` cleanup paths."""
    path = seal_path(layout, slug, unit_name)
    try:
        os.remove(path)
    except OSError:
        pass
