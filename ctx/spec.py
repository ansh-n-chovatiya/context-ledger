"""Specs and the ambiguity gate.

The gate is a file, not a prompt instruction. `questions.md` holds blocking
questions as unchecked boxes, and a spec is not `ready` while any remain — so
the model cannot assume its way past ambiguity, because there is a check it has
to clear rather than a rule it has to remember.

Answers are appended with the question they answer, which makes the file an
audit trail: you can show what was asked before work began.
"""

import datetime
import hashlib
import re

from . import config as config_mod, frontmatter

BLOCKING = "Blocking"
NONBLOCKING = "Non-blocking"
RESOLVED = "Resolved"

_OPEN = re.compile(r"^\s*[-*]\s*\[ \]\s*(.+?)\s*$")
_DONE = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*(.+?)\s*$")
_ADR = re.compile(r"^(\d{4})-")


# The longest a spec slug may be, in bytes of UTF-8.
#
# The hard constraint is 255 bytes per path *component*, which is what ext4,
# APFS and NTFS each allow for one directory name; a 400-character intent blew
# straight through it and `ctx spec` died with `OSError: [Errno 63] File name
# too long`. 80 is well under that, and the headroom is deliberate rather than
# timid: the slug is a directory with files beneath it, so the binding number
# is not the slug alone but `.ctx/specs/<slug>/questions.md`. At 80 that whole
# relative path is 104 bytes, which leaves ~155 characters for the repository
# root before a legacy Windows `MAX_PATH` of 260 is in play - enough for a
# checkout nested a few directories deep under a long user profile name. A cap
# nearer 255 would satisfy the filesystem and still fail on Windows.
#
# 80 is also about as much of an intent as anyone reads off a directory
# listing; past that the name has stopped identifying the spec and started
# quoting it.
SLUG_MAX_BYTES = 80

# Hex characters of the disambiguating digest. 8 hex characters is 32 bits:
# collisions need ~77,000 specs sharing one truncated prefix before they are
# even at even odds, and every one of those would have to be created in the
# same ledger.
SLUG_HASH_CHARS = 8

# What a slug becomes when nothing usable survives - an intent written entirely
# in characters no normalisation keeps. A directory has to be called something,
# and `spec` is better than a stack trace or, worse, an empty component that
# silently writes `spec.md` into `.ctx/specs/` itself.
SLUG_FALLBACK = "spec"

# Characters no path component may contain on all three platforms at once:
# the ASCII control range, the Windows-reserved set, and both separators.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def normalise_slug(slug):
    """A slug reduced to one directory name that is safe and short everywhere.

    Every path in this module goes through `spec_dir`, and `spec_dir` goes
    through here, so the cap applies to whoever derived the slug rather than
    to whoever called the CLI: `ctx spec`, `ctx question`, `ctx resolve` and
    the plan-time `spec.create` all land on the same directory for the same
    intent, and a caller that stored the uncapped slug (in `state.json`, say)
    still resolves to it.

    Two properties this has to have, and one it must not:

    * Deterministic. The disambiguator is a SHA-256 of the slug itself, so the
      same intent gives the same directory on every run, machine and platform.
      Nothing here reads the clock, the filesystem or a counter - if it did,
      re-running `ctx spec` with the same words would scaffold a second spec
      beside the first instead of finding it.
    * Idempotent. `normalise_slug(normalise_slug(x)) == normalise_slug(x)`,
      because the result is always within the cap and already free of unsafe
      characters, so a second pass returns it untouched. `spec_dir` is called
      on slugs that have already been through here, and a cap that re-truncated
      its own output would walk a slug away from its directory one call at a
      time.

    It must *not* be a plain truncation. Two 400-character intents that agree
    for their first 80 characters - the same feature described twice, which is
    exactly when someone writes a long intent - would truncate to the same
    directory and the second would silently open the first one's spec. The
    digest is what keeps them apart, and it is taken from the whole slug, so it
    differs wherever the slugs do.
    """
    # `-` is stripped from the ends alongside dots and spaces: an input of
    # `///` substitutes to a bare `-`, which is a legal but useless directory
    # name, and falling through to `SLUG_FALLBACK` is the better answer.
    text = _UNSAFE.sub("-", str(slug or "")).strip().strip("-. ")
    if not text:
        return SLUG_FALLBACK
    raw = text.encode("utf-8")
    if len(raw) <= SLUG_MAX_BYTES:
        return text
    digest = hashlib.sha256(raw).hexdigest()[:SLUG_HASH_CHARS]
    # `errors="ignore"` drops a UTF-8 sequence the cut landed in the middle of,
    # so a non-ASCII intent is shortened to a character boundary rather than to
    # an undecodable byte string.
    head = raw[: SLUG_MAX_BYTES - SLUG_HASH_CHARS - 1].decode("utf-8", "ignore")
    head = head.strip().strip("-. ")
    return f"{head}-{digest}" if head else digest


def spec_dir(layout, slug):
    return layout.specs / normalise_slug(slug)


def spec_path(layout, slug):
    return spec_dir(layout, slug) / "spec.md"


def questions_path(layout, slug):
    return spec_dir(layout, slug) / "questions.md"


def create(layout, slug, intent="", verify=None):
    """Scaffold a spec and its questions file. Both are safe to re-run."""
    # Capped here as well as in `spec_dir`, so the `spec:` recorded in both
    # files' frontmatter names the directory they actually live in.
    slug = normalise_slug(slug)
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
    return [text for kind, text in _items(text) if kind == "open"]


def _all_items(text):
    return [text for _kind, text in _items(text)]


_BULLET = re.compile(r"^[-*]\s*(?:\[([ xX])\]\s*)?(.*)$")


def _items(text):
    """Bullet lines from a questions section, wrapped continuations rejoined.

    A question (or resolved answer) can wrap across physical lines just like
    any other markdown list item, and `frontmatter.Document.list_items` fixed
    the same defect for task/unit/spec criteria — this mirrors that fix for
    the independent bullet parser here, since `spec.questions()` never goes
    through `Document.list_items` (checkbox state has to survive, which that
    method's plain marker-stripping does not preserve).

    The continuation rule is identical: once a `-`/`*` bullet opens an item
    (optionally carrying `[ ]`/`[x]`/`[X]`), any following line that is
    non-blank, is not itself a bullet, and is indented further than that
    bullet was, is folded onto the item with a single space; a blank line, a
    new bullet, or the end of the section closes it. A line at or before the
    bullet's own column that qualifies as neither a bullet nor a
    continuation — e.g. the HTML-comment scaffolding `create()` seeds each
    section with — is dropped, exactly as before. Whitespace in the result
    is collapsed to single spaces. As in `list_items`, an indented nested
    bullet still opens its own separate item rather than nesting; the flat
    list this returns has never represented nesting, and `_open_items`/
    `_all_items` only care about a single field — the checkbox state — of
    each entry, not its position in a tree.

    Returns `[(kind, text), ...]` where `kind` is `"open"` (`[ ]`), `"done"`
    (`[x]`/`[X]`), or `"plain"` (no checkbox at all — e.g. a resolved item).
    """
    out = []
    buffer = None
    kind = None
    marker_indent = 0
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        match = _BULLET.match(stripped) if stripped[:1] in ("-", "*") else None
        if match:
            if buffer is not None:
                out.append((kind, _collapse(buffer)))
            box = match.group(1)
            kind = "open" if box == " " else "done" if box else "plain"
            buffer = [match.group(2).strip()]
            marker_indent = indent
        elif not stripped:
            if buffer is not None:
                out.append((kind, _collapse(buffer)))
            buffer = None
        elif buffer is not None and indent > marker_indent:
            buffer.append(stripped)
    if buffer is not None:
        out.append((kind, _collapse(buffer)))
    return out


def _collapse(pieces):
    """Join wrapped-line fragments into one string, whitespace normalised."""
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()


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
    # `ctx decide` slugifies a free-text title, which has the same failure mode
    # a long intent had: `0001-<400 characters>.md` is not a filename any of
    # the three platforms will accept.
    path = layout.decisions / f"{number:04d}-{normalise_slug(slug)}.md"
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
