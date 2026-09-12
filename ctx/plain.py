"""The plain-language file a person writes for a plan, and honest filler for the rest.

A plan on disk is unit contracts: `owns`, `depends_on`, `verify`, wave numbers.
That is the right language for the thing that dispatches work and the wrong
language for the person who has to approve it. `plain.md` sits next to
`plan.json` and the unit files and holds prose **authored by a human** — five
plan-level sections and one block per unit. This module is the only thing that
parses it.

    ---
    ctx_schema: 1
    plan: plan-preview
    digest: 4f2a1c…
    ---

    ## Summary
    We are fixing seven safety problems we found in our own checking system.

    ## Why now
    ## What changes for you
    ## What could go wrong
    ## Out of scope

    ## Unit: 01-verify-kinds — Make the safety check honest
    **What it does:** Makes the safety check report a real breakage as a real
    breakage, instead of quietly filing it as a setup problem.
    **Why it matters:** Right now the one failure the check exists to catch is
    the one it can miss.
    **What changes:** The single file that decides whether work passed.
    **Risk:** Low — it makes an existing check stricter; nothing new runs.
    **How we'll know:** New tests deliberately break something.

The title after the unit name is optional, and authored like everything else
here. Without it a page has to name each step after its slug — "Plain source",
"Safe html" — which is the least readable line on a page that exists to be
readable, and the objective cannot stand in for it because it is written in
file paths. So `Plain.title(name)` returns what a human wrote or `None`, and
never manufactures one: an em dash or a plain hyphen separates it, a separator
with nothing after it counts as unwritten, and a step with no title is the
ordinary case.

Four decisions worth keeping.

**A field that exists but is empty is not authored.** `plan-check` scaffolds
this form automatically, so the common state on disk is headings and labels
with nothing after them. Treating a blank `**Risk:**` as prose would show a
reviewer an empty form and call it an answer, which is worse than showing
nothing — it looks like somebody considered the question. Blank falls back to
generated text and the unit is reported as unwritten.

**Staleness is keyed off the units' contract digest, never off `plan.json`'s
`revision`.** `plan.write_graph` increments `revision` on *every* `plan-check`
run, so a re-check that changed nothing would mark every `plain.md` in the
repository stale, and a staleness warning that fires when nothing changed is a
staleness warning nobody reads. `digest()` hashes `contract.field_digests` —
the same seal the done-gate uses — so it moves when what the plan *promises*
moves and not otherwise. `verified` is dropped from that seal: it accumulates
as a unit's checks are signed off, and work progressing is not the prose going
out of date.

**Generated text states facts and never judges.** It may say a step changes
four files and runs at the same time as step 2. It may not say the step is low
risk or that it improves anything: nothing mechanical knows either, and a
machine-written "Risk: low" on a page a human signs is a lie with a signature
under it. It also never uses the contract's own vocabulary (`owns`, `forbid`,
`depends_on`, `wave`, `tier`, `budget_tokens`, `subagent`) or a file path,
because the reader of this page does not have those words.

`Plain.unit(name, facts)` takes the facts as a plain dict so that nothing here
has to reach into a plan. The caller assembles it; the keys are:

    number      int        this step's position, 1-based
    of          int        how many steps the plan has
    files       int        how many files this step changes
    waits_for   [int]      step numbers that must finish first
    alongside   [int]      step numbers that run at the same time
    checks      [str]      how it is checked, in a human phrasing

`checks` entries are used verbatim, so they must be sentences a non-technical
reader understands ("the test suite runs"), not commands ("python -m pytest
tests/"). Nothing here scrubs them: a generator that quietly rewrote its input
would also quietly hide a caller passing the wrong thing.
"""

import hashlib
import re

from . import atomic, config as config_mod, contract, frontmatter, plan as plan_mod

PLAN_SECTIONS = ("summary", "why now", "what changes for you",
                 "what could go wrong", "out of scope")
UNIT_FIELDS = ("what it does", "why it matters", "what changes",
               "risk", "how we'll know")

#: The keys `Plain.unit` reads out of the `facts` dict its caller assembles.
FACT_KEYS = ("number", "of", "files", "waits_for", "alongside", "checks")

FILENAME = "plain.md"

_UNIT_PREFIX = "unit:"
# `**What it does:** text`, and the variants a human actually types: the colon
# inside or outside the bold, stray spaces, a curly apostrophe from a word
# processor.
_FIELD = re.compile(r"^\s*\*\*\s*(?P<label>[^*]+?)\s*\*\*\s*:?\s*(?P<value>.*)$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_UNWRITTEN = "Nobody has written this down yet."

# `## Unit: 01-plain-source — Make the safety check honest`.
_UNIT_HEADING = re.compile(r"^\s*unit\s*:\s*(?P<rest>.*)$", re.I)
# An em dash or en dash anywhere splits the name from the title. A plain
# hyphen only does so surrounded by whitespace: unit names are full of hyphens
# themselves, and `01-a-long-hyphenated-name` must not lose its own tail.
_DASH = re.compile(r"[—–]")
_SPACED_HYPHEN = re.compile(r"\s+-+\s+")
_SEPARATORS = "—–- \t"
# Mirrors `frontmatter._HEADING`. `Document.sections()` lowercases its keys,
# which is right for looking a section up and wrong for a title a human
# capitalised on purpose — so bodies still come from `sections()` and only the
# heading's original text is recovered here.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
# What `scaffold` writes after the separator. A comment, so the rule in
# `_split_heading` reads it as no title at all: an author sees that a title is
# invited without the form having answered for them.
TITLE_PLACEHOLDER = "<!-- a short title, in plain words -->"


# --------------------------------------------------------------------------- #
# small text helpers
# --------------------------------------------------------------------------- #

def _label(text):
    """A heading or bolded label, normalised to the keys used here."""
    text = _HTML_COMMENT.sub("", str(text or ""))
    text = text.replace("’", "'").strip()
    text = text.rstrip(":").strip()
    return re.sub(r"[ \t]+", " ", text).lower()


def _body(text):
    """Authored prose: comments gone, per-line trailing space gone, stripped.

    Line breaks inside a field are kept — a human may write two sentences on
    two lines and the page renders them as they were written.
    """
    text = _HTML_COMMENT.sub("", str(text or ""))
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ints(value):
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        value = [value]
    out = [_int(item) for item in value]
    return [number for number in out if number > 0]


def _join(pieces):
    """`a`, `a and b`, `a, b and c` — the way a person writes a list."""
    pieces = [piece for piece in pieces if piece]
    if len(pieces) <= 1:
        return "".join(pieces)
    return "%s and %s" % (", ".join(pieces[:-1]), pieces[-1])


def _steps(numbers):
    if len(numbers) == 1:
        return "step %d" % numbers[0]
    return "steps %s" % _join([str(number) for number in numbers])


def _display(name):
    """`what it does` -> `What it does`, for the form on disk."""
    return name[:1].upper() + name[1:]


def _headings(doc):
    """`{lowercased heading: the heading as written}` for one document."""
    out = {}
    for line in (doc.body if doc is not None else "").splitlines():
        match = _MD_HEADING.match(line)
        if match:
            text = match.group(2).strip()
            out[text.lower()] = text
    return out


def _split_heading(text):
    """`Unit: 01-alpha — A title` -> `("01-alpha", "A title")`.

    The title is optional and absent is the common case; a separator with
    nothing usable after it — including the scaffolded placeholder, which is an
    HTML comment — is absent too, by the same rule criterion 4 applies to the
    five fields. Nothing is invented here: a unit with no authored title
    reports `None`, and deriving something readable from the slug is the
    caller's decision to make, not this module's.
    """
    text = _HTML_COMMENT.sub("", str(text or ""))
    match = _UNIT_HEADING.match(text)
    rest = (match.group("rest") if match else text).strip()
    if not rest:
        return "", None

    for pattern in (_DASH, _SPACED_HYPHEN):
        parts = pattern.split(rest, 1)
        if len(parts) == 2:
            name, title = parts
            break
    else:
        # No separator. The name is the first token and anything after it is
        # taken as the title anyway: a human who wrote one meant it, and
        # silently dropping authored words is the worse failure.
        parts = rest.split(None, 1)
        name, title = parts[0], (parts[1] if len(parts) > 1 else "")

    title = re.sub(r"\s+", " ", title.strip(_SEPARATORS)).strip()
    return name.strip(_SEPARATORS), title or None


# --------------------------------------------------------------------------- #
# layout and digest
# --------------------------------------------------------------------------- #

def path(layout, slug):
    """`.ctx/plans/<slug>/plain.md`, whether or not it exists."""
    return plan_mod.plan_dir(layout, slug) / FILENAME


def digest(layout, slug):
    """A stable digest of what this plan's units promise.

    Per unit it is `contract.combine(contract.field_digests(...))` — the same
    seal the done-gate compares against — minus `verified`, which records
    progress rather than intent. The plan-level hash is over `name=digest`
    lines in name order, so adding, removing or rewriting a unit moves it and
    re-running `plan-check` does not.
    """
    parts = []
    for unit in plan_mod.load_units(layout, slug):
        fields = contract.field_digests(unit.doc)
        fields.pop("verified", None)
        parts.append("%s=%s" % (unit.name, contract.combine(fields)))
    joined = "\n".join(sorted(parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def _parse_block(text):
    """One `## Unit:` block -> `{field: authored text}`.

    An unrecognised bolded label, and everything wrapped under it, is dropped
    rather than raised on: a human wrote this file by hand and will get a label
    slightly wrong, which is not a reason to refuse to render the page.
    """
    fields = dict((field, "") for field in UNIT_FIELDS)
    current, buffer = None, []

    def flush():
        if current in fields:
            fields[current] = _body("\n".join(buffer))

    for line in str(text or "").splitlines():
        match = _FIELD.match(line)
        if match:
            flush()
            current, buffer = _label(match.group("label")), [match.group("value")]
        elif current is not None:
            buffer.append(line)
    flush()
    return fields


def _parse(doc, names):
    """`(plan sections, unit blocks, titles, unknown unit names)`."""
    plan_text = dict((section, "") for section in PLAN_SECTIONS)
    units, titles, unknown = {}, {}, []
    known = dict((name.lower(), name) for name in names)
    written = _headings(doc)
    for heading, body in (doc.sections() if doc is not None else {}).items():
        key = _label(heading)
        if key in plan_text:
            plan_text[key] = _body(body)
        elif key.startswith(_UNIT_PREFIX):
            # From the heading as the author capitalised it, not the lowercased
            # key `sections()` hands back — a title is prose.
            raw_name, title = _split_heading(written.get(heading, heading))
            raw = _label(raw_name)
            if not raw:
                continue
            name = known.get(raw)
            if name is None:
                # Never silently dropped: a block naming a unit the plan does
                # not have is prose somebody wrote that nothing will show, and
                # the caller has to be able to say so.
                if raw not in unknown:
                    unknown.append(raw)
            else:
                units[name] = _parse_block(body)
                if title:
                    titles[name] = title
    return plan_text, units, titles, unknown


class Plain:
    """What a human has actually written for one plan, and what is missing.

    `plan` always has all five `PLAN_SECTIONS` keys; `units` holds only the
    blocks the file actually carries, keyed by the plan's own unit name. Use
    `unit(name, facts)` to render one — it falls back to generated sentences
    for every field nobody filled in, and `title(name)` for the step's
    authored title, which never falls back to anything.
    """

    def __init__(self, slug, names, exists=False, stamped=None, current=None,
                 plan=None, units=None, titles=None, unknown=None):
        self.slug = slug
        self.names = list(names or [])
        self.exists = bool(exists)
        self.digest = stamped or None
        self.current_digest = current
        self.plan = plan if plan is not None else dict(
            (section, "") for section in PLAN_SECTIONS)
        self.units = units if units is not None else {}
        self.titles = dict(titles or {})
        self.unknown = list(unknown or [])
        self.missing_fields = dict(
            (name, [field for field in UNIT_FIELDS
                    if not (self.units.get(name) or {}).get(field)])
            for name in self.names
        )
        # `missing` is the reviewer-facing list: units with no authored prose
        # at all, which is what a scaffolded-but-unfilled form leaves behind.
        # Partly filled units are in `missing_fields` instead, so a caller can
        # tell "nobody wrote this" from "nobody wrote the risk".
        self.missing = [name for name in self.names
                        if len(self.missing_fields[name]) == len(UNIT_FIELDS)]

    def title(self, name):
        """The step's authored title, or `None` when nobody wrote one.

        `None` is the whole answer. A page that wants a heading for an
        untitled step derives one from the slug itself — that is the caller's
        call, and inventing one here would make an authored title and a
        machine-made one indistinguishable to everything downstream.
        """
        return self.titles.get(name) or None

    @property
    def stale(self):
        """True when the stamped digest is absent or no longer matches."""
        if not self.digest:
            return True
        return self.digest != self.current_digest

    def unit(self, name, facts=None):
        """Render one unit: authored text where there is any, facts elsewhere.

        Returns the five `UNIT_FIELDS` as keys, plus `name`, `title` (the
        authored title or `None` — never generated, so it is not in
        `generated`), `generated` (`{field: bool}` — True where the text below
        was generated) and `authored` (True when a human wrote at least one
        field).
        """
        authored = self.units.get(name) or {}
        fallback = _generate(facts or {})
        out = {"name": name, "title": self.title(name)}
        flags = {}
        for field in UNIT_FIELDS:
            written = _body(authored.get(field, ""))
            flags[field] = not written
            out[field] = written or fallback[field]
        out["generated"] = flags
        out["authored"] = any(not generated for generated in flags.values())
        return out


def load(layout, slug):
    """Read `plain.md` for `slug`. Absence is a normal answer, not an error."""
    names = [unit.name for unit in plan_mod.load_units(layout, slug)]
    try:
        doc = frontmatter.read(path(layout, slug))
    except (OSError, UnicodeDecodeError, ValueError):
        # An unreadable file is reported the same way an absent one is: the
        # page still renders, with everything marked unwritten.
        doc = None
    stamped = ""
    if doc is not None:
        stamped = str(doc.meta.get("digest") or "").strip()
    plan_text, units, titles, unknown = _parse(doc, names)
    return Plain(slug, names, exists=doc is not None, stamped=stamped,
                 current=digest(layout, slug), plan=plan_text,
                 units=units, titles=titles, unknown=unknown)


# --------------------------------------------------------------------------- #
# generated fallback
# --------------------------------------------------------------------------- #

def _generate(facts):
    """Sentences built only from `facts` — position, size, order, checks.

    Deliberately narrow. Two of the five fields ask *why*, and no fact answers
    that, so they say so instead of guessing.
    """
    number, total = _int(facts.get("number")), _int(facts.get("of"))
    files = _int(facts.get("files"))
    waits = _ints(facts.get("waits_for"))
    alongside = _ints(facts.get("alongside"))
    checks = [str(check).strip() for check in (facts.get("checks") or [])
              if str(check).strip()]

    does = []
    if number and total:
        does.append("Step %d of %d." % (number, total))
    elif number:
        does.append("Step %d." % number)
    if waits:
        does.append("It waits for %s to finish." % _steps(waits))
    if alongside:
        does.append("It runs at the same time as %s." % _steps(alongside))

    if files == 1:
        changes = "It changes 1 file."
    elif files > 1:
        changes = "It changes %d files." % files
    else:
        changes = "No files are recorded as changing."

    return {
        "what it does": " ".join(does) or _UNWRITTEN,
        "why it matters": _UNWRITTEN,
        "what changes": changes,
        "risk": _UNWRITTEN,
        "how we'll know": (
            "Checked by: %s." % "; ".join(checks) if checks
            else "No checks are recorded for this step."
        ),
    }


# --------------------------------------------------------------------------- #
# the form
# --------------------------------------------------------------------------- #

def _plan_form():
    return "\n\n".join("## %s" % _display(section) for section in PLAN_SECTIONS)


def _unit_form(name):
    lines = ["## Unit: %s — %s" % (name, TITLE_PLACEHOLDER)]
    lines.extend("**%s:**" % _display(field) for field in UNIT_FIELDS)
    return "\n".join(lines)


def _names(units):
    """Accept unit names or `plan.Unit` objects, in the order given."""
    out = []
    for entry in units or []:
        name = str(getattr(entry, "name", entry)).strip()
        if name and name not in out:
            out.append(name)
    return out


def scaffold(layout, slug, units, force=False):
    """Write, or extend, the form a human fills in. Returns the path.

    On a plan with no `plain.md` this writes the whole form: every plan
    section, one block per unit, each of the five fields present and empty, and
    the current `digest:` stamped. A form beats a blank page — the questions
    are the point.

    On one that exists it **appends blocks for units that have none** and
    touches nothing else, so re-scaffolding after a plan grows can never edit
    somebody's prose. The stamped digest is left alone in that case too: the
    plan just changed, the prose has not been re-read against it, and saying so
    is the honest answer. `force=True` rewrites the whole form and re-stamps.
    """
    names = _names(units)
    target = path(layout, slug)
    if force or not target.is_file():
        meta = {"ctx_schema": config_mod.SCHEMA, "plan": slug,
                "digest": digest(layout, slug)}
        body = "\n\n".join([_plan_form()] + [_unit_form(name) for name in names])
        return atomic.write_text(target, frontmatter.Document(meta, body).render())

    doc = frontmatter.read(target)
    _, present, _titles, unknown = _parse(doc, names)
    seen = set(present) | set(unknown)
    pending = [name for name in names if name not in seen]
    if not pending:
        return target
    text = target.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    addition = "\n".join(_unit_form(name) for name in pending)
    # Appended, never re-rendered: what is already in the file stays byte for
    # byte, including whatever the author did to the formatting.
    return atomic.write_text(target, "%s\n%s\n" % (text, addition))
