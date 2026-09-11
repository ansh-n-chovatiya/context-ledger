"""Phase gates: the mechanism behind "no fix before a failing reproduction".

A discipline that only lives in a document — "reproduce it, locate it, then
fix it" — survives exactly as long as everyone remembers to follow it. The
`kind: bug` preset makes that order a fact the code can check: `fix` refuses
to open until `reproduce` has a recorded run that actually failed and
`locate` has evidence pointing at where, and `guard` refuses until `fix` has
made the *same* reproduction command pass. Nothing here is enforced by
convention; `can_enter` is the only door, and it is closed by default.

Two design choices carry the module, both borrowed from `ctx.findings` on
purpose rather than reinvented:

**A refusal is a return value, not an exception.** `can_enter` and `record`
run inside an agent turn — raising there costs the turn and the work it was
about to do. Both return `(False, why)` instead, so the caller can read the
reason and try the right thing next, in the same breath.

**Records live in the work file's body, not its frontmatter.** A `locate`
phase's evidence is a `file:line` reference, or a diff excerpt, or command
output — exactly the multi-line content `miniyaml.dumps` cannot round-trip
through frontmatter (it writes a multi-line string raw, and the next `loads`
dies on its second line). So a phase's outcome is rendered as a fenced block
in the body, the same way a finding's evidence is, and for the same reason:
the ledger must not be corruptible by the content it is asked to hold.

The general mechanism and the bug preset are kept apart deliberately.
`for_unit` answers "what phases, in what order" for *any* unit that declares
`phases: [...]`, and that ordering carries no assumption about what a phase
means — a non-bug unit gates strictly in the order it named, nothing more.
`kind: bug` is the one preset that attaches meaning (non-zero exit, `file:line`
evidence, the same command exiting zero) to specific phase names, and that
meaning is opt-in: it only turns on for a unit that actually declared itself
a bug.

A third choice is borrowed from `ctx.findings` for the same reason the other
two were: **the gate check and the write it authorises are one locked
read-modify-write.** `record` used to load the ledger, ask `can_enter` about
what it had loaded, and then render the whole file back — so a concurrent
`record` on the same unit both erased the other's entry and answered its gate
question against a file that no longer existed in that form. Both halves now
happen inside a single `lock.held(layout, f"plan-{slug}")`: the plan, not the
unit, because two units of one plan share `plan.json` and the round counter,
and a per-unit lock would serialise nothing that actually collides. One
acquisition, never two — `lock.held` is not re-entrant, so `save()` takes no
lock of its own and `_exclusive` is the module's only lock site.
"""

import contextlib
import datetime
import re

from . import config as config_mod, frontmatter, lock

# The bug preset: four phases, gated in this order, each with a specific
# completion rule enforced by `can_enter` — see the module docstring. A unit
# that sets `kind: bug` gets exactly these phases regardless of any `phases:`
# it might also declare, because the preset is what makes the discipline a
# fact rather than a naming convention a unit could opt out of by spelling
# the phases slightly differently.
BUG = ("reproduce", "locate", "fix", "guard")

# What `guard` asks the verifier to judge. It is not evaluated by this module
# — `guard` is a `rubric` check, the same kind `config.PROFILES` already routes
# to `/ctx:verify` and a model — so the check dict travels with the Phase and
# the caller (dispatch) hands it to `verify.run` unchanged.
GUARD_ABOUT = (
    "the fix addresses the cause `locate` pointed at, and the reproduction "
    "recorded in `fix` is the same command `reproduce` failed on — not a "
    "different, easier check swapped in to make the phase pass"
)

_PHASE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FILE_LINE = re.compile(r"[^\s:,()]+:[0-9]+")

_HEADING = re.compile(r"^###\s+\[(\d+)\]\s+([a-z0-9_-]+)\s*(?:—\s*(.*))?$")
_FIELD = re.compile(r"^-\s+(command|exit_code|when)\s*:\s*(.*)$")
_BLOCK = re.compile(r"^(evidence|note)\s*:\s*$")
_FENCE = re.compile(r"^(`{3,})\s*$")
_BACKTICKS = re.compile(r"`+")


class Phase:
    """One named step in a unit's gate. `check` is only ever populated for the
    bug preset's `guard`, and is the exact dict shape `verify.ordered` and
    `verify.label_of` already know how to read — a `rubric` check with an
    `about` — so a caller can hand it to `verify.run` without translating."""

    def __init__(self, name, *, check=None):
        self.name = _norm(name)
        self.check = dict(check) if check else {}

    def __repr__(self):
        return f"<Phase {self.name}>"

    def __eq__(self, other):
        return isinstance(other, Phase) and self.name == other.name

    def __hash__(self):
        return hash(self.name)


def for_unit(unit):
    """The ordered phases a unit gates through, or `[]` if it declared none.

    `kind: bug` always wins: it is the preset that exists precisely so a bug
    unit cannot quietly opt out of "reproduce before fix" by declaring its own
    `phases:` instead. Anything else uses exactly what it declared, in that
    order, with no meaning attached beyond the order itself — see criterion 6
    in the unit contract: a non-bug unit's `phases:` is pure sequencing.
    """
    if _kind(unit) == "bug":
        return [
            Phase("reproduce"), Phase("locate"), Phase("fix"),
            Phase("guard", check={"kind": "rubric", "about": GUARD_ABOUT}),
        ]
    declared = list(getattr(unit, "phases", None) or [])
    return [Phase(name) for name in declared if _PHASE_NAME.match(_norm(name))]


def can_enter(unit, ledger, phase):
    """Whether `phase` may be started now, given what `ledger` already holds.

    Returns `(True, "")` or `(False, why)` — never raises. `why` always names
    the specific phase that is missing or unsatisfied, because a refusal that
    just says "not allowed" reads as arbitrary and teaches nothing about what
    to do next.
    """
    target = _norm(phase)
    declared = for_unit(unit)
    names = [p.name for p in declared]
    if target not in names:
        available = ", ".join(names) or "(none declared)"
        return False, f"{target!r} is not one of this unit's declared phases: {available}"

    if _kind(unit) == "bug":
        return _bug_can_enter(unit, ledger, target)

    index = names.index(target)
    missing = [name for name in names[:index] if not ledger.for_phase(name)]
    if missing:
        verb = "has" if len(missing) == 1 else "have"
        return False, (
            f"{target!r} is locked until {', '.join(missing)} {verb} been recorded "
            f"— phases gate in the declared order: {', '.join(names)}"
        )
    return True, ""


def record(layout, slug, unit, phase, *, command="", exit_code=None, evidence="", note=""):
    """Persist one phase outcome — but only if `can_enter` says the phase is
    open. This is the enforcement point, not `can_enter` alone: a caller that
    forgot to check the gate before doing the work still cannot get the
    outcome written, because the write is where "no fix before a failing
    reproduction" would otherwise quietly stop being a fact and go back to
    being a request.

    Returns `(True, ledger)` on success, `(False, why)` on refusal — the same
    shape as `findings.Ledger.set_status`, for the same reason: this runs
    inside an agent turn, where raising costs the turn and the work with it.
    """
    ledger = load(layout, slug, unit.name)
    # The gate question and the write it authorises are the same
    # read-modify-write: `_exclusive` re-reads the file under the plan lock,
    # `can_enter` is answered against *that*, and the entry is appended before
    # the lock is released. Asking outside the lock would let a `reproduce`
    # recorded by another process arrive between the refusal and the retry —
    # or, worse, let a `fix` be authorised by a `reproduce` that a concurrent
    # whole-file write was in the middle of erasing. `_add` is the unlocked
    # half of `add`: calling `add` here would take the lock a second time, and
    # it is not re-entrant.
    with ledger._exclusive():
        ok, why = can_enter(unit, ledger, phase)
        if not ok:
            return False, why
        ledger._add(phase, command=command, exit_code=exit_code,
                    evidence=evidence, note=note)
    return True, ledger


# --------------------------------------------------------------------------- #
# bug-preset satisfaction rules
# --------------------------------------------------------------------------- #

def _bug_can_enter(unit, ledger, phase):
    if phase == "reproduce":
        # Always open: there is nothing to reproduce a bug's absence of, so
        # the first phase has no prerequisite to gate on.
        return True, ""
    if phase == "locate":
        ok, why = _reproduce_satisfied(unit, ledger)
        if not ok:
            return False, f"'locate' is locked: {why}"
        return True, ""
    if phase == "fix":
        missing = []
        ok, why = _reproduce_satisfied(unit, ledger)
        if not ok:
            missing.append(f"reproduce ({why})")
        ok, why = _locate_satisfied(unit, ledger)
        if not ok:
            missing.append(f"locate ({why})")
        if missing:
            return False, "'fix' is locked until " + " and ".join(missing) + " are recorded"
        return True, ""
    if phase == "guard":
        ok, why = _fix_satisfied(unit, ledger)
        if not ok:
            return False, f"'guard' is locked: {why}"
        return True, ""
    return False, f"{phase!r} is not a recognised bug phase"


def _reproduce_satisfied(unit, ledger):
    """Only a recorded **non-zero** exit counts. This is the inversion the
    whole preset exists to enforce: a reproduction that passes has reproduced
    nothing, and letting it satisfy the phase would let a bug unit skip
    straight to `fix` with no evidence the bug is even real."""
    entries = ledger.for_phase("reproduce")
    if not entries:
        return False, "reproduce has not been recorded yet"
    if not any(e.exit_code is not None and e.exit_code != 0 for e in entries):
        return False, (
            "reproduce has been recorded, but every recorded run exited zero — "
            "the reproduction has to actually fail before anything else may happen"
        )
    return True, ""


def _locate_satisfied(unit, ledger):
    """A `file:line` reference has to be present in the evidence. A bare
    assertion ("it's in the auth code somewhere") is not evidence — it is the
    same guess a reviewer would reject, and this gate holds it to the same
    standard the review loop already does."""
    entries = ledger.for_phase("locate")
    if not entries:
        return False, "locate has not been recorded yet"
    if not any(_FILE_LINE.search(e.evidence) for e in entries):
        return False, (
            "locate has been recorded, but with no file:line evidence — a bare "
            "assertion of where the bug lives does not satisfy this phase"
        )
    return True, ""


def _fix_satisfied(unit, ledger):
    """The *same* reproduction command — the one `unit.reproduction` names —
    has to be recorded exiting zero under the `fix` phase. Same command, not
    a different one: a fix that only proves some other check passes has not
    shown this bug is gone, it has shown something else is fine."""
    target = _norm_command(getattr(unit, "reproduction", ""))
    if not target:
        return False, "the unit has no `reproduction` command recorded to re-run"
    entries = ledger.for_phase("fix")
    if not entries:
        return False, "fix has not been recorded yet"
    for entry in entries:
        if entry.exit_code == 0 and _norm_command(entry.command) == target:
            return True, ""
    return False, (
        "fix has been recorded, but not with the same reproduction command "
        "exiting zero — recording a different command's success does not "
        "prove this bug is fixed"
    )


# --------------------------------------------------------------------------- #
# the ledger: one file per unit, entries in the body
# --------------------------------------------------------------------------- #

class Entry:
    """One recorded attempt at one phase. Multiple entries per phase are
    normal — `reproduce` is expected to be run more than once, and only one
    of those runs needs to have failed for the phase to be satisfied."""

    def __init__(self, phase, *, command="", exit_code=None, evidence="", note="",
                 when=None):
        self.phase = _norm(phase)
        self.command = str(command or "").strip()
        self.exit_code = _int_or_none(exit_code)
        self.evidence = str(evidence or "").strip()
        self.note = str(note or "").strip()
        self.when = when or datetime.datetime.now().isoformat(timespec="seconds")

    def __repr__(self):
        return f"<Entry {self.phase} exit={self.exit_code}>"


class Ledger:
    """The recorded outcomes for one unit's phases, backed by one file."""

    def __init__(self, path, slug, unit_name, entries=None, layout=None):
        self.path = path
        self.slug = slug
        self.unit_name = unit_name
        self.entries = list(entries or [])
        # Only `load` has one, and only `_exclusive` uses it. A Ledger built by
        # hand still works; it just cannot serialise against anyone.
        self.layout = layout

    def __repr__(self):
        return f"<Ledger {self.slug}/{self.unit_name} {len(self.entries)} entries>"

    def for_phase(self, phase):
        wanted = _norm(phase)
        return [e for e in self.entries if e.phase == wanted]

    @contextlib.contextmanager
    def _exclusive(self):
        """Hold `plan-<slug>` across the read *and* the write, and re-read.

        The re-read is the point: waiting for the lock means someone else just
        wrote this file, so whatever was loaded before the wait is stale, and
        rendering the whole document back from it is how their entry
        disappeared. Yields whether the lock was taken — `lock.held` fails
        open, and a best-effort append beats a refused one.
        """
        with _plan_lock(self.layout, self.slug) as taken:
            self._reread()
            yield taken

    def _reread(self):
        """Adopt what is on disk now. A missing file leaves this ledger as it
        is: an empty ledger is what `load` would have produced anyway."""
        doc = frontmatter.read(self.path)
        if doc is None:
            return
        self.entries = _parse_body(doc.body)

    def add(self, phase, *, command="", exit_code=None, evidence="", note=""):
        with self._exclusive():
            return self._add(phase, command=command, exit_code=exit_code,
                             evidence=evidence, note=note)

    def _add(self, phase, *, command="", exit_code=None, evidence="", note=""):
        """`add` with the plan lock already held — the half `record` calls once
        `can_enter` has approved the phase, inside the same acquisition."""
        entry = Entry(phase, command=command, exit_code=exit_code, evidence=evidence,
                       note=note)
        self.entries.append(entry)
        self.save()
        return entry

    def save(self):
        """Render and write. Takes no lock: it is the write half of
        `_exclusive`, which holds one already, and a second acquisition would
        deadlock on a lock that is not re-entrant."""
        meta = {
            "ctx_schema": config_mod.SCHEMA,
            "unit": self.unit_name,
            "plan": self.slug,
            "updated": datetime.date.today().isoformat(),
        }
        frontmatter.Document(meta, self._render_body()).write(self.path)
        return self.path

    def _render_body(self):
        lines = [f"# Phase record — {self.unit_name}", "", "## Entries", ""]
        if not self.entries:
            lines.append("_None recorded yet._")
            return "\n".join(lines) + "\n"
        for index, entry in enumerate(self.entries, start=1):
            heading = f"### [{index}] {entry.phase}"
            if entry.command:
                heading += f" — {entry.command}"
            lines.append(heading)
            lines.append(f"- exit_code: {entry.exit_code if entry.exit_code is not None else ''}")
            lines.append(f"- command: {entry.command}")
            lines.append(f"- when: {entry.when}")
            for label, text in (("evidence", entry.evidence), ("note", entry.note)):
                if not text:
                    continue
                fence = _fence_for(text)
                lines.extend(["", f"{label}:", fence, text, fence])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def path_for(layout, slug, unit_name):
    """Beside the plan's units, not inside one — a unit file is hand-authored
    and this file is written by the phase gate, the same split `findings`
    makes for the same reason."""
    return layout.plans / slug / "phases" / f"{unit_name}.md"


def load(layout, slug, unit_name):
    """The unit's phase ledger. A missing file is an empty ledger, never an
    error — every phase gate asks before anything has been recorded."""
    path = path_for(layout, slug, unit_name)
    doc = frontmatter.read(path)
    if doc is None:
        return Ledger(path, slug, unit_name, layout=layout)
    return Ledger(path, slug, str(doc.meta.get("unit") or unit_name),
                  entries=_parse_body(doc.body), layout=layout)


@contextlib.contextmanager
def _plan_lock(layout, slug):
    """`lock.held(layout, f"plan-{slug}")`, or a no-op without a layout.

    The same name `findings` uses, on purpose: a phase record and a finding on
    two different units of one plan are the two writers most likely to collide,
    and they only serialise against each other if they ask for the same lock.
    """
    if layout is None or getattr(layout, "runtime", None) is None:
        # No layout, or a stand-in that only knows where plans live (the
        # byte-identical tests hand `path_for` exactly that). There is no
        # `runtime/locks/` to take a lock in, and inventing one under an
        # unknown object is worse than the lost update it would prevent.
        yield False
        return
    with lock.held(layout, f"plan-{slug}") as taken:
        yield taken


# --------------------------------------------------------------------------- #
# body parsing
# --------------------------------------------------------------------------- #

def _parse_body(body):
    """Entries out of the markdown. Tolerant on read, like `findings`: a
    hand-edited file with one mangled entry still yields the rest."""
    lines = (body or "").splitlines()
    out, current, index = [], None, 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line.rstrip())
        if heading:
            current = Entry(heading.group(2), command=heading.group(3) or "")
            out.append(current)
            index += 1
            continue
        if current is None:
            index += 1
            continue

        field = _FIELD.match(line.strip())
        if field:
            key, value = field.group(1), field.group(2).strip()
            if key == "exit_code":
                current.exit_code = _int_or_none(value)
            elif key == "when":
                current.when = value
            elif key == "command" and value:
                current.command = value
            index += 1
            continue

        block = _BLOCK.match(line.strip())
        if block:
            text, index = _read_block(lines, index + 1)
            if block.group(1) == "evidence":
                current.evidence = text
            else:
                current.note = text
            continue
        index += 1
    return out


def _read_block(lines, index):
    """The fenced text after a `label:` line. (text, next_index) — mirrors
    `findings._read_block`: the opening fence's own length is what closes it,
    so evidence containing a ``` line does not truncate the entry it belongs
    to."""
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return "", index
    opening = _FENCE.match(lines[index].strip())
    if not opening:
        return "", index
    fence = opening.group(1)
    index += 1
    collected = []
    while index < len(lines):
        if lines[index].strip() == fence:
            index += 1
            break
        collected.append(lines[index])
        index += 1
    return "\n".join(collected).strip(), index


def _fence_for(text):
    longest = max([len(run) for run in _BACKTICKS.findall(text)], default=0)
    return "`" * max(3, longest + 1)


# --------------------------------------------------------------------------- #
# coercion
# --------------------------------------------------------------------------- #

def _kind(unit):
    return str(getattr(unit, "kind", "") or "").strip().lower()


def _norm(name):
    return " ".join(str(name or "").split()).strip().lower()


def _norm_command(command):
    """Whitespace-insensitive comparison for "the same command": a reproduction
    step re-typed with different spacing is still the same command, and
    penalising that would make the gate fussier than the discipline it enforces."""
    return " ".join(str(command or "").split())


def _int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
