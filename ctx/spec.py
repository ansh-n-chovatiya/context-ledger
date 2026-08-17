"""Specs and the ambiguity gate.

The gate is a file, not a prompt instruction. `questions.md` holds blocking
questions as unchecked boxes, and a spec is not `ready` while any remain — so
the model cannot assume its way past ambiguity, because there is a check it has
to clear rather than a rule it has to remember.

Answers are appended with the question they answer, which makes the file an
audit trail: you can show what was asked before work began.
"""

import datetime
import re

from . import config as config_mod, frontmatter

BLOCKING = "Blocking"
NONBLOCKING = "Non-blocking"
RESOLVED = "Resolved"

_OPEN = re.compile(r"^\s*[-*]\s*\[ \]\s*(.+?)\s*$")
_DONE = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*(.+?)\s*$")
_ADR = re.compile(r"^(\d{4})-")


def spec_dir(layout, slug):
    return layout.specs / slug


def spec_path(layout, slug):
    return spec_dir(layout, slug) / "spec.md"


def questions_path(layout, slug):
    return spec_dir(layout, slug) / "questions.md"


def create(layout, slug, intent="", verify=None):
    """Scaffold a spec and its questions file. Both are safe to re-run."""
    path = spec_path(layout, slug)
    if not path.exists():
        meta = {
            "ctx_schema": config_mod.SCHEMA,
            "spec": slug,
            "status": "draft",
            "created": datetime.date.today().isoformat(),
            "verify": list(verify or []),
        }
        body = (
            "## Intent\n"
            f"{intent or '<one paragraph: the observable outcome, and why it matters>'}\n\n"
            "## Acceptance criteria\n"
            "1. <checkable — name the observable, not the implementation>\n\n"
            "## Out of scope\n"
            "- <what this deliberately does not cover>\n\n"
            "## Notes\n"
        )
        frontmatter.Document(meta, body).write(path)

    qpath = questions_path(layout, slug)
    if not qpath.exists():
        meta = {"ctx_schema": config_mod.SCHEMA, "spec": slug}
        body = (
            f"## {BLOCKING}\n"
            "<!-- Anything whose answer changes what gets built. Unchecked boxes\n"
            "     here block planning: `- [ ] Q1: …` -->\n\n"
            f"## {NONBLOCKING}\n"
            "<!-- Worth knowing, but you can proceed without it. -->\n\n"
            f"## {RESOLVED}\n"
        )
        frontmatter.Document(meta, body).write(qpath)
    return path, qpath


def questions(layout, slug):
    """(open_blocking, open_nonblocking, resolved) as plain strings."""
    doc = frontmatter.read(questions_path(layout, slug))
    if doc is None:
        return [], [], []
    sections = doc.sections()
    return (
        _open_items(sections.get(BLOCKING.lower(), "")),
        _open_items(sections.get(NONBLOCKING.lower(), "")),
        _all_items(sections.get(RESOLVED.lower(), "")),
    )


def _open_items(text):
    return [m.group(1) for m in (_OPEN.match(l) for l in text.splitlines()) if m]


def _all_items(text):
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            out.append(re.sub(r"^[-*]\s*(\[[ xX]\]\s*)?", "", stripped))
    return out


def add_questions(layout, slug, items, blocking=True):
    """Append questions to the right section, preserving everything else."""
    qpath = questions_path(layout, slug)
    doc = frontmatter.read(qpath)
    if doc is None:
        _, qpath = create(layout, slug)
        doc = frontmatter.read(qpath)
    heading = BLOCKING if blocking else NONBLOCKING
    existing = set(
        _open_items(doc.sections().get(heading.lower(), ""))
        + _all_items(doc.sections().get(heading.lower(), ""))
    )
    fresh = [i for i in items if i.strip() and i.strip() not in existing]
    if not fresh:
        return 0
    doc.body = _append_to_section(
        doc.body, heading, "".join(f"- [ ] {item.strip()}\n" for item in fresh)
    )
    doc.write(qpath)
    return len(fresh)


def resolve(layout, slug, question, answer):
    """Tick the question off and record the answer with the date it was given."""
    qpath = questions_path(layout, slug)
    doc = frontmatter.read(qpath)
    if doc is None:
        return False
    needle = question.strip().lower()
    matched = []

    lines = doc.body.splitlines()
    for index, line in enumerate(lines):
        match = _OPEN.match(line)
        if match and needle in match.group(1).strip().lower():
            lines[index] = line.replace("[ ]", "[x]", 1)
            matched.append(match.group(1).strip())
    if not matched:
        return False

    doc.body = _append_to_section(
        "\n".join(lines), RESOLVED,
        "".join(
            f"- {item} → {answer.strip()} ({datetime.date.today().isoformat()})\n"
            for item in matched
        ),
    )
    doc.write(qpath)
    return True


def ready(layout, slug):
    """Gate 1. (is_ready, open_blocking_questions)."""
    blocking, _non, _resolved = questions(layout, slug)
    return (not blocking), blocking


def mark(layout, slug, status):
    path = spec_path(layout, slug)
    doc = frontmatter.read(path)
    if doc is None:
        return False
    doc.meta["status"] = status
    doc.write(path)
    return True


def _append_to_section(body, heading, addition):
    """Insert at the end of `## heading`, creating the section if absent."""
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.M | re.I)
    match = pattern.search(body)
    if not match:
        return body.rstrip() + f"\n\n## {heading}\n{addition}"
    following = re.compile(r"^##\s+", re.M)
    nxt = following.search(body, match.end())
    cut = nxt.start() if nxt else len(body)
    chunk = body[match.end():cut].rstrip("\n")
    return body[:match.end()] + "\n" + chunk + "\n" + addition + "\n" + body[cut:]


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #

def next_adr_number(layout):
    highest = 0
    if layout.decisions.is_dir():
        for path in layout.decisions.glob("*.md"):
            match = _ADR.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def write_decision(layout, title, slug, context="", decision="", consequences=""):
    number = next_adr_number(layout)
    path = layout.decisions / f"{number:04d}-{slug}.md"
    meta = {
        "ctx_schema": config_mod.SCHEMA,
        "adr": number,
        "title": title,
        "status": "accepted",
        "date": datetime.date.today().isoformat(),
    }
    body = (
        f"# {number:04d}. {title}\n\n"
        "## Context\n"
        f"{context or '<what forced a choice>'}\n\n"
        "## Decision\n"
        f"{decision or '<what we chose, in one sentence>'}\n\n"
        "## Consequences\n"
        f"{consequences or '<what this costs us, and what it rules out>'}\n\n"
        "## Status\n"
        "Accepted. Supersede with a new ADR rather than editing this one.\n"
    )
    frontmatter.Document(meta, body).write(path)
    return path
