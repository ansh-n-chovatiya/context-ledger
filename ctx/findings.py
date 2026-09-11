"""Review findings: the durable half of the adversarial review loop.

A reviewer subagent and the implementer it reviews do not share a context, and
neither survives compaction. A finding that lives only in a transcript is gone
by the third fix round — the implementer is arguing with a summary of a summary,
and the done-gate has nothing to refuse on. So findings live in a file, one per
unit, and every state change is a write.

Three decisions carry the design.

**There is no `acknowledged` status.** The failure mode of a review loop is
performative agreement — "good catch, noted" — which reads as progress, closes
nothing, and leaves the defect in the tree. A finding leaves `open` by being
fixed (`addressed`), refuted (`disputed`, with evidence), or decided against
(`parked`, with a ruling). Agreement on its own is not a state this module can
represent, and that unavailability is the mechanism.

**A refusal is a return value, not an exception.** `set_status` is called from
inside a subagent turn; raising there costs the turn and the fix with it. It
returns `(False, why)` so the caller can be told what the state machine wanted
and try again in the same breath.

**Findings are written into the markdown body, not the frontmatter.** That was
forced: `miniyaml.dumps` writes a multi-line string raw, and `miniyaml.loads`
then dies on its second line — so a finding whose `evidence` holds command
output or a diff excerpt, which is every finding worth raising, would have
produced a frontmatter file that no longer parses. Evidence and rulings go in
fenced blocks in the body instead, and the fence grows past any run of backticks
in the text for the same reason: the store must not be corruptible by the
content it is asked to hold. The body is also what someone opening the file in
an editor reads, which the frontmatter list never was.

A fourth thing follows the first rule rather than bending it: **an escalated
round is not a new status.** When a fix round fails with escalation turned on,
`Ledger.bump_round` may move the round to a dearer model tier — but that move
is recorded as a fact about the round (which round, which tier to which tier,
and why), never as somewhere a `Finding` can sit instead of
`open`/`addressed`/`disputed`/`parked`. `STATUSES` does not grow for it, no
`Finding` field changes because of it, and — same as evidence and rulings —
the record lives in the body, fenced, for the same miniyaml reason. With
`models.escalate_on_failed_round` at its default `false`, nothing calls the
escalation path at all, so a ledger written by a project that never turns the
flag on is byte-identical to one written before this feature existed.

**Every mutation is a locked read-modify-write, not a write.** The reviewer and
the implementer are separate processes working the same plan, and they both
change this file: one raises a finding, the other resolves a different one. Each
used to load the ledger, mutate the object it had, and render the *whole* file
back — so whichever saved second erased the other's finding entirely. So the
mutators do not write what they loaded. Each one takes `plan-<slug>` (the plan,
not the unit: a reviewer and an implementer on different units still share the
round counter and the plan's files), re-reads the file inside that lock, applies
its change to what it finds there, and writes — all in one acquisition, because
a lock taken twice around the two halves serialises nothing. `_exclusive` is the
only place in this module that takes a lock; `save()` deliberately does not, so
the two can never nest (`lock.held` is not re-entrant).
"""

import contextlib
import datetime
import re

from . import config as config_mod, frontmatter, lock

SEVERITIES = ("critical", "important", "minor")
STATUSES = ("open", "addressed", "disputed", "parked")

# `minor` is deliberately absent: minor findings are recorded and deferred, never
# entered into a fix loop. A review that can block on nits is a review the
# implementer learns to route around.
BLOCKING = ("critical", "important")

# Fix rounds before the loop stops and the user is asked. Three is the same
# ceiling the done-gate uses for attempts; a fourth round of the same argument is
# a decision to escalate, not to retry.
MAX_ROUNDS = 3

_HEADING = re.compile(r"^###\s+\[(\d+)\]\s+([A-Za-z]+)\s*/\s*([A-Za-z]+)\s*(?:—\s*(.*))?$")
_FIELD = re.compile(r"^-\s+(where|round|source)\s*:\s*(.*)$")
_BLOCK = re.compile(r"^(evidence|ruling|reason)\s*:\s*$")
_FENCE = re.compile(r"^(`{3,})\s*$")
_BACKTICKS = re.compile(r"`+")

# Deliberately distinct from `_HEADING`: an escalation heading has no `[id]`
# directly after `###`, so a hand-edited file can never make a Finding parse
# as an Escalation or the reverse.
_ESC_HEADING = re.compile(r"^###\s+escalation\s+\[(\d+)\]\s+(\S+)\s*->\s*(\S+)\s*$")

PREAMBLE = """A reviewer raised these; the unit's implementer resolves them. A finding leaves
`open` only by being fixed (`addressed`), refuted with evidence (`disputed`), or
decided against with a ruling (`parked`) — there is no way to merely agree with
one. `critical` and `important` block the done-gate while they are open;
`minor` never does."""


def path_for(layout, slug, unit_name):
    """Beside the plan's units, not inside them: units are the authored artifact
    and are hand-edited, while this file is written by two agents in turn."""
    return layout.plans / slug / "findings" / f"{unit_name}.md"


def load(layout, slug, unit_name):
    """The unit's ledger. A missing file is an empty ledger, never an error —
    the reviewer and the gate both ask before anything has been raised."""
    path = path_for(layout, slug, unit_name)
    doc = frontmatter.read(path)
    if doc is None:
        return Ledger(path, slug, unit_name, layout=layout)
    findings, escalations = _parse_body(doc.body)
    ledger = Ledger(
        path, slug,
        str(doc.meta.get("unit") or unit_name),
        findings=findings,
        round_number=_int(doc.meta.get("round"), 1),
        escalations=escalations,
        layout=layout,
    )
    return ledger


class Finding:
    def __init__(self, id, severity, summary, *, status="open", evidence="",
                 where="", round_number=1, ruling="", source="reviewer"):
        self.id = int(id)
        self.severity = _severity(severity)
        self.status = _status(status)
        self.summary = _oneline(summary)
        self.evidence = str(evidence or "").strip()
        self.where = _oneline(where)
        self.round = int(round_number)
        self.ruling = str(ruling or "").strip()
        self.source = _oneline(source) or "reviewer"

    def __repr__(self):
        return f"<Finding {self.id} {self.severity}/{self.status}>"

    def blocking(self):
        return self.severity in BLOCKING and self.status == "open"

    def as_dict(self):
        """Comparable form, for round-trip assertions and for the briefing."""
        return {
            "id": self.id, "severity": self.severity, "status": self.status,
            "summary": self.summary, "evidence": self.evidence,
            "where": self.where, "round": self.round, "ruling": self.ruling,
            "source": self.source,
        }

    def line(self):
        """One line for a gate refusal message."""
        where = f" ({self.where})" if self.where else ""
        return f"[{self.id}] {self.severity}: {self.summary}{where}"


class Escalation:
    """A fact about a round, not a status: the round moved from one model
    tier to a dearer one, and why. Never attached to a `Finding` — see the
    module docstring's fourth decision."""

    def __init__(self, round_number, from_model, to_model, reason=""):
        self.round = int(round_number)
        self.from_model = _oneline(from_model)
        self.to_model = _oneline(to_model)
        self.reason = str(reason or "").strip()

    def __repr__(self):
        return f"<Escalation round {self.round} {self.from_model}->{self.to_model}>"

    def as_dict(self):
        return {
            "round": self.round, "from": self.from_model, "to": self.to_model,
            "reason": self.reason,
        }

    def line(self):
        """One line, for a journal entry or a briefing."""
        head = f"round {self.round}: {self.from_model} -> {self.to_model}"
        return f"{head} ({self.reason})" if self.reason else head


class Ledger:
    def __init__(self, path, slug, unit, findings=None, round_number=1,
                 escalations=None, layout=None):
        self.path = path
        self.slug = slug
        self.unit = unit
        self.findings = list(findings or [])
        self.round = int(round_number)
        self.escalations = list(escalations or [])
        # Only `load` has one, and only `_exclusive` uses it. A Ledger built by
        # hand still works; it just cannot serialise against anyone.
        self.layout = layout

    def __repr__(self):
        return f"<Ledger {self.slug}/{self.unit} {len(self.findings)} findings>"

    # ----------------------------------------------------------------------- #
    # mutation
    #
    # Every mutator writes, the way `plan.Unit.set` does. A ledger that has to be
    # saved by hand is one an interrupted turn loses, and losing it is exactly
    # the failure this module exists to prevent. `save()` stays public for a
    # caller that edited a Finding's fields directly.
    #
    # Each public mutator is `with self._exclusive(): <the unlocked half>`. The
    # split is deliberate and load-bearing: the lock is taken exactly once, in
    # `_exclusive`, which is also what re-reads the file — so the read and the
    # write it is based on cannot drift apart, and no mutator can nest inside
    # another and deadlock on a lock that is not re-entrant.
    # ----------------------------------------------------------------------- #

    @contextlib.contextmanager
    def _exclusive(self):
        """Hold `plan-<slug>` across the read *and* the write, and re-read.

        The re-read is the point. Waiting for the lock means someone else just
        wrote this file, so the state loaded before the wait is stale by
        definition; applying a mutation to it and rendering the whole document
        back is exactly how the other writer's finding disappeared. Yields
        whether the lock was actually taken — `lock.held` fails open, and a
        best-effort mutation is still better than a refused one.
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
        findings, escalations = _parse_body(doc.body)
        self.findings = findings
        self.escalations = escalations
        self.round = _int(doc.meta.get("round"), self.round)

    def add(self, severity, summary, *, where="", evidence="", source="reviewer"):
        with self._exclusive():
            return self._add(severity, summary, where=where, evidence=evidence,
                             source=source)

    def _add(self, severity, summary, *, where="", evidence="", source="reviewer"):
        """`add` with the plan lock already held. `_next_id` therefore counts
        the findings the *file* has, not the ones this object was loaded with,
        so two reviewers cannot both mint id 3."""
        finding = Finding(
            self._next_id(), severity, summary,
            where=where, evidence=evidence, round_number=self.round, source=source,
        )
        self.findings.append(finding)
        self.save()
        return finding

    def get(self, finding_id):
        try:
            wanted = int(finding_id)
        except (TypeError, ValueError):
            return None
        for finding in self.findings:
            if finding.id == wanted:
                return finding
        return None

    def set_status(self, finding_id, status, *, ruling="", evidence=""):
        """Move a finding, or say why it may not move. Returns (ok, problem)."""
        with self._exclusive():
            return self._set_status(finding_id, status, ruling=ruling,
                                    evidence=evidence)

    def _set_status(self, finding_id, status, *, ruling="", evidence=""):
        """`set_status` with the plan lock already held. The lookup is inside
        the lock too: a finding raised by a reviewer a millisecond ago is one
        this can resolve, and a refusal ("no finding 3 in this ledger") must
        never be an artefact of having read the file too early."""
        finding = self.get(finding_id)
        if finding is None:
            known = ", ".join(str(f.id) for f in self.findings) or "none"
            return False, f"no finding {finding_id!r} in this ledger (have: {known})"

        wanted = str(status or "").strip().lower()
        if wanted not in STATUSES:
            extra = ""
            if wanted in ("acknowledged", "noted", "ack", "agreed"):
                # Named explicitly because it is the status a model reaches for
                # first, and the whole loop fails quietly when it gets it.
                extra = (
                    " — there is deliberately no acknowledged status: agreeing with a "
                    "finding does not close it. Fix it, refute it with evidence, or "
                    "park it with a ruling."
                )
            return False, f"status {status!r} is not one of {'/'.join(STATUSES)}{extra}"

        note = str(evidence or "").strip()
        decision = str(ruling or "").strip()

        if wanted == "disputed" and not note:
            return False, (
                f"disputing finding {finding.id} needs evidence — a dispute is a "
                "technical claim, so pass the file:line or the command output that "
                "shows the finding is wrong. Disagreement on its own is not a state "
                "this ledger records."
            )
        if wanted == "parked" and not decision:
            return False, (
                f"parking finding {finding.id} needs a ruling — parking spends the "
                "user's judgement on their behalf, so say what was decided and why, "
                "and it stays in the file for them to overturn."
            )

        finding.status = wanted
        if decision:
            finding.ruling = decision
        if note:
            # Appended, never overwritten: the reviewer's evidence is what the
            # finding was raised on, and a dispute that erased it would leave the
            # file arguing against a claim it no longer contains.
            stamp = f"[round {self.round}, {wanted}]"
            finding.evidence = f"{finding.evidence}\n\n{stamp} {note}".strip()
        self.save()
        return True, ""

    def bump_round(self, config=None, model=None, reason=None):
        """Start the next fix round. The caller compares against MAX_ROUNDS —
        this module counts, it does not decide when to give up, and nothing
        here moves that ceiling: escalation changes the seat a round runs on,
        never how many rounds there are.

        `config` and `model` are optional, and every 0.7.0 caller — and every
        0.8 caller with `models.escalate_on_failed_round` at its default
        `false` — omits them or hits the flag check below and gets exactly
        the old behaviour: round bumped, ledger saved, nothing else written.
        That is what "byte-identical when escalation is disabled" means in
        practice: the disabled path never reaches the code that would make
        the file different.

        With both given and the flag on, a failed round also escalates:
        `config.tier_up` names the next dearer tier for `model`, and — only
        if it actually moved — the round, the tier change and `reason` (or a
        generated one) are recorded in the ledger. `tier_up` already answers
        "no dearer tier" and "not a tracked model" by returning `model`
        unchanged rather than raising, so this calls it after every failed
        round without first checking whether escalation is still possible,
        exactly as `config.tier_up`'s own docstring describes.
        """
        with self._exclusive():
            return self._bump_round(config, model, reason)

    def _bump_round(self, config=None, model=None, reason=None):
        """`bump_round` with the plan lock already held. The counter is read
        from the file and incremented in one acquisition — two processes
        deciding the next round is 2 is the lost update in its purest form."""
        self.round += 1
        self._maybe_escalate(config, model, reason)
        self.save()
        return self.round

    def _maybe_escalate(self, config, model, reason):
        """Record a tier escalation for the round just started, or do
        nothing. Returns the `Escalation` recorded, or `None`."""
        if config is None or model is None:
            return None
        models_cfg = config.get("models") or {}
        if not models_cfg.get("escalate_on_failed_round", False):
            return None
        new_model = config_mod.tier_up(config, model)
        if new_model == model:
            # Already the dearest tier, or `model` was never in `models.tiers`
            # at all — an explicit unit `model:`, which `tier_up` leaves alone
            # on purpose. Either way there is no move to record.
            return None
        text = str(reason or "").strip() or (
            f"round {self.round - 1} still had blocking findings open on "
            f"{model}; round {self.round} escalates to {new_model}"
        )
        record = Escalation(self.round, model, new_model, text)
        self.escalations.append(record)
        return record

    def save(self):
        """Render and write. Takes no lock, on purpose: it is the write half of
        `_exclusive`, which already holds one, and a second acquisition here
        would deadlock every mutator on a lock that is not re-entrant. Called
        directly — the documented escape hatch for a caller that edited a
        Finding's fields — it is an unserialised write, exactly as it was
        before."""
        meta = {
            "ctx_schema": config_mod.SCHEMA,
            "unit": self.unit,
            "plan": self.slug,
            "round": self.round,
            "updated": datetime.date.today().isoformat(),
        }
        frontmatter.Document(meta, self._render_body()).write(self.path)
        return self.path

    # ----------------------------------------------------------------------- #
    # reading
    # ----------------------------------------------------------------------- #

    def blocking(self):
        return [f for f in self.findings if f.blocking()]

    def open_findings(self):
        return [f for f in self.findings if f.status == "open"]

    def counts(self):
        out = {status: 0 for status in STATUSES}
        for finding in self.findings:
            if finding.status in out:
                out[finding.status] += 1
        out["blocking"] = len(self.blocking())
        return out

    def summary(self):
        """One line, for a briefing or a gate message."""
        head = f"round {self.round}/{MAX_ROUNDS}"
        if not self.findings:
            return f"{head} — no findings"
        counts = self.counts()
        by_severity = []
        for severity in SEVERITIES:
            total = len([f for f in self.open_findings() if f.severity == severity])
            if total:
                by_severity.append(f"{total} {severity}")
        bits = [", ".join(by_severity) + " open"] if by_severity else ["nothing open"]
        for status in ("addressed", "disputed", "parked"):
            if counts[status]:
                bits.append(f"{counts[status]} {status}")
        return f"{head} — " + ", ".join(bits)

    # ----------------------------------------------------------------------- #
    # persistence detail
    # ----------------------------------------------------------------------- #

    def _next_id(self):
        return max([f.id for f in self.findings], default=0) + 1

    def _render_body(self):
        lines = [f"# Review findings — {self.unit}", "", PREAMBLE, "", "## Findings", ""]
        if not self.findings:
            lines.append("_None raised yet._")
        else:
            for finding in self.findings:
                heading = f"### [{finding.id}] {finding.severity} / {finding.status}"
                if finding.summary:
                    heading += f" — {finding.summary}"
                lines.append(heading)
                if finding.where:
                    lines.append(f"- where: {finding.where}")
                lines.append(f"- round: {finding.round}")
                lines.append(f"- source: {finding.source}")
                for label, text in (("evidence", finding.evidence), ("ruling", finding.ruling)):
                    if not text:
                        continue
                    fence = _fence_for(text)
                    lines.extend(["", f"{label}:", fence, text, fence])
                lines.append("")
        # Escalations only exist when the caller opted into
        # `models.escalate_on_failed_round`, so this section — and the blank
        # line it needs — is entirely absent otherwise. That is the whole
        # byte-identical-when-disabled guarantee: this branch never runs.
        if self.escalations:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("## Escalations")
            lines.append("")
            for esc in self.escalations:
                lines.append(f"### escalation [{esc.round}] {esc.from_model} -> {esc.to_model}")
                if esc.reason:
                    fence = _fence_for(esc.reason)
                    lines.extend(["", "reason:", fence, esc.reason, fence])
                lines.append("")
        if not self.findings and not self.escalations:
            return "\n".join(lines) + "\n"
        return "\n".join(lines).rstrip() + "\n"


@contextlib.contextmanager
def _plan_lock(layout, slug):
    """`lock.held(layout, f"plan-{slug}")`, or a no-op without a layout.

    The name is the *plan*, not the unit. A reviewer on `02-api` and an
    implementer on `03-cli` never touch the same findings file, but they do
    share `plan.json`, the round counter and the seal — so the thing worth
    serialising is the plan, and a per-unit lock would have let precisely the
    reported race through.
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
    """Findings and escalation records out of the markdown. Tolerant on read,
    like every other reader here: a hand-edited file with one mangled entry
    still yields the rest. Returns `(findings, escalations)`."""
    lines = (body or "").splitlines()
    findings, escalations = [], []
    current, kind, index = None, None, 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line.rstrip())
        if heading:
            current = Finding(
                heading.group(1), heading.group(2), heading.group(4) or "",
                status=heading.group(3),
            )
            kind = "finding"
            findings.append(current)
            index += 1
            continue
        esc_heading = _ESC_HEADING.match(line.rstrip())
        if esc_heading:
            current = Escalation(esc_heading.group(1), esc_heading.group(2), esc_heading.group(3))
            kind = "escalation"
            escalations.append(current)
            index += 1
            continue
        if current is None:
            index += 1
            continue

        if kind == "finding":
            field = _FIELD.match(line.strip())
            if field:
                key, value = field.group(1), field.group(2).strip()
                if key == "round":
                    current.round = _int(value, current.round)
                elif key == "where":
                    current.where = value
                else:
                    current.source = value or "reviewer"
                index += 1
                continue

        block = _BLOCK.match(line.strip())
        if block:
            text, index = _read_block(lines, index + 1)
            label = block.group(1)
            if kind == "finding" and label in ("evidence", "ruling"):
                if label == "evidence":
                    current.evidence = text
                else:
                    current.ruling = text
            elif kind == "escalation" and label == "reason":
                current.reason = text
            continue
        index += 1
    return findings, escalations


def _read_block(lines, index):
    """The fenced text after a `label:` line. (text, next_index).

    The opening fence's own length is what closes it, so evidence containing a
    ``` line — a reviewer pasting a diff of a markdown file — does not truncate
    the finding it belongs to.
    """
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

def _severity(value):
    """Unknown severities fail towards blocking.

    A reviewer that writes `blocker` or `high` has still found something, and
    silently demoting it to a non-blocking severity would let a real defect
    through a gate that reported itself green.
    """
    severity = str(value or "").strip().lower()
    return severity if severity in SEVERITIES else "important"


def _status(value):
    status = str(value or "").strip().lower()
    return status if status in STATUSES else "open"


def _oneline(value):
    return " ".join(str(value or "").split())


def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
