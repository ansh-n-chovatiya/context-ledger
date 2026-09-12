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
    steps[].plain.*             block HTML (bar `generated`, which is flags)
    steps[].tech.criteria[]     block HTML
    critical_path.basis         one line

**No clock.** The page is committed, so bytes that change because a day passed
are a spurious diff on every reviewer's branch. `plan.json`'s `generated` date
is carried through as data; the clock that produced it is `plan.py`'s. A test
parses this module and fails on any call that could read one.

**`revision` is technical.** `plan.write_graph` bumps it on every `plan-check`
run, so anything user-visible keyed off it would move when nothing moved. It is
carried for the technical view; staleness comes from `plain.digest`, which
hashes what the units promise.
"""

import json
import re

from . import (atomic, config as config_mod, plain as plain_mod,
               plan as plan_mod, preview_html, verify)

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

    return {
        "schema": SCHEMA,
        "plan": {
            "slug": slug,
            "title": _phrase(_plan_title(layout, slug), patterns),
            "spec": str(graph.get("spec") or slug),
            "revision": _int(graph.get("revision")),
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
        },
        "steps": [_step(unit, numbers, rounds, grouped, source, patterns)
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
        "ownership_gaps": {
            "files": [{"path": path, "tests": tests} for path, tests in gaps],
            "truncated": truncated,
        },
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


def _step(unit, numbers, rounds, grouped, source, patterns):
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
    prose = dict(
        (key, preview_html.markup(written[field], patterns))
        for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS)
    )
    prose["generated"] = dict(
        (key, bool(written["generated"][field]))
        for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS)
    )

    return {
        "number": number,
        "slug": unit.name,
        "title": _phrase(_words(unit.name, drop_index=True), patterns),
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


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
