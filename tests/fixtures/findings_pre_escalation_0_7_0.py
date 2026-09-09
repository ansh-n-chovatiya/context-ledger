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
"""

import datetime
import re

from . import config as config_mod, frontmatter

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
_BLOCK = re.compile(r"^(evidence|ruling)\s*:\s*$")
_FENCE = re.compile(r"^(`{3,})\s*$")
_BACKTICKS = re.compile(r"`+")

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
        return Ledger(path, slug, unit_name)
    ledger = Ledger(
        path, slug,
        str(doc.meta.get("unit") or unit_name),
        findings=_parse_body(doc.body),
        round_number=_int(doc.meta.get("round"), 1),
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


class Ledger:
    def __init__(self, path, slug, unit, findings=None, round_number=1):
        self.path = path
        self.slug = slug
        self.unit = unit
        self.findings = list(findings or [])
        self.round = int(round_number)

    def __repr__(self):
        return f"<Ledger {self.slug}/{self.unit} {len(self.findings)} findings>"

    # ----------------------------------------------------------------------- #
    # mutation
    #
    # Every mutator writes, the way `plan.Unit.set` does. A ledger that has to be
    # saved by hand is one an interrupted turn loses, and losing it is exactly
    # the failure this module exists to prevent. `save()` stays public for a
    # caller that edited a Finding's fields directly.
    # ----------------------------------------------------------------------- #

    def add(self, severity, summary, *, where="", evidence="", source="reviewer"):
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

    def bump_round(self):
        """Start the next fix round. The caller compares against MAX_ROUNDS —
        this module counts, it does not decide when to give up."""
        self.round += 1
        self.save()
        return self.round

    def save(self):
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
            return "\n".join(lines) + "\n"
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
        return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# body parsing
# --------------------------------------------------------------------------- #

def _parse_body(body):
    """Findings out of the markdown. Tolerant on read, like every other reader
    here: a hand-edited file with one mangled finding still yields the rest."""
    lines = (body or "").splitlines()
    out, current, index = [], None, 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line.rstrip())
        if heading:
            current = Finding(
                heading.group(1), heading.group(2), heading.group(4) or "",
                status=heading.group(3),
            )
            out.append(current)
            index += 1
            continue
        if current is None:
            index += 1
            continue

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
            if block.group(1) == "evidence":
                current.evidence = text
            else:
                current.ruling = text
            continue
        index += 1
    return out


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
