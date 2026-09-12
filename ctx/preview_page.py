"""One HTML file a non-technical person can open, read, and approve.

The reader this page is built for does not open `plan.json`, has no account
anywhere, and may not be technical. They double-click a file with the wifi off
and need to learn six things: what is about to happen, why, what will be
different afterwards, which steps run at the same time, what could go wrong,
and what is deliberately not being done. A page that is factually perfect and
unreadable to that person has failed, whatever the assertions say.

Four decisions follow from that, and each one is load-bearing.

**Nothing is fetched.** No stylesheet link, no font, no script source, no
image URL — not even the SVG's `xmlns`, which is optional for inline SVG in
HTML and is a URL, which is the only thing that matters here. The file works
off a thumb drive with the network unplugged, and `check()` refuses a page
that grew a reference to anything outside itself.

**Nothing is built by script.** The plain view is static markup; the technical
view is the same markup with `display: none` on it. The toggle is a checkbox
and one CSS rule, so a reader with JavaScript disabled — or a PDF printed from
this file — sees the whole document rather than a blank page. The only
`<script>` element carries the view-model as data, for a tool that wants the
facts back out; remove it and the page is unchanged.

**The reader's words, or the engineer's, never both at once.** `owns`,
`forbid`, `depends_on`, `wave`, `tier`, `budget_tokens`, `subagent` and every
file path live inside elements marked `class="tech"`. Everything outside them
is English: steps are numbered from 1, rounds are rounds, and concurrency is
"happens at the same time as step 2" rather than "wave 1". `regions()` is what
makes that assertable — it splits the parsed document in two, so the test can
ask what the *default view* says instead of searching a document that contains
the vocabulary in its technical half by design.

**What the model says, the page says.** `check()` compares a rendered page
against the model it came from and reports five kinds of loss: a step nobody
named, an owned path missing from the technical view, an acceptance-criteria
count that disagrees, a piece of plain-language prose the page dropped, and any
external reference. `render()`'s own output passes it — that is the calibration.
A checker that cannot pass a good page, or cannot fail a bad one, is decoration.

Escaping is not done here. Prose arrives from `preview.view_model` already
sanitized, redacted, escaped and marked up by `preview_html.markup`, and is
inserted verbatim; everything else — paths, slugs, tiers, statuses, token
counts, verify labels — is raw data and goes through `preview_html.escape` or
`preview_html.attr` at the point it is printed. Getting that backwards either
double-escapes the prose into visible entities or prints a path unescaped.

No clock, no identity, no randomness: the page is committed, so two runs over
an unchanged plan have to produce one set of bytes.
"""

import re
from html.parser import HTMLParser

from . import atomic, preview, preview_html

_escape = preview_html.escape
_attr = preview_html.attr

#: The five authored plan sections, in the order the page shows them, each
#: under the heading the contract names. The keys are `plain.PLAN_SECTIONS`
#: verbatim — spaces and all — and are stated here rather than imported so
#: this module has no opinion about `plain.py`'s internals. A section the
#: model grows and this tuple does not know about is still rendered (see
#: `_extra_sections`), because dropping prose a human wrote is the one
#: failure the reader cannot see.
SECTIONS = (
    ("summary", "What we're going to do"),
    ("why now", "Why we're doing it"),
    ("what changes for you", "What will be different afterwards"),
    ("what could go wrong", "What could go wrong"),
    ("out of scope", "What we are not doing"),
)

#: `steps[].plain` keys -> the label above them. "What might go wrong" is
#: deliberately not "What could go wrong": the latter is a section heading,
#: and two identical headings at two levels read as a mistake.
FIELDS = (
    ("what", "What it does"),
    ("why", "Why it matters"),
    ("changes", "What changes"),
    ("risk", "What might go wrong"),
    ("how_we_know", "How we'll know it worked"),
)

#: The few words the page cannot avoid, explained in the reader's terms. None
#: of them may be the contract's own vocabulary: a glossary that has to define
#: `budget_tokens` is a page that should not have printed it.
GLOSSARY = (
    ("Step", "One piece of the work, with its own list of files and its own "
             "checks. Steps are numbered in the order they can start."),
    ("Round", "A group of steps that all happen at the same time. The next "
              "round begins only when every step in this one has finished."),
    ("At the same time", "Two steps that run together, on different files, so "
                         "the whole job finishes sooner."),
    ("Token", "Roughly three quarters of a word of reading or writing. The "
              "totals on this page are a rough guess at how much reading and "
              "writing the work will take. It is not a price and not a "
              "promise."),
    ("Checks", "Automatic tests and human sign-offs that have to pass before "
               "a step counts as finished."),
    ("Technical detail", "The switch at the top of this page. It reveals the "
                         "file names, the checks and the exact wording each "
                         "step was given. Nothing is hidden from you; it is "
                         "folded away because most readers do not need it."),
)

# Anything that would make the file depend on a network, a server, or a second
# file sitting next to it. `check()` matches these against `Regions.surface` —
# tags, their attributes, and the body of anything the browser executes — and
# not against prose. A plan whose contracts say "no `https://` in the output"
# is describing this rule, not breaking it, and a page that failed its own
# check for quoting it would be a checker nobody could leave switched on.
_EXTERNAL = tuple(re.compile(pattern, re.I) for pattern in (
    r"https?:",
    r"//cdn",
    r"<link\b",
    r"<iframe\b",
    r"<script[^>]*\bsrc\s*=",
    r"\bfetch\s*\(",
    r"XMLHttpRequest",
    r"@import\b",
    r"\burl\s*\(",
))


# --------------------------------------------------------------------------- #
# small words
# --------------------------------------------------------------------------- #

def _int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _thousands(number):
    return "{:,}".format(_int(number))


def _plural(count, singular, plural=None):
    word = singular if count == 1 else (plural or singular + "s")
    return "%d %s" % (count, word)


def _listed(words):
    """`['a', 'b', 'c']` -> `'a, b and c'`. An Oxford comma is not the house
    style of a page read aloud."""
    words = list(words)
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return "%s and %s" % (", ".join(words[:-1]), words[-1])


def _steps_phrase(numbers):
    """`[2]` -> `step 2`; `[2, 3]` -> `steps 2 and 3`."""
    numbers = list(numbers)
    if not numbers:
        return ""
    label = "step" if len(numbers) == 1 else "steps"
    return "%s %s" % (label, _listed([str(_int(n)) for n in numbers]))


def _sentence_case(text):
    text = str(text or "").strip()
    return text[:1].upper() + text[1:]


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #

def _prose(html, empty="Nobody has written this down yet."):
    """Block HTML from the model, or a plain admission that there is none."""
    text = str(html or "").strip()
    if text:
        return text
    return '<p class="note">%s</p>' % _escape(empty)


def _paths(items, kind):
    if not items:
        return "<p>none</p>"
    return "<ul>%s</ul>" % "".join(
        '<li class="%s">%s</li>' % (kind, _escape(path)) for path in items)


def _row(cells, tag="td"):
    return "<tr>%s</tr>" % "".join(
        "<%s>%s</%s>" % (tag, cell, tag) for cell in cells)


def _table(headings, rows):
    """A table, in the one kind of container allowed to scroll sideways.

    Headings are literals from this module and are escaped here so that no
    call site has to remember to; the rows are assembled by their callers,
    which is where the prose/structure distinction actually lives.
    """
    return (
        '<div class="scroll">\n<table>\n<thead>%s</thead>\n<tbody>%s</tbody>'
        "\n</table>\n</div>"
        % (_row([_escape(heading) for heading in headings], "th"),
           "".join(rows))
    )


def _tech(title, body):
    return ('<div class="tech">\n<h4>%s</h4>\n%s\n</div>'
            % (_escape(title), body))


# --------------------------------------------------------------------------- #
# the diagram
# --------------------------------------------------------------------------- #

_LEFT, _GAP, _TOP, _DROP, _RADIUS = 104, 96, 38, 84, 20


def _diagram(vm):
    """The rounds and their dependencies as inline SVG.

    Rounds are rows, top to bottom; a line from one step to another means the
    lower one cannot start until the upper one is done. Every coordinate is an
    integer — a float would render differently under a different locale or a
    different Python, and this file is committed.

    No `xmlns`: inline SVG in an HTML document does not need it, and it is a
    URL, which this page does not contain. Deliberately not the only
    explanation of the schedule — the rounds are written out in words directly
    above it, because a diagram is not readable by everybody.
    """
    graph = vm.get("graph") or {}
    nodes = list(graph.get("nodes") or [])
    if not nodes:
        return ""

    rounds = {}
    for node in nodes:
        rounds.setdefault(_int(node.get("round")), []).append(node)
    levels = sorted(rounds)
    widest = max(len(rounds[level]) for level in levels)

    centre = {}
    for row, level in enumerate(levels):
        members = sorted(rounds[level], key=lambda node: _int(node.get("number")))
        for column, node in enumerate(members):
            centre[_int(node.get("number"))] = (
                _LEFT + column * _GAP, _TOP + row * _DROP)

    width = _LEFT + (widest - 1) * _GAP + _RADIUS + 24
    height = _TOP + (len(levels) - 1) * _DROP + _RADIUS + 24

    parts = []
    for edge in graph.get("edges") or []:
        start = centre.get(_int(edge.get("from")))
        end = centre.get(_int(edge.get("to")))
        if not start or not end:
            continue
        parts.append('<line class="edge" x1="%d" y1="%d" x2="%d" y2="%d" />'
                     % (start[0], start[1] + _RADIUS, end[0], end[1] - _RADIUS))
    for row in range(len(levels)):
        parts.append('<text class="round-label" x="8" y="%d">Round %d</text>'
                     % (_TOP + row * _DROP + 5, row + 1))
    for number in sorted(centre):
        x, y = centre[number]
        parts.append('<circle class="node" cx="%d" cy="%d" r="%d" />'
                     % (x, y, _RADIUS))
        parts.append('<text class="number" x="%d" y="%d" text-anchor="middle">'
                     "%d</text>" % (x, y + 5, number))

    label = ("The %s of this plan, grouped into %s. Steps drawn side by side "
             "happen at the same time; a line means the lower step waits for "
             "the upper one."
             % (_plural(len(nodes), "step"), _plural(len(levels), "round")))
    return (
        '<div class="scroll">\n'
        '<svg viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
        'aria-label="%s">\n<title>%s</title>\n%s\n</svg>\n</div>'
        % (width, height, width, height, _attr(label), _escape(label),
           "\n".join(parts))
    )


# --------------------------------------------------------------------------- #
# the page, section by section
# --------------------------------------------------------------------------- #

def _banners(vm):
    """What the reader must be told about the page itself, before they read it.

    Degraded is not broken: a plan nobody has written a summary for still
    renders every step, and says plainly that the words below were assembled
    rather than written.
    """
    plain = vm.get("plain") or {}
    out = []
    if not plain.get("present"):
        out.append(
            "The plain-language summary for this plan has not been written "
            "yet. Everything below was put together automatically from the "
            "plan itself, so it describes what will happen but not why it "
            "matters. Ask for a summary before you approve anything you are "
            "not sure about.")
    elif plain.get("stale"):
        out.append(
            "The plan has changed since this summary was written, so parts of "
            "it may be out of date.")
    missing = list(plain.get("missing") or [])
    if plain.get("present") and missing:
        out.append(
            "%s no description written for %s, so what you see for %s was put "
            "together automatically."
            % (_plural(len(missing), "step"),
               "it" if len(missing) == 1 else "them",
               "it" if len(missing) == 1 else "them"))
    return "\n".join('<p class="banner">%s</p>' % _escape(text) for text in out)


def _header(vm):
    plan = vm.get("plan") or {}
    counts = plan.get("counts") or {}
    critical = vm.get("critical_path") or {}

    facts = [_plural(_int(counts.get("units")), "step"),
             _plural(_int(counts.get("waves")), "round")]
    estimate = _int(critical.get("estimate_tokens"))
    if estimate:
        facts.append("about %s tokens of work" % _thousands(estimate))

    basis = str(critical.get("basis") or "").strip()
    note = '<p class="note">%s</p>' % basis if basis else ""
    return (
        '<header class="head">\n'
        '<label class="toggle" for="tech-toggle">Technical detail</label>\n'
        "<h1>%s</h1>\n"
        '<p class="counts">%s</p>\n%s\n%s\n</header>'
        % (plan.get("title") or _escape(plan.get("slug") or "this plan"),
           _escape(" · ".join(facts)), note, _banners(vm))
    )


def _section(key, heading, vm):
    body = (vm.get("plain") or {}).get("sections") or {}
    return '<section id="%s">\n<h2>%s</h2>\n%s\n</section>' % (
        _attr(key.replace(" ", "-")), _escape(heading),
        _prose(body.get(key), "Nobody has written this part down yet."))


def _extra_sections(vm):
    """Any plan section the model carries that `SECTIONS` does not name.

    `plain.py` growing a sixth section must reach the page: prose a human
    wrote and the page silently dropped is exactly the failure a reviewer
    cannot detect by reading. It lands under its own heading rather than being
    forced into one of the five.
    """
    known = set(key for key, _heading in SECTIONS)
    body = (vm.get("plain") or {}).get("sections") or {}
    out = []
    for key in sorted(body):
        if key in known or not str(body[key] or "").strip():
            continue
        out.append('<section id="%s">\n<h2>%s</h2>\n%s\n</section>'
                   % (_attr(key.replace(" ", "-")),
                      _escape(_sentence_case(key)), body[key]))
    return "\n".join(out)


def _rounds(vm):
    """Each round in words, before any diagram: who runs together, and after
    what."""
    steps = vm.get("steps") or []
    titles = dict((_int(step.get("number")), step.get("title") or "")
                  for step in steps)
    rounds = {}
    for step in steps:
        rounds.setdefault(_int(step.get("round")), []).append(
            _int(step.get("number")))

    out = []
    for position, level in enumerate(sorted(rounds), start=1):
        members = sorted(rounds[level])
        if len(members) > 1:
            sentence = "%s run at the same time." % _sentence_case(
                _steps_phrase(members))
        else:
            sentence = "%s runs on its own." % _sentence_case(
                _steps_phrase(members))
        if position > 1:
            sentence += (" It begins once round %d has finished."
                         % (position - 1))
        items = "".join(
            '<li><a href="#step-%d">Step %d</a> — %s</li>'
            % (number, number, titles.get(number, "")) for number in members)
        out.append('<div class="round">\n<h3>Round %d</h3>\n<p>%s</p>\n'
                   "<ul>%s</ul>\n</div>" % (position, _escape(sentence), items))
    return "\n".join(out)


def _ownership_table(vm):
    entries = vm.get("ownership") or []
    if not entries:
        return ""
    rows = []
    for entry in entries:
        numbers = [_int(number) for number in entry.get("steps") or []]
        if entry.get("contested"):
            note = ("two steps in the same round declare it — they cannot run "
                    "together")
        elif entry.get("bottleneck"):
            note = "declared by more than one step, in different rounds"
        else:
            note = "one step only"
        rows.append(
            '<tr><td class="path">%s</td><td>%s</td><td>%s</td></tr>'
            % (_escape(entry.get("path") or ""),
               _escape(_steps_phrase(numbers) or "nobody"), _escape(note)))
    return _tech("Which step declares which file",
                 _table(["File", "Declared by", "Note"], rows))


def _split_up(vm):
    steps = vm.get("steps") or []
    counts = (vm.get("plan") or {}).get("counts") or {}
    if not steps:
        return ('<section id="split-up">\n<h2>How the work is split up</h2>\n'
                "<p>This plan has no steps yet, so there is nothing to split "
                "up. Nobody has written down what the work is.</p>\n</section>")

    rounds = _int(counts.get("waves"))
    lead = ("The work is split into %s. They run in %s: everything in a round "
            "happens at the same time, and a round starts only when the one "
            "before it has finished."
            % (_plural(len(steps), "step"), _plural(rounds, "round")))

    bottlenecks = vm.get("bottlenecks") or []
    shared = ""
    if bottlenecks:
        shared = ("<p>%s needed by more than one step. That limits how much "
                  "can happen at the same time — the details are under "
                  "Technical detail.</p>"
                  % _escape(_sentence_case(
                      "%s is" % _plural(len(bottlenecks), "file")
                      if len(bottlenecks) == 1
                      else "%s are" % _plural(len(bottlenecks), "file"))))

    return ('<section id="split-up">\n<h2>How the work is split up</h2>\n'
            "<p>%s</p>\n%s\n%s\n%s\n%s\n</section>"
            % (_escape(lead), _rounds(vm), _diagram(vm), shared,
               _ownership_table(vm)))


def _when(step):
    """A step's concurrency in words. Never "wave 1" — the reader has no such
    word, and the whole point of the page is that they do not need one."""
    alongside = [_int(number) for number in step.get("alongside") or []]
    waits = [_int(number) for number in step.get("waits_for") or []]
    parts = []
    if alongside:
        parts.append("Happens at the same time as %s." % _steps_phrase(alongside))
    else:
        parts.append("Nothing else runs at the same time as this step.")
    if waits:
        parts.append("Waits for %s to finish first." % _steps_phrase(waits))
    elif _int(step.get("round")) > 1:
        parts.append("It depends on no single earlier step, but it starts "
                     "only once round %d has finished."
                     % (_int(step.get("round")) - 1))
    else:
        parts.append("Nothing has to finish before it starts.")
    return " ".join(parts)


def _step_tech(step):
    tech = step.get("tech") or {}
    number = _int(step.get("number"))

    criteria = tech.get("criteria") or []
    if criteria:
        listed = "<ol>%s</ol>" % "".join(
            '<li class="criterion" data-step="%d">%s</li>' % (number, item)
            for item in criteria)
    else:
        listed = "<p>none stated</p>"

    verify = tech.get("verify") or []
    if verify:
        checks = "<ul>%s</ul>" % "".join(
            "<li><code>%s</code> — %s</li>"
            % (_escape(check.get("kind") or ""), _escape(check.get("label") or ""))
            for check in verify)
    else:
        checks = "<p>none</p>"

    facts = [
        ("unit", "<code>%s</code>" % _escape(step.get("slug") or "")),
        ("tier", _escape(tech.get("tier") or "")),
        ("status", _escape(tech.get("status") or "")),
        ("budget_tokens", _escape(_thousands(tech.get("budget_tokens")))),
        ("wave", _escape(str(_int(step.get("round"))))),
    ]
    model = str(tech.get("model") or "").strip()
    if model:
        facts.append(("model", _escape(model)))
    rows = "".join("<dt>%s</dt><dd>%s</dd>" % (_escape(key), value)
                   for key, value in facts)

    depends = tech.get("depends_on") or []
    rows += "<dt>depends_on</dt><dd>%s</dd>" % (
        "<ul>%s</ul>" % "".join("<li><code>%s</code></li>" % _escape(name)
                                for name in depends)
        if depends else "none")
    rows += "<dt>owns</dt><dd>%s</dd>" % _paths(tech.get("owns"), "owns")
    rows += "<dt>reads</dt><dd>%s</dd>" % _paths(tech.get("reads"), "reads")
    rows += "<dt>forbid</dt><dd>%s</dd>" % _paths(tech.get("forbid"), "forbid")

    return _tech(
        "Technical detail",
        '<dl class="kv">%s</dl>\n<h4>acceptance criteria</h4>\n%s\n'
        "<h4>verify</h4>\n%s" % (rows, listed, checks))


def _step_card(step):
    number = _int(step.get("number"))
    plain = step.get("plain") or {}
    generated = plain.get("generated") or {}

    fields = []
    for key, label in FIELDS:
        mark = ('<span class="auto">put together automatically</span>'
                if generated.get(key) else "")
        fields.append('<div class="field">\n<h4>%s %s</h4>\n%s\n</div>'
                      % (_escape(label), mark, _prose(plain.get(key))))

    return ('<section class="step" id="step-%d">\n'
            "<h3>Step %d — %s</h3>\n"
            '<p class="when">%s</p>\n%s\n%s\n</section>'
            % (number, number, step.get("title") or "", _escape(_when(step)),
               "\n".join(fields), _step_tech(step)))


def _steps(vm):
    steps = vm.get("steps") or []
    if not steps:
        return ('<section id="steps">\n<h2>Each step, in detail</h2>\n'
                "<p>There are no steps to describe yet.</p>\n</section>")
    return ('<section id="steps">\n<h2>Each step, in detail</h2>\n%s\n'
            "</section>" % "\n".join(_step_card(step) for step in steps))


def _risks(vm):
    """The authored warning, then one line per step, then the gaps nobody
    claimed."""
    sections = (vm.get("plain") or {}).get("sections") or {}
    steps = vm.get("steps") or []

    per_step = ""
    if steps:
        per_step = (
            "<h3>What could go wrong in each step</h3>\n<ul>%s</ul>"
            % "".join(
                "<li><strong>Step %d — %s</strong> %s</li>"
                % (_int(step.get("number")), step.get("title") or "",
                   _prose((step.get("plain") or {}).get("risk")))
                for step in steps))

    gaps = vm.get("ownership_gaps") or {}
    files = list(gaps.get("files") or [])
    unclaimed = ""
    if files:
        sentence = ("%s that cover this work %s claimed by any step, so a step "
                    "could be held up by a file it is not allowed to change."
                    % (_plural(len(files), "test file"),
                       "is not" if len(files) == 1 else "are not"))
        rows = "".join(
            '<tr><td class="path">%s</td><td>%s</td></tr>'
            % (_escape(entry.get("path") or ""),
               _escape(", ".join(entry.get("tests") or [])))
            for entry in files)
        unclaimed = "<p>%s</p>\n%s" % (
            _escape(_sentence_case(sentence)),
            _tech("Tests nobody in this plan declares",
                  _table(["Source file", "Tests that name it"], rows)))

    return ('<section id="what-could-go-wrong">\n<h2>What could go wrong</h2>\n'
            "%s\n%s\n%s\n</section>"
            % (_prose(sections.get("what could go wrong"),
                      "Nobody has written down what could go wrong."),
               unclaimed, per_step))


def _how_we_check(vm):
    steps = vm.get("steps") or []
    if not steps:
        body = "<p>There are no steps, so there is nothing to check yet.</p>"
    else:
        rows = "".join(
            "<tr><td>Step %d — %s</td><td>%s</td></tr>"
            % (_int(step.get("number")), step.get("title") or "",
               _prose((step.get("plain") or {}).get("how_we_know")))
            for step in steps)
        verify_rows = "".join(
            "<tr><td>Step %d</td><td><code>%s</code></td><td>%s</td></tr>"
            % (_int(step.get("number")), _escape(check.get("kind") or ""),
               _escape(check.get("label") or ""))
            for step in steps
            for check in (step.get("tech") or {}).get("verify") or [])
        body = "%s\n%s" % (
            _table(["Step", "How we'll know it worked"], [rows]),
            _tech("The checks each step has to pass",
                  _table(["Step", "Kind", "What it asserts"], [verify_rows])))

    return ('<section id="how-we-check">\n<h2>%s</h2>\n'
            "<p>No step counts as finished until its own checks have passed. "
            "Nobody marks their own homework: the checks below run whether or "
            "not anybody remembers to ask for them.</p>\n%s\n</section>"
            % (_escape("How we'll check it worked"), body))


def _glossary():
    items = "".join("<dt>%s</dt><dd>%s</dd>" % (_escape(term), _escape(meaning))
                    for term, meaning in GLOSSARY)
    return ('<section id="glossary">\n<h2>What the words mean</h2>\n'
            '<dl class="kv">%s</dl>\n</section>' % items)


def _footer(vm):
    plan = vm.get("plan") or {}
    facts = [
        "plan %s" % (plan.get("slug") or ""),
        "spec %s" % (plan.get("spec") or ""),
        "revision %d" % _int(plan.get("revision")),
        "graph generated %s" % (plan.get("generated") or "unknown"),
        "summary digest %s" % ((plan.get("digest") or "none")[:12] or "none"),
        "view-model schema %d" % _int(vm.get("schema")),
    ]
    return ("<footer>\n"
            "<p>This page was made from the plan itself. It is a plan, not a "
            "record of what happened.</p>\n"
            '<p class="tech">%s</p>\n</footer>' % _escape(" · ".join(facts)))


# --------------------------------------------------------------------------- #
# the stylesheet
# --------------------------------------------------------------------------- #
#
# Inline, because the page has to work with nothing beside it. Three things
# here are requirements rather than taste:
#
#   * both themes set a background *and* a foreground — a body that sets only
#     one borrows the other from the host and can land on black-on-black;
#   * `.scroll` is the only rule in the file with `overflow-x`, so a table or
#     the diagram can scroll sideways and the page itself never does;
#   * `@media print` reveals `.tech` and hides the switch, so `Cmd-P` produces
#     the whole document rather than the half somebody happened to be looking
#     at.

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 1.5rem 1rem 4rem; max-width: 44rem;
  background: #ffffff;
  color: #16181b;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  font-size: 17px; line-height: 1.6; overflow-wrap: break-word;
}
h1 { font-size: 1.7rem; line-height: 1.25; margin: 0 0 .4rem; }
h2 {
  font-size: 1.25rem; margin: 2.6rem 0 .6rem; padding-bottom: .3rem;
  border-bottom: 2px solid #d7dbe0;
}
h3 { font-size: 1.05rem; margin: 1.4rem 0 .3rem; }
h4 {
  font-size: .78rem; letter-spacing: .05em; text-transform: uppercase;
  color: #52585f; margin: .9rem 0 .2rem;
}
p, ul, ol, dl { margin: .5rem 0; }
ul, ol { padding-left: 1.3rem; }
li { margin: .15rem 0; }
a { color: #0a4fb4; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .88em;
}
pre {
  white-space: pre-wrap; word-break: break-word; background: #f3f5f7;
  padding: .6rem .8rem; border-radius: 6px;
}
.counts { font-size: 1.05rem; margin: 0; font-weight: 600; }
.note { color: #52585f; font-size: .93rem; }
.banner {
  border: 1px solid #9a6208; border-left-width: 6px; background: #fdf3e3;
  color: #16181b; padding: .7rem .9rem; border-radius: 6px; margin: 1rem 0;
}
.head { display: flex; flex-direction: column; }
.toggle {
  align-self: flex-end; border: 1px solid #5c636b; border-radius: 999px;
  padding: .2rem .8rem; font-size: .85rem; cursor: pointer;
  user-select: none; -webkit-user-select: none; margin-bottom: .6rem;
}
.toggle-input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.round { margin: .8rem 0; }
.step {
  border: 1px solid #d7dbe0; border-radius: 8px; padding: .2rem 1rem 1rem;
  margin: 1.2rem 0; background: #fafbfc;
}
.when { font-weight: 600; margin: .4rem 0 .6rem; }
.field { margin: .6rem 0; }
.auto {
  font-weight: 400; text-transform: none; letter-spacing: 0; color: #6a7078;
}
.scroll { overflow-x: auto; margin: 1rem 0; }
table {
  border-collapse: collapse; width: 100%; min-width: 20rem; font-size: .92rem;
}
th, td {
  border: 1px solid #d7dbe0; padding: .35rem .5rem; text-align: left;
  vertical-align: top;
}
th { background: #f3f5f7; }
td p { margin: .2rem 0; }
svg { display: block; }
.node { fill: #ffffff; stroke: #5c636b; stroke-width: 2; }
.edge { stroke: #6f767f; stroke-width: 2; }
.number { fill: #16181b; font-size: 15px; font-weight: 600; }
.round-label { fill: #52585f; font-size: 13px; }
.kv dt { font-weight: 600; margin-top: .4rem; }
.kv dd { margin: 0 0 .2rem 1rem; }
.tech { display: none; }
.tech {
  border-left: 3px solid #b6bcc4; padding-left: .8rem; margin: .9rem 0;
}
#tech-toggle:checked ~ .head .tech,
#tech-toggle:checked ~ main .tech,
#tech-toggle:checked ~ footer .tech { display: block; }
#tech-toggle:checked ~ .head .toggle { background: #16181b; color: #ffffff; }
#tech-toggle:focus ~ .head .toggle { outline: 3px solid #0a4fb4; }
@media (prefers-color-scheme: dark) {
  body { background: #15181c; color: #e6e9ed; }
  h2 { border-bottom-color: #343a42; }
  h4, .note, .round-label { color: #aeb6c0; }
  a { color: #8ab4ff; }
  pre, th { background: #1b1f24; }
  th, td { border-color: #343a42; }
  .banner { background: #2a2114; border-color: #d3a24a; color: #f4ead9; }
  .step { background: #1b1f24; border-color: #343a42; }
  .auto { color: #a2aab4; }
  .toggle { border-color: #aeb6c0; }
  .tech { border-left-color: #4a515a; }
  .node { fill: #1b1f24; stroke: #aeb6c0; }
  .edge { stroke: #8f97a1; }
  .number { fill: #e6e9ed; }
  .round-label { fill: #aeb6c0; }
  #tech-toggle:checked ~ .head .toggle { background: #e6e9ed; color: #15181c; }
}
@media print {
  body { background: #ffffff; color: #000000; max-width: none; font-size: 10.5pt; padding: 0; }
  .tech { display: block !important; border-left: 1px solid #999999; }
  .toggle, .toggle-input { display: none !important; }
  .scroll { overflow: visible; }
  .step, .banner, .round { break-inside: avoid; page-break-inside: avoid; }
  h2, h3 { break-after: avoid; page-break-after: avoid; }
  a { color: #000000; text-decoration: none; }
  svg { max-width: 100%; height: auto; }
}
""".strip()


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #

def render(vm):
    """The whole page for one view-model, as a complete HTML document.

    Deterministic: same model in, same bytes out. Nothing here reads a clock,
    iterates an unordered mapping without sorting it, or depends on who is
    running it.
    """
    vm = vm or {}
    plan = vm.get("plan") or {}
    sections = [_section(key, heading, vm) for key, heading in SECTIONS[:3]]

    document = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Plan preview: %s</title>" % _escape(plan.get("slug") or ""),
        "<style>\n%s\n</style>" % CSS,
        "</head>",
        "<body>",
        '<input type="checkbox" id="tech-toggle" class="toggle-input">',
        _header(vm),
        "<main>",
        "\n".join(sections),
        _split_up(vm),
        _steps(vm),
        _risks(vm),
        _section("out of scope", "What we are not doing", vm),
        _how_we_check(vm),
        _extra_sections(vm),
        _glossary(),
        "</main>",
        _footer(vm),
        '<script type="application/json" id="view-model">%s</script>'
        % preview_html.embed_json(vm),
        "</body>",
        "</html>",
    ]
    return "\n".join(part for part in document if part) + "\n"


def write(layout, slug):
    """Render the plan's preview page beside the plan, atomically.

    Returns the path. The file is committed, so it goes through
    `atomic.write_text` — a `Path.write_text` that dies half way leaves a torn
    document in somebody's diff.
    """
    return atomic.write_text(preview.html_path(layout, slug),
                             render(preview.view_model(layout, slug)))


# --------------------------------------------------------------------------- #
# reading a rendered page back
# --------------------------------------------------------------------------- #

_VOID = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
))
# Their contents are code and data, not something a reader reads.
_OPAQUE = frozenset(("script", "style"))


class Regions(object):
    """A rendered page, split into what the reader sees and what is folded away.

    `plain_text` is the text of every element *not* inside a `class="tech"`
    element; `tech_text` is the rest. The split is the only honest way to
    assert that the default view speaks the reader's language: the contract's
    vocabulary is legitimately in the document — the technical view is made of
    it — so searching the whole file for `owns` would pass however the page
    read.

    `criteria` counts the rendered acceptance criteria per step number, which
    is how `check()` catches a page that shows three of a step's four.

    `surface` is every tag with its attributes, plus the body of any
    `<script>` or `<style>` the browser would act on — in other words, the
    only places a page can reach out from. Prose is deliberately *not* in it:
    a unit contract that says "no `https://` in the output" would otherwise
    make every page rendered from that plan fail its own external-reference
    check, and a URL sitting in a text node fetches nothing. The JSON island
    is exempt for the same reason and no other: it is data the browser parses
    on request, not code it runs.
    """

    def __init__(self, plain_text, tech_text, criteria, surface):
        self.plain_text = plain_text
        self.tech_text = tech_text
        self.criteria = criteria
        self.surface = surface


class _Reader(HTMLParser):

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.plain = []
        self.tech = []
        self.criteria = {}
        self.surface = []
        self._stack = []   # [(tag, inside_tech)]
        self._opaque = 0
        self._inert = False

    # -- helpers ---------------------------------------------------------- #

    @staticmethod
    def _attrs(attrs):
        return dict((name, value or "") for name, value in attrs)

    def _inside_tech(self):
        return bool(self._stack) and self._stack[-1][1]

    def _note(self, attrs):
        found = self._attrs(attrs)
        classes = found.get("class", "").split()
        if "criterion" in classes and "data-step" in found:
            key = _int(found["data-step"], None)
            if key is not None:
                self.criteria[key] = self.criteria.get(key, 0) + 1
        return "tech" in classes

    # -- the parser's own interface --------------------------------------- #

    def handle_startendtag(self, tag, attrs):
        self._note(attrs)
        self.surface.append(self.get_starttag_text() or "")

    def handle_starttag(self, tag, attrs):
        tech = self._note(attrs) or self._inside_tech()
        self.surface.append(self.get_starttag_text() or "")
        if tag in _VOID:
            return
        if tag in _OPAQUE:
            self._opaque += 1
            self._inert = (tag == "script"
                           and self._attrs(attrs).get("type") == "application/json")
        self._stack.append((tag, tech))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] != tag:
                continue
            for name, _tech in self._stack[index:]:
                if name in _OPAQUE:
                    self._opaque = max(0, self._opaque - 1)
                    self._inert = False
            del self._stack[index:]
            return

    def handle_data(self, data):
        if self._opaque:
            if not self._inert:
                self.surface.append(data)
            return
        if not data.strip():
            return
        (self.tech if self._inside_tech() else self.plain).append(data.strip())


def regions(html):
    """Parse a rendered page into its plain and technical halves."""
    reader = _Reader()
    try:
        reader.feed(str(html or ""))
        reader.close()
    except Exception:  # pragma: no cover - html.parser is forgiving by design
        pass
    return Regions("\n".join(reader.plain), "\n".join(reader.tech),
                   reader.criteria, "\n".join(reader.surface))


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def check(html, vm):
    """`[]` when `html` covers `vm` faithfully, one line per thing it lost.

    Five kinds of loss, and they are the five that a reader cannot detect by
    reading — a page missing a step looks exactly like a plan with one fewer
    step. Each has a rejection test *and* a positive control in
    `tests/test_preview_page.py`, and `render`'s own output has to return `[]`
    here, which is what makes this a checker rather than a comment.
    """
    vm = vm or {}
    html = str(html or "")
    read = regions(html)
    page = read.plain_text + "\n" + read.tech_text
    problems = []

    for step in vm.get("steps") or []:
        number = _int(step.get("number"))
        slug = str(step.get("slug") or "")
        tech = step.get("tech") or {}

        if slug and slug not in page:
            problems.append(
                "step %d (%s) is not named anywhere on the page" % (number, slug))

        for path in tech.get("owns") or []:
            if path not in read.tech_text:
                problems.append(
                    "step %d: the file it declares, %s, is missing from the "
                    "technical view" % (number, path))

        stated = len(tech.get("criteria") or [])
        shown = read.criteria.get(number, 0)
        if shown != stated:
            problems.append(
                "step %d: the page shows %d acceptance criteria, the plan "
                "states %d" % (number, shown, stated))

        plain = step.get("plain") or {}
        for key in sorted(plain):
            if key == "generated":
                continue
            value = plain[key]
            if value and value not in html:
                problems.append(
                    "step %d: the plain-language %s is missing from the page"
                    % (number, key))

    sections = (vm.get("plain") or {}).get("sections") or {}
    for name in sorted(sections):
        if sections[name] and sections[name] not in html:
            problems.append(
                "the %r part of the plain-language summary is missing from "
                "the page" % name)

    for pattern in _EXTERNAL:
        if pattern.search(read.surface):
            problems.append(
                "the page refers to something outside itself, matching %s — it "
                "has to work from a file with no network"
                % pattern.pattern)

    return problems
