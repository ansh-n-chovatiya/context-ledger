"""The one JSON-safe dict every rendered preview page is built from.

A plan on disk is unit contracts and a derived graph. A preview page is a thing
a non-technical person reads once and signs. Between the two sits exactly one
assembly step — this module — so that the HTML never sees a plan, never reaches
into a `Unit`, and therefore cannot invent a fact, drop one, or reword one on
its way to the reader.

**Everything schedule-shaped is asked of `plan.py`, never recomputed here.**
`plan-check` has derived waves, ordering, contention and a critical-path
estimate since before there was a page, and `commands._plan_intelligence` turns
those same four functions into the `--json` document. A second derivation would
be a second truth, free to disagree with the first on the one artefact somebody
signs — so `check`, `waves`, `parallelism`, `bottlenecks`, `critical_path`,
`ownership_gaps` and `covers_any` are called, and
`tests/test_preview_model.py::TestItAgreesWithPlanCheck` runs the real command
against the same fixture and compares the two documents fact by fact.

**Prose is rendered; structure is not.** Every value the model calls prose has
been through `preview_html.markup`, so it is sanitized, redacted and escaped,
and arrives as HTML the renderer drops in unchanged. Everything else — a path,
a tier, a status, a token budget, a verify command — is left exactly as the
contract states it, because scrubbing structured values is how
`budget_tokens: 60000` once became `budget_tokens: <<redacted>>` and
`credential: none` once inverted. The renderer escapes those at the point it
prints them; `preview_html.embed_json` carries the whole dict into a `<script>`
block without either treatment.

The prose keys, and the only ones a renderer may insert unescaped:

    plan.title                  one line, already inline-marked-up
    plain.sections.*            block HTML
    steps[].title               one line
    steps[].plain.*             block HTML (bar `generated` and `provenance`,
                                which are flags and labels)
    steps[].tech.criteria[]     block HTML
    critical_path.basis         one line

**Five tiers answer every prose field, and the model says which one did.**
Nothing on this page may be blank, and nothing on it may be invented, so a
field nobody wrote falls back through progressively weaker *facts* rather than
to a placeholder. The first rung with something to say wins:

    1. what a human wrote in `plain.md`                    -> `authored`
    2. what the spec's intake recorded, for the three plan
       sections `spec.INTAKE_CATEGORIES` names             -> `inferred`
    3. the unit's own `## Objective` / `## Background`     -> `inferred`
       (and, for `why it matters` only, the plan's own
       `why now` where the unit has no background — which
       is every unit, since `plan.UNIT_TEMPLATE` has no
       such section; see `_plain_why`)
    4. the ownership-gap facts `plan.py` already derived   -> `inferred`
    5. the position sentence `plain._generate` builds      -> `generated`

Every rung below the first is held to the plain half's vocabulary rule by
`_has_technical_vocabulary`, tier 2 included: a recorded intake answer is
free-form text somebody typed, and the bar is about what reaches the reader,
not about which rung produced it.

`plain.md`'s contract is untouched: authored text still wins outright, and
nothing here writes to that file on anybody's behalf. Tiers 2-4 quote source
text that already exists on disk — a spec answer, a unit's own objective, a
derived fact — and never compose a judgment of their own, which is why they
are labelled `inferred` rather than `generated` and why a reader can be told
where the words came from.

`steps[].plain.provenance` and `plain.provenance` carry that label per field.
They are *additive*: `steps[].plain.generated` keeps exactly its old meaning —
True whenever a human did not write the field — so a renderer that only knows
about the flag still marks the same fields it always did, and a renderer that
knows about provenance can say something more useful about why.

**A step is headed by what a human called it**, from `plain.md`'s block
heading, falling back to the slug in words (`01-plain-source` -> "Plain
source") when nobody wrote one. `steps[].title_generated` says which of the two
you are looking at, with the same polarity as `steps[].plain.generated`: True
means the machine produced it. It sits beside the value it describes rather
than inside that map, so that every key of `plain.generated` still has a
matching key in `plain` — a renderer pairing the two off cannot trip over a
flag whose text lives somewhere else.

**No clock.** The page is committed, so bytes that change because a day passed
are a spurious diff on every reviewer's branch. `plan.json`'s `generated` date
is carried through as data; the clock that produced it is `plan.py`'s. A test
parses this module and fails on any call that could read one.

**`plan.json`'s `revision` is deliberately not here**, not even as a technical
fact. `plan.write_graph` bumps that counter on every `plan-check` run, and this
dict is embedded verbatim in a page that is committed *and* rewritten on every
`plan-check` — so carrying the number, anywhere in the model, means every run
dirties a tracked file whether or not the plan changed. It was carried once, in
the technical block, and it reached the page twice: a footer sentence and the
embedded JSON. `plan.digest` is the page's identity instead — it hashes what
the units promise, so it moves when the plan's substance moves and not
otherwise, and it is what `ctx start` reads back out of the page to decide
whether the page is behind. The counter stays in `plan.json`, where a counter
belongs, and `plan-check --json` still reports it.
"""

import json
import re

from . import (atomic, config as config_mod, plain as plain_mod,
               plan as plan_mod, preview_html, spec as spec_mod, verify)

#: Version of the document `view_model` returns. Independent of `ctx_schema`:
#: the page's shape and the ledger's file format move for different reasons,
#: and a renderer needs to know which of the two it is looking at.
SCHEMA = 1

DATA_FILENAME = "preview.data.json"
HTML_FILENAME = "preview.html"

#: `plain.UNIT_FIELDS` -> the key the model uses. The authored file is written
#: in whole questions ("How we'll know"); JSON wants identifiers. Derived from
#: `plain.UNIT_FIELDS` order so a field added there fails loudly here rather
#: than being silently dropped from the page.
FIELD_KEYS = ("what", "why", "changes", "risk", "how_we_know")

#: One human phrasing per `verify` kind, for the generated "How we'll know".
#:
#: `plain._generate` renders these **verbatim** and scrubs nothing, so they are
#: held to the same rules as the rest of the generated text: no command, no
#: path, and none of the contract's own vocabulary, because the person reading
#: them does not have those words. `verify.label_of` is the technical answer to
#: the same question and is deliberately not reused here — it prints the
#: command.
#:
#: They also carry no character HTML-escaping would rewrite, so the sentence in
#: the model and the sentence on the page are the same sentence. An apostrophe
#: is not wrong, it just makes "is this phrase on the page?" a question about
#: entities instead of about English.
CHECK_PHRASES = {
    "diff": "the step really changed something",
    "exists": "a file it had to produce is there",
    "symbol": "the code it had to write is there",
    "review": "the change is read back and reviewed",
    "test_first": "a test was written first, and failed first",
    "cmd": "the automated checks all pass",
    "rubric": "the work is judged against a written standard",
    "human": "a person signs it off",
}
# Only reachable if a kind is registered into `verify.KIND_TABLE` without being
# given a phrasing above; a test asserts the two agree, so this says what it
# knows rather than guessing.
_UNPHRASED = "another check runs"

_INDEX = re.compile(r"^\d+[-_. ]+")
_SPACES = re.compile(r"\s+")
# The same shape `plan.Unit.interfaces` strips out of a contract section. Held
# here rather than reached for across the module boundary: it is a literal, not
# a derivation, and nothing about the two uses has to move together.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# `markup` wraps a paragraph in `<p>`, which a caller putting the value inside
# an `<h1>` cannot use: an HTML parser closes the heading at the `<p>`.
_ONE_PARAGRAPH = re.compile(r"\A<p>(?P<inner>.*)</p>\Z", re.S)


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

def data_path(layout, slug):
    """The embedded model, written beside the plan, whether or not it exists."""
    return plan_mod.plan_dir(layout, slug) / DATA_FILENAME


def html_path(layout, slug):
    """The rendered page, beside the plan. This module never writes it."""
    return plan_mod.plan_dir(layout, slug) / HTML_FILENAME


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

def _phrase(text, patterns=()):
    """One line of prose: redacted, escaped, inline markup, no block wrapper.

    A result that is exactly one paragraph is unwrapped so it can sit inside a
    heading. Anything longer keeps its blocks — a title that turned out to be
    three paragraphs is the renderer's problem to display, not this function's
    to silently truncate.
    """
    rendered = preview_html.markup(text, patterns)
    match = _ONE_PARAGRAPH.match(rendered)
    if match and "<p>" not in match.group("inner"):
        return match.group("inner")
    return rendered


def _words(text, drop_index=False):
    """A slug as a person would say it: `01-plain-source` -> `Plain source`.

    Hyphens are only opened out when the text has no spaces of its own, so a
    heading somebody actually wrote ("Plain-language preview") is left alone.
    """
    text = str(text or "").strip()
    if drop_index:
        text = _INDEX.sub("", text)
    if " " not in text:
        text = text.replace("-", " ").replace("_", " ")
    text = _SPACES.sub(" ", text).strip()
    return text[:1].upper() + text[1:]


def _plan_title(layout, slug):
    """The plan's name in words, from its README heading or from the slug.

    `ctx plan` scaffolds the heading as `Plan — <slug>`, so the label is
    stripped and both routes land on the same answer; a human who replaces the
    heading with a real sentence gets that sentence instead.
    """
    heading = ""
    try:
        text = plan_mod.readme_path(layout, slug).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    for line in text.splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            break
    for label in ("Plan — ", "Plan - ", "Plan: "):
        if heading.startswith(label):
            heading = heading[len(label):].strip()
            break
    return _words(heading or slug)


def _check_phrases(checks):
    """How this unit is checked, in sentences, deduplicated, cheapest first.

    Two `cmd` checks are one fact to a reader — "the project's own checks are
    run" twice is noise, not detail.
    """
    out = []
    for check in verify.ordered(checks):
        phrase = CHECK_PHRASES.get(check.get("kind"), _UNPHRASED)
        if phrase not in out:
            out.append(phrase)
    return out


# --------------------------------------------------------------------------- #
# the plan, as `plan.py` already derived it
# --------------------------------------------------------------------------- #

def _graph_document(layout, slug):
    """`plan.json`, or a refusal naming the command that writes it.

    Absence is not a `KeyError` three frames later: it means nobody has run
    `plan-check`, which is a thing the person at the terminal can fix.
    """
    path = plan_mod.graph_path(layout, slug)
    advice = ("no plan graph for %r — run `ctx plan-check %s` first"
              % (slug, slug))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(advice) from exc
    except ValueError as exc:
        raise SystemExit(
            "the plan graph for %r could not be read — re-run "
            "`ctx plan-check %s` to rewrite it" % (slug, slug)
        ) from exc
    return document if isinstance(document, dict) else {}


def _grouped(layout, slug, units):
    """`{round: [Unit, ...]}` — the waves `plan.py` computes, with nothing lost.

    Two fallbacks, both for plans that would never dispatch but must still be
    *shown*: a plan whose units fail `validate` gets no waves out of `check`,
    so it is ordered by `waves` directly; and a dependency cycle leaves its
    members unplaced by either, so they land in a trailing round. Dropping them
    would leave the page silently short a step, which is the one failure a
    reviewer cannot see.
    """
    grouped, _problems = plan_mod.check(layout, slug)
    if not grouped and units:
        grouped, _problems = plan_mod.waves(units)
    grouped = dict((level, list(members)) for level, members in grouped.items())

    placed = set(unit.name for members in grouped.values() for unit in members)
    unplaced = [unit for unit in units if unit.name not in placed]
    if unplaced:
        grouped[(max(grouped) + 1) if grouped else 1] = unplaced
    return grouped


def _ownership(units, numbers, rounds):
    """`[{path, steps, contested, bottleneck}]` for every declared path.

    Who owns a path is asked of `plan.covers_any` — the same rule the wave
    collision check uses — so a unit owning `ctx/*.py` counts as an owner of
    `ctx/cli.py` here exactly as it would there. `contested` is a *same-round*
    question, which is the only one that matters to a reader: two steps
    declaring one file in different rounds take turns; in one round they race.
    """
    declared = []
    for unit in units:
        for path in unit.owns:
            if path not in declared:
                declared.append(path)

    contended = set(path for path, _owners in plan_mod.bottlenecks(units))
    entries = []
    for path in sorted(declared):
        owners = [unit for unit in units if plan_mod.covers_any(path, unit.owns)]
        levels = [rounds[unit.name] for unit in owners]
        entries.append({
            "path": path,
            "steps": sorted(numbers[unit.name] for unit in owners),
            "contested": any(levels.count(level) > 1 for level in levels),
            "bottleneck": path in contended,
        })
    return entries


# --------------------------------------------------------------------------- #
# the plan sections, from what the spec's intake was already told
# --------------------------------------------------------------------------- #

#: The day-stamp `spec.resolve` and `spec.record_inferred` both put on the end
#: of a Resolved line. Provenance for the questions file, not prose for a page.
_ANSWER_DATE = re.compile(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*\Z")
#: `spec.record_inferred`'s audit tail: " — inferred, not asked (why) (date)".
_NOT_ASKED = re.compile(r"\s+—\s+inferred, not asked\b.*\Z", re.S)


def _intake_answer(item, prefix):
    """The answer one Resolved line records, or None if it carries none.

    Two shapes reach `spec.py`'s Resolved section, and `spec.intake_status`
    counts a category as answered by either, so both are read here:

        Why now: <answer> — inferred, not asked (<rationale>) (<date>)
        Why now: <question> → <answer> (<date>)

    The first is `record_inferred`, where nobody was asked; the second is
    `resolve` ticking off a real question whose text named the category.
    Everything after the label is the answer in the first case and the
    *question* in the second — so the arrow wins wherever there is one. A
    "why now" section that quotes the question back at the reader would be
    worse than an empty one, because it reads like an answer.

    The audit tail and the date come off either way: they say why the
    questions file can be trusted, which is not what this page is for.
    """
    body = _NOT_ASKED.sub("", item[len(prefix):])
    if "→" in body:
        body = body.split("→", 1)[1]
    return _ANSWER_DATE.sub("", body).strip() or None


def _fill_plan_sections(layout, spec_slug, source):
    """`{section: "authored"|"inferred"|"generated"}`, filling tier 2 in place.

    Three of `plain.PLAN_SECTIONS` are `spec.INTAKE_CATEGORIES` under the same
    name, and `ctx spec-ready` will not open planning until all three have been
    answered — so a plan whose summary form nobody filled in is not blank, its
    answers are just somewhere else. Where a section is unauthored and its
    category was answered, the answer is copied into `source.plan`, the
    in-memory `Plain` the caller just loaded.

    **Never into `plain.md` itself.** That file's contract is that a human owns
    it and nothing writes to it on their behalf; this is the page choosing what
    to show, not the tool filling in somebody's form.

    A section with no intake category behind it — "summary", "out of scope" —
    is left exactly as it was found. There is nothing on disk that answers it,
    and composing one would be the invention this whole ladder exists to avoid.
    """
    try:
        _blocking, _non, resolved = spec_mod.questions(layout, spec_slug)
    except (OSError, UnicodeDecodeError, ValueError):
        # Guarded exactly as `plain.load` guards its own read: an unreadable
        # questions file means this tier found nothing, not that the page
        # cannot be rendered. `ctx preview` is often the first thing run
        # against a ledger somebody else wrote.
        resolved = []
    provenance = {}
    for section in plain_mod.PLAN_SECTIONS:
        if source.plan.get(section, ""):
            provenance[section] = "authored"
            continue
        answer = None
        if section in spec_mod.INTAKE_CATEGORIES:
            # The prefix `spec.intake_status` matches on, so a category that
            # gate counts as answered is one this can quote. The questions
            # file is append-only, so a later line is a correction of an
            # earlier one and the last answer wins.
            prefix = (section + ":").lower()
            for item in resolved:
                if item.lower().startswith(prefix):
                    answer = _intake_answer(item, prefix) or answer
        # Held to the same rule as tiers 3 and 4: the bar is about what reaches
        # the reader, not about which tier produced it. An intake answer is
        # free-form text somebody typed at a terminal — `ctx infer "... rewrite
        # ctx/plan.py so nothing else owns it"` is a perfectly good answer to
        # record and exactly what this half of the page may not carry. Declined
        # whole rather than laundered, and only that section: an answer full of
        # paths does not cost a clean sibling its tier.
        if answer and _has_technical_vocabulary(answer):
            answer = None
        if answer:
            source.plan[section] = answer
            provenance[section] = "inferred"
        else:
            provenance[section] = "generated"
    return provenance


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #

def view_model(layout, slug):
    """The whole preview document for one plan, as JSON-safe data.

    Deterministic: the same plan on disk produces the same bytes, whatever the
    date and whoever is running it.
    """
    graph = _graph_document(layout, slug)
    patterns = (config_mod.load(layout) or {}).get("redact") or []

    loaded = plan_mod.load_units(layout, slug)
    grouped = _grouped(layout, slug, loaded)
    # The same flattening `commands._plan_intelligence` does, so the two
    # reports are asking the same functions about the same list.
    units = [unit for level in sorted(grouped) for unit in grouped[level]]

    numbers = dict((unit.name, index + 1) for index, unit in enumerate(units))
    rounds = dict((unit.name, level)
                  for level in grouped for unit in grouped[level])
    source = plain_mod.load(layout, slug)

    count, wave_count, ratio = plan_mod.parallelism(grouped)
    estimate, total_tokens, per_wave = plan_mod.critical_path(grouped)
    gaps, truncated = plan_mod.ownership_gaps(layout, units)
    # Built once, in the shape the JSON carries, so the fact a step's risk
    # states and the fact the page's gap table shows are the same list.
    gap_files = [{"path": path, "tests": tests} for path, tests in gaps]

    # The plan's originating spec. `write_graph` defaults `spec` to the plan
    # slug, so this is the plan slug for a `--no-spec` plan, and the one
    # expression is shared with the `"plan"."spec"` key below rather than
    # written out twice.
    spec_slug = str(graph.get("spec") or slug)
    plan_provenance = _fill_plan_sections(layout, spec_slug, source)
    # Tier 3's other half. A unit's "why it matters" is meant to come from its
    # own `## Background` — except `UNIT_TEMPLATE` has no such section, so no
    # unit this tool scaffolds has ever had one and the field fell all the way
    # to tier 5 on the ordinary path. The plan's own recorded reason for
    # existing is the nearest true answer to "why does this step matter", and
    # it is already on disk; borrowing it beats printing "nobody has written
    # this down yet" under a heading somebody is being asked to sign.
    #
    # Read after `_fill_plan_sections` has run, so it is whatever that
    # resolved — a human's `plain.md` paragraph or the intake answer — rather
    # than a second reading of the questions file.
    plan_why = str(source.plan.get("why now") or "").strip() or None

    return {
        "schema": SCHEMA,
        "plan": {
            "slug": slug,
            "title": _phrase(_plan_title(layout, slug), patterns),
            "spec": spec_slug,
            # No `revision`: see the module docstring. It is the one value in
            # `plan.json` that moves without the plan moving, and this dict is
            # embedded in a committed file.
            "generated": str(graph.get("generated") or ""),
            "digest": source.current_digest or "",
            "counts": {
                "units": len(units),
                "waves": len(grouped),
                "budget_tokens": sum(unit.budget for unit in units),
            },
        },
        "plain": {
            "present": source.exists,
            "stale": source.stale,
            "missing": list(source.missing),
            "unknown": list(source.unknown),
            "sections": dict(
                (section, preview_html.markup(text, patterns))
                for section, text in source.plan.items()
            ),
            "provenance": plan_provenance,
        },
        "steps": [_step(unit, numbers, rounds, grouped, source, patterns,
                        gap_files, plan_why)
                  for unit in units],
        "graph": {
            "nodes": [{"number": numbers[unit.name], "slug": unit.name,
                       "round": rounds[unit.name]} for unit in units],
            "edges": [{"from": numbers[name], "to": numbers[unit.name]}
                      for unit in units for name in unit.depends_on
                      if name in numbers],
        },
        "ownership": _ownership(units, numbers, rounds),
        "bottlenecks": [
            {"path": path,
             "steps": sorted(numbers[name] for name in owners if name in numbers)}
            for path, owners in plan_mod.bottlenecks(units)
        ],
        "ownership_gaps": {"files": gap_files, "truncated": truncated},
        "concurrency": {"units": count, "waves": wave_count, "ratio": ratio},
        "critical_path": {
            "estimate_tokens": estimate,
            "total_tokens": total_tokens,
            "waves": [{"wave": level, "tokens": tokens}
                      for level, tokens in per_wave],
            "basis": _phrase(
                "A rough total of what each step said it would cost, counting "
                "steps that run together once. It is an estimate nobody has "
                "measured, not a promise."
            ),
        },
    }


def _title(unit, authored, patterns):
    """`(title, generated)` — what a human called this step, or what it is.

    `plain.Plain.title` returns what somebody wrote in the block heading, or
    `None`; it deliberately invents nothing, so the fallback is here. Both
    routes go through `_phrase`, because both end up inside a heading element
    and one of them is prose out of a file a human edits — a title carrying
    `<script>` or a secret is escaped and redacted exactly like the five
    fields below it.

    The fallback also catches an authored title that *renders* to nothing: a
    heading of zero-width characters, or the placeholder comment `scaffold`
    writes, is not a title, and a step headed by an empty string cannot be
    read. `generated` is True in that case, because that is what happened.
    """
    title = _phrase(authored or "", patterns)
    if title:
        return title, False
    return _phrase(_words(unit.name, drop_index=True), patterns), True


#: The contract's own vocabulary, which `plain.py` bars from this half of the
#: page: "the reader of this page does not have those words". Stated here as
#: well because tier 3 quotes prose nobody wrote for that reader, and something
#: has to hold it to the same rule. `tests/test_preview_model.py` asserts this
#: tuple and the one the page's own rule is tested with are the same list, so
#: the two cannot drift into disagreeing about what the rule is.
CONTRACT_WORDS = ("owns", "forbid", "depends_on", "wave", "tier",
                  "budget_tokens", "subagent")

#: `CONTRACT_WORDS` as whole words. Anchored because the unanchored substring
#: check this replaced declined ordinary English on sight of a fragment:
#: "Downstream consumers see a clear error" carries `owns`, "a frontier case"
#: carries `tier`, "it waves goodbye" carries `wave` — three sentences a
#: reader would have understood, each thrown away for a word that was never
#: there. `_` is a word character, so `\b` bounds `depends_on` and
#: `budget_tokens` at both ends exactly as it bounds the rest.
_CONTRACT_WORD = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(word) for word in CONTRACT_WORDS),
    re.I)

#: A filename: a dotted token ending in something a source file ends in.
_FILENAME = re.compile(
    r"[\w.-]+\.(?:py|pyi|js|jsx|ts|tsx|json|ya?ml|toml|ini|cfg|md|rst|sh|bash|"
    r"html?|css|sql|go|rs|rb|java|kt|swift|c|h|cc|cpp|hpp|lock|txt)\b",
    re.I)
#: Any token carrying a `/`. English uses one too — "and/or", "read/write",
#: "he/she" — and those are two whole words either side of it with nothing
#: else; anything else with a slash in it is shaped like a path.
_SLASHED = re.compile(r"\S*/\S*")
_WORD_PAIR = re.compile(r"\A[A-Za-z]{2,}/[A-Za-z]{2,}\Z")


def _has_technical_vocabulary(text):
    """True when this prose says something the plain half may not say.

    A file path or the contract's own words. `plain.py` bars both from the
    text a non-technical reader is shown, and the bar is about what reaches
    that reader — not about which tier produced it, so prose quoted out of a
    unit contract is held to it exactly as generated text is.
    """
    if _CONTRACT_WORD.search(text):
        return True
    if _FILENAME.search(text):
        return True
    return any(not _WORD_PAIR.match(token.strip(" .,;:!?()[]{}<>`\"'"))
               for token in _SLASHED.findall(text) if token.strip(" .,;:`\"'"))


def _plain_from_objective(unit):
    """(what_it_does, why_it_matters) drawn from the unit's own prose, or
    (None, None) when it has none a reader of this page can be shown.

    HTML comments come out first, the way `plan.Unit.interfaces` treats the
    same problem: a section holding nothing but the scaffold's own `<!-- ...
    -->` hint has not been written, and quoting the hint at a non-technical
    reader is worse than the generated sentence it would displace.

    **An `## Objective` is written for the implementer**, and routinely names
    files and says `owns` and `forbid` — which is right, there, and is exactly
    what the plain half of the page may not carry. So the text is *declined*
    rather than repaired: rewriting or truncating somebody's free-form prose to
    launder it is how a sentence ends up meaning something its author did not
    write. A field that trips the check is treated as though the section were
    empty and falls through to the generated sentence, and only that field —
    an objective full of paths does not cost a clean background its tier.
    """
    objective = _HTML_COMMENT.sub(
        "", unit.doc.section("objective") or "").strip()
    background = _HTML_COMMENT.sub(
        "", unit.doc.section("background") or "").strip()

    def usable(text):
        return bool(text) and not _has_technical_vocabulary(text)

    # The scaffold's own hint is not an objective. `plan.is_unwritten_objective`
    # is the same predicate `plan.validate` refuses on, asked here rather than
    # restated, so the gate and the page cannot disagree about whether anybody
    # wrote one. Without it a freshly scaffolded unit put
    # `<one sentence: the observable outcome>` on the page labelled `inferred`.
    if plan_mod.is_unwritten_objective(objective):
        objective = ""
    return (objective if usable(objective) else None,
            background if usable(background) else None)


def _plain_from_ownership_gap(unit, gap_files):
    """A plain sentence stating the real ownership-gap fact for this unit's own
    owned paths — never `None`, so risk is never left to the generic sentence
    `plain._generate` falls back to. Zero is a fact too: it is what
    `ownership_gaps` actually found, checked the same way a hit is.

    `gap_files` is `ownership_gaps`'s own `files` list — `[{path, tests}, ...]`
    — already computed by the caller; this never recomputes it, so the risk a
    reader is shown and the risk `plan-check` reports are the same finding.

    Whether a gap is *this* unit's is asked of `plan.covers_any`, the same rule
    `_ownership` above uses and the same one the wave collision check uses — so
    a unit owning `src/*.py` is told about the gap on `src/a.py` exactly as it
    would be told about colliding on it. A second ownership rule in this one
    module would be a second truth about the same word.

    **It counts; it does not name.** `plain.py` holds this half of the page to
    a rule — "never uses the contract's own vocabulary ... or a file path,
    because the reader of this page does not have those words" — and a sentence
    listing `src/a.py, src/b.py` breaks it just as surely when the words were
    inferred as when they were generated. A count is the same fact in the
    reader's language, and is the idiom `plain._generate` already uses when it
    says a step changes four files. The paths themselves are two feet away in
    the technical view and in the page's own ownership-gap table, for the
    reader who does have those words.

    Still a fact, not a judgment: how many tests, and that any of them could be
    what breaks. It does not say the step is risky, and `ownership_gaps` is a
    heuristic about which tests *name* a module, so the sentence claims only
    that they refer to it — including the zero case, which claims only that
    the search came up empty, not that the change is safe.
    """
    hits = [gap for gap in gap_files
            if plan_mod.covers_any(gap["path"], unit.owns)]
    tests = set(test for gap in hits for test in gap["tests"])
    if not tests:
        return (
            "No test elsewhere in the project was found to refer to what this "
            "step changes; a mistake here would have to be caught by this "
            "plan's own checks."
        )
    if len(tests) == 1:
        return (
            "One test elsewhere in the project already refers to part of what "
            "this step changes; a change here could be caught by that test "
            "instead of by this plan's own checks."
        )
    return (
        "%d tests elsewhere in the project already refer to part of what this "
        "step changes; a change here could be caught by one of those instead "
        "of by this plan's own checks." % len(tests)
    )


def _plain_why(unit_background, plan_why):
    """Why this step matters: its own `## Background`, else the plan's reason.

    A cross-level fallback, and the only one on the page — so it is worth
    saying why it is honest. `UNIT_TEMPLATE` has no `## Background` section,
    which means the tier meant to answer this field has no source on the
    ordinary path and never did: every unit `ctx plan-unit` scaffolds arrives
    without one, and the field fell to the literal "Nobody has written this
    down yet." The alternative fix — grow the template a sixth section — asks
    somebody to write more prose for every unit of every plan, which is the
    cost this whole design exists to avoid.

    The plan's `why now` is a weaker claim than a unit's own background, not a
    false one: it says why the work this step belongs to is being done, which
    is a true and useful answer to "why does this matter" and is exactly what
    a reviewer signing the page is being asked about. It is labelled
    `inferred` by the caller for that reason — nobody wrote it about *this*
    step — and it is held to the same vocabulary rule as everything else in
    the plain half, because an authored `plain.md` paragraph is free to name a
    file where a unit card is not.
    """
    if unit_background:
        return unit_background
    if plan_why and not _has_technical_vocabulary(plan_why):
        return plan_why
    return None


def _step(unit, numbers, rounds, grouped, source, patterns, gap_files,
          plan_why=None):
    """One step: what a reader needs, then what an engineer needs, separately."""
    level = rounds[unit.name]
    number = numbers[unit.name]
    alongside = sorted(numbers[other.name] for other in grouped[level]
                       if other.name != unit.name)
    waits_for = sorted(numbers[name] for name in unit.depends_on
                       if name in numbers)

    written = source.unit(unit.name, {
        "number": number,
        "of": len(numbers),
        "files": len(unit.owns),
        "waits_for": waits_for,
        "alongside": alongside,
        "checks": _check_phrases(unit.checks),
    })
    # Tiers 3 and 4, consulted only where nobody wrote anything. Keyed by
    # `FIELD_KEYS` — this module's own names — rather than by `plain.py`'s
    # English, so the fallback cannot go quietly missing because a question in
    # the authored form was reworded.
    inferred_what, inferred_why = _plain_from_objective(unit)
    overrides = {
        "what": inferred_what,
        "why": _plain_why(inferred_why, plan_why),
        "risk": _plain_from_ownership_gap(unit, gap_files),
    }

    prose, provenance = {}, {}
    for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS):
        override = overrides.get(key)
        if not written["generated"][field]:
            provenance[key], text = "authored", written[field]
        elif override:
            provenance[key], text = "inferred", override
        else:
            provenance[key], text = "generated", written[field]
        prose[key] = preview_html.markup(text, patterns)
    # Unchanged in meaning: True wherever a human did not write the field,
    # whatever filled it in. A renderer that only knows this flag still marks
    # exactly the fields it marked before `provenance` existed.
    prose["generated"] = dict(
        (key, bool(written["generated"][field]))
        for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS)
    )
    prose["provenance"] = provenance

    title, derived = _title(unit, written.get("title"), patterns)

    return {
        "number": number,
        "slug": unit.name,
        "title": title,
        "title_generated": derived,
        "plain": prose,
        "round": level,
        "alongside": alongside,
        "waits_for": waits_for,
        "tech": {
            "tier": unit.tier,
            "model": unit.model,
            "status": unit.status,
            "budget_tokens": unit.budget,
            "owns": list(unit.owns),
            "reads": list(unit.reads),
            "forbid": list(unit.forbid),
            "depends_on": list(unit.depends_on),
            "criteria": [preview_html.markup(item, patterns)
                         for item in unit.doc.list_items("acceptance criteria")],
            "verify": [_check(check) for check in verify.ordered(unit.checks)],
        },
    }


def _check(check):
    """One `verify` entry as data: its kind, and the gate's own one-line label.

    The label is `verify.label_of`, not a second description of the same check
    — it is what the terminal prints, so the technical view of the page and the
    gate cannot describe a check differently. It carries commands and paths and
    is therefore structure, not prose: the renderer escapes it.
    """
    return {"kind": str(check.get("kind") or ""), "label": verify.label_of(check)}


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def write_data(layout, slug):
    """Write the model beside the plan and return the path.

    Sorted keys and a trailing newline: the file is committed, so two runs over
    an unchanged plan have to produce one set of bytes.
    """
    text = json.dumps(view_model(layout, slug), indent=2, sort_keys=True)
    return atomic.write_text(data_path(layout, slug), text + "\n")
