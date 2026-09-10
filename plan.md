# Plan — Plan Preview: a shareable HTML page a non-technical reviewer can approve

**Status:** proposed, not yet dispatched
**Target version:** 0.9.0
**Written:** 2026-09-10

---

## 1. The problem

Today a `ctx` plan exists as `plan.json`, a terse `README.md`, and a set of unit
contracts written for an engineer or a subagent. There is no artefact a person
can look at and say "yes, do that" before work starts.

Claude normally solves this with an artifact — but an artifact needs a
claude.ai account to open. The people who most need to approve a plan often do
not have one, and may not be technical at all.

Context Ledger's actual job is to run work in parallel and organise it so
somebody who is not writing prompts by hand can still direct it. That job is
unfinished if the only readable description of the work is a file full of
`owns:` globs and `_check_cmd` internals.

**So:** every plan gets its own self-contained HTML file, written in plain
language, that opens by double-clicking it — no account, no server, no network.

---

## 2. What we are building

`ctx plan-check` already writes `plan.json` and re-renders `README.md`. It will
now also write:

```
.ctx/plans/<slug>/
  plan.json          structure, for machines
  README.md          the board, for the orchestrator
  units/*.md         contracts, for engineers and subagents
  plain.md           NEW — plain language, for humans
  preview.html       NEW — the generated page, committed and shareable
  preview.data.json  NEW — the view-model, only written by `--design`
  revisions/
```

`preview.html` is committed. That is deliberate: the whole point is to hand it
to somebody, and a file that vanishes on a fresh clone cannot be handed to
anybody. It is also reviewable in a pull request alongside the plan it describes.

### Non-goals

- Nothing blocks. `ctx start` prints one advisory line and dispatches as it
  always has. No new gate, no approval state machine.
- The page is read-only. No checkboxes, no verdicts, no round-trip protocol.
- No server, no CDN, no fonts, no `fetch`. It must open from `file://` on a
  laptop with the wifi off.

---

## 3. The one rule that makes this safe

**The HTML never sees the plan. It sees a view-model.**

```
plan.json ─┐
units/*.md ─┼─► view_model()  ──►  dict, JSON-safe, the single source of truth
plain.md  ─┘                        │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
             render() → preview.html      preview.data.json + design brief
             (deterministic, tested)      (the /ctx:preview --design path)
```

Both the built-in renderer and any bespoke design I hand-write later consume
the same dict. A re-skin can change every pixel and still cannot invent, drop
or reword a unit's facts.

Three properties hold for every generated page:

1. **Escape first, mark up second.** Unit prose is text that can arrive from a
   shared repository, and the output is a file somebody double-clicks. Every
   string is HTML-escaped, and only then is a deliberately tiny markdown subset
   applied to the already-escaped text. The embedded JSON is `</script>`-safe.
2. **Redaction on the write path.** `ctx/redact.py` scrubs all prose before it
   reaches disk, for the same reason context bundles do: this file is committed
   and shared, and a secret that reaches disk has already leaked.
3. **Byte-deterministic.** No wall-clock timestamp, no random ids, sorted keys.
   Same plan revision in, identical file out — otherwise every `plan-check`
   produces a spurious diff on a committed artefact.

---

## 4. Writing for the reviewer

This is the part that decides whether the feature is worth anything.

### 4.1 What the page says, in order

| Section | Answers |
|---|---|
| Heading | What is this plan called, in words, not a slug |
| What we're going to do | 3–5 sentences of narrative |
| Why we're doing it | The problem, stated without jargon |
| What will be different afterwards | Observable outcomes, not file changes |
| How the work is split up | The steps, and **which ones happen at the same time** |
| Each step | What it does · why it matters · what changes · risk · how we'll know it worked |
| What could go wrong | Risks in plain words |
| What we are *not* doing | Out of scope, so silence is not mistaken for a promise |
| How we'll check it worked | Verification, described not commanded |

The parallelism story gets real estate, because that is the product. A reviewer
should be able to see "steps 1 and 2 happen together, step 3 waits for both"
without knowing what a wave is.

### 4.2 Language rules the renderer enforces

These are not style suggestions; unit `05` tests them.

- **No unexplained jargon in the default view.** `owns`, `forbid`,
  `depends_on`, `wave`, `tier`, `budget_tokens`, `subagent` and file paths all
  live behind the technical toggle. The default view says "runs at the same
  time as Step 2", not "wave 1".
- **Steps are numbered from 1, not from `01-kebab-name`.** The slug appears
  only under the toggle.
- **Every stated risk names a consequence**, not a severity word alone.
- **A glossary** covers the handful of terms that cannot be avoided.

### 4.3 Where the plain words come from

A Python renderer can translate *structure* ("this step changes four files and
waits for two others"). It cannot translate *substance* — nothing mechanical
turns "`_check_cmd` calls `_missing_tool(output)` on the combined stdout+stderr"
into "we're fixing a safety check that sometimes hides real breakages".

So substance is authored, into `plain.md`, at planning time. Format:

```markdown
---
ctx_schema: 1
plan: close-the-wave-1-audit-blockers
revision: 4
---

## Summary
We are fixing seven safety problems we found in our own checking system...

## Why now
...

## What changes for you
...

## What could go wrong
...

## Out of scope
...

## Unit: 01-verify-kinds
**What it does:** Makes the safety check report a real breakage as a real
breakage, instead of quietly filing it as a setup problem and moving on.
**Why it matters:** Right now the one failure the check exists to catch is the
one it can miss.
**What changes:** The single file that decides whether work passed or failed.
**Risk:** Low — it makes an existing check stricter; nothing new runs.
**How we'll know:** The test suite passes, including new tests that deliberately
break something and confirm the check now notices.
```

`frontmatter.Document.sections()` already splits on `##`, so this parses with
code that exists. `revision:` is compared against `plan.json` — a mismatch marks
those entries "may be out of date" on the page and is reported by
`ctx preview --check`.

**When `plain.md` is missing or an entry is absent, the page still renders.**
It falls back to generated structural sentences and a visible banner saying the
plain-language summary has not been written yet. This is what lets `plan-check`
write a preview with no agent in the loop, and it means the feature degrades to
"less useful" rather than "broken".

### 4.4 Baseline layout

Plain by default; one **Technical detail** switch reveals paths, `owns`/`forbid`,
dependencies, acceptance criteria verbatim, verify commands, tier, model and
token budget — in place, next to the plain text, not on a separate page.

```
┌──────────────────────────────────────── [ ● Technical detail ] ┐
│  Closing seven safety gaps in our checking system              │
│  7 steps · 5 rounds · about 390k tokens of work                │
├────────────────────────────────────────────────────────────────┤
│  What we're going to do                                        │
│  ...                                                           │
│                                                                │
│  How the work is split up                                      │
│   ┌ Round 1 ── Step 1 ─┬─ Step 2 ─┐   these happen together    │
│   └ Round 2 ─────────── Step 3 ───┘   waits for both           │
│                                                                │
│  Step 1 — Make the safety check honest            ● low risk   │
│  Right now a real break can be reported as a setup problem     │
│  and quietly ignored. We make it report the break.             │
│  ▸ Happens at the same time as Step 2                          │
│  ▸ owns ctx/verify.py, tests/test_gates.py         ← toggled   │
└────────────────────────────────────────────────────────────────┘
```

Plus: wave timeline as the spine, dependency graph as inline SVG, a file
ownership map showing overlaps and `forbid` boundaries (technical view),
search/filter, expand-collapse, a print stylesheet that produces a clean PDF,
dark mode via `prefers-color-scheme`, and readable down to 400px.

---

## 5. The `--design` path — a bespoke page per plan

`ctx preview --design` writes `preview.data.json` and prints a design brief.
`/ctx:preview --design` then has me invoke **`ui-ux-pro-max`**, starting from a
low-fidelity sketch every time, and produce a page designed for *this* plan —
its own typography, colour, rhythm and interaction.

Two guards stop "unique" from becoming "wrong":

- **`ctx preview --check`** re-derives the view-model and asserts that every
  unit name, every `owns` path, every acceptance-criterion count and every
  plain-language entry actually appears in the rendered page. A beautiful page
  that silently drops a step fails the check.
- **`ctx preview --check` also fails on any external reference** — `http://`,
  `https://`, `//cdn`, `<link rel=stylesheet href=…>`, `fetch(`, `XMLHttpRequest`.
  Offline-openable is a correctness property here, not a preference.

**A design unit is not done when the assertions pass. It is done when the page
has been opened in a browser and looked at.** Source measurements do not
substitute for seeing it — every design unit below carries this in its
acceptance criteria.

---

## 6. Units

Cut along file ownership, so same-wave units never touch the same file.

### Wave 1

#### `01-plain-source`
**Owns:** `ctx/plain.py`, `tests/test_plain_source.py`
**Reads:** `ctx/frontmatter.py`, `ctx/plan.py`, `ctx/miniyaml.py`, `tests/support.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_html.py`, `ctx/cli.py`

The plain-language layer: parse `plain.md`, and generate the structural
fallback when it is missing.

**Interfaces produced — keep stable, `02` codes against these:**
```python
plain.PLAN_SECTIONS = ("summary", "why now", "what changes for you",
                       "what could go wrong", "out of scope")
plain.UNIT_FIELDS   = ("what it does", "why it matters", "what changes",
                       "risk", "how we'll know")

plain.path(layout, slug) -> Path                  # .ctx/plans/<slug>/plain.md
plain.load(layout, slug) -> Plain                 # never raises on absence
plain.scaffold(layout, slug, units) -> Path       # writes an empty plain.md

class Plain:
    exists: bool
    revision: int | None       # from frontmatter; None when absent
    stale: bool                # revision != plan.json revision
    plan: dict                 # PLAN_SECTIONS -> str ("" when unwritten)
    units: dict                # unit name -> {UNIT_FIELDS -> str}
    missing: list              # unit names with no entry
    def unit(self, name, fallback_facts) -> dict   # authored, else generated
```

**Acceptance criteria**
1. `load` on a plan with no `plain.md` returns `Plain(exists=False)` with every
   section `""` and every unit name in `missing`. It does not raise.
2. `load` parses `## Unit: <name>` sections and the five bolded fields, with
   surrounding whitespace stripped. An unrecognised field is ignored, not an
   error — a human wrote this file by hand.
3. A `## Unit: <name>` naming a unit that is not in the plan is reported in a
   `Plain.unknown` list rather than silently kept.
4. `stale` is True when frontmatter `revision` differs from `plan.json`'s, and
   False when they match. A `plain.md` with no `revision` key is stale.
5. `unit()` returns authored text when present; otherwise generated sentences
   built only from facts: step position, how many files change, what it waits
   for, what runs alongside it, how it is checked. Generated text never claims
   a purpose or a risk level.
6. Generated text contains none of `owns`, `forbid`, `depends_on`, `wave`,
   `tier`, `budget_tokens`, `subagent`, or any path separator. Asserted with an
   explicit vocabulary test over a plan fixture, plus a positive control proving
   the same assertion fires when a banned word is introduced.
7. `scaffold` writes a `plain.md` with every plan section and one
   `## Unit:` block per unit, each field present and empty, so a human or an
   agent has a form to fill in rather than a blank page.
8. Python 3.8 compatible. Stdlib only.

---

#### `03-safe-html`
**Owns:** `ctx/preview_html.py`, `tests/test_preview_html_safety.py`
**Reads:** `ctx/redact.py`, `tests/support.py`
**Forbid:** `ctx/plain.py`, `ctx/preview.py`, `ctx/preview_page.py`, `ctx/cli.py`

The security-critical primitives. Pure functions, no I/O, no knowledge of plans.

**Interfaces produced:**
```python
preview_html.escape(text) -> str            # &<>"' and nothing clever
preview_html.markup(text) -> str            # escape, THEN the tiny subset
preview_html.embed_json(obj) -> str         # </script>-safe, sorted, stable
preview_html.attr(value) -> str             # attribute-context escaping
preview_html.SUBSET = ("paragraph", "list", "fence", "code", "bold", "italic")
```

**Acceptance criteria**
1. `markup("</script><img src=x onerror=alert(1)>")` yields text containing no
   `<script`, no `<img`, and no `onerror`, and renders visibly as that literal
   string. Same for `javascript:` and `data:text/html` in link position.
2. The markdown subset is exactly `SUBSET`. Raw HTML in the source is escaped,
   never passed through — asserted by feeding `<b>x</b>` and getting escaped text.
3. `markup` escapes *before* applying markup. Proven by a case that would
   produce different output if the order were reversed, e.g. `` `<b>` ``.
4. `embed_json` output can be placed inside `<script type="application/json">`
   and survives a payload containing `</script>`, `<!--`, ` ` and ` `,
   parsing back to the identical object.
5. `embed_json` is deterministic: same object, byte-identical output, keys sorted.
6. Redaction is applied by `markup` — a unit objective containing an
   AWS-shaped key or a `postgres://user:pw@host` URL emits `<<redacted>>`.
   Positive control: an ordinary file path is *not* redacted.
7. Every function has a fuzz-ish test over a list of at least 20 hostile inputs,
   each asserting the output contains no unescaped `<` outside generated tags.
8. Python 3.8 compatible. Stdlib only.

---

### Wave 2

#### `02-view-model`
**Depends on:** `01-plain-source`, `03-safe-html`
**Owns:** `ctx/preview.py`, `tests/test_preview_model.py`
**Reads:** `ctx/plan.py`, `ctx/plain.py`, `ctx/preview_html.py`, `ctx/paths.py`,
`ctx/complexity.py`, `tests/support.py`
**Forbid:** `ctx/plain.py`, `ctx/preview_html.py`, `ctx/preview_page.py`, `ctx/cli.py`

Assembles the single dict everything downstream renders from.

**Interfaces produced — `04`, `05` and `06` all code against this:**
```python
preview.SCHEMA = 1
preview.view_model(layout, slug) -> dict
preview.data_path(layout, slug) -> Path       # preview.data.json
preview.html_path(layout, slug) -> Path       # preview.html
preview.write_data(layout, slug) -> Path
```

View-model shape (stable; additive changes only):
```python
{
  "schema": 1,
  "plan": {"slug", "title", "spec", "revision", "generated",
           "counts": {"units", "waves", "budget_tokens"}},
  "plain": {"present", "stale", "missing": [...], "sections": {...}},
  "steps": [                       # ordered, human numbering starts at 1
    {"number", "slug", "title",
     "plain": {"what", "why", "changes", "risk", "how_we_know", "generated"},
     "round", "alongside": [...], "waits_for": [...],
     "tech": {"tier", "model", "status", "budget_tokens",
              "owns", "reads", "forbid", "depends_on",
              "criteria": [...], "verify": [...], "sections": {...}}}
  ],
  "graph": {"nodes": [...], "edges": [...]},
  "ownership": [{"path", "steps": [...], "contested": bool}]
}
```

**Acceptance criteria**
1. `view_model` on the repo's own `close-the-wave-1-audit-blockers` fixture
   returns every unit, in wave order, numbered from 1, with `alongside` and
   `waits_for` derived from `plan.json` waves and `depends_on` — never
   hand-written.
2. Called twice on unchanged input, the two dicts are equal, and
   `json.dumps(vm, sort_keys=True)` is byte-identical. No `datetime.now()`
   anywhere in the module — asserted by grepping the module source in the test.
3. All prose passes through `preview_html`'s redaction before entering the dict.
4. `plain.present` is False and `plain.missing` lists every unit when
   `plain.md` is absent; the model still contains a full `steps` list with
   `plain.generated == True` on each.
5. `ownership` marks a path `contested` when two steps in the *same round*
   declare it, and not when they are in different rounds.
6. `counts.budget_tokens` is the sum of unit budgets; a unit with no budget
   contributes 0 rather than raising.
7. A plan with zero units returns a valid model with empty `steps` and does not
   raise. A plan whose `plan.json` is missing raises a clear `SystemExit`
   naming `ctx plan-check`, not a `KeyError`.
8. `write_data` output round-trips through `json.load` unchanged.
9. Python 3.8 compatible. Stdlib only.

---

### Wave 3

#### `04-baseline-page`
**Depends on:** `02-view-model`
**Owns:** `ctx/preview_page.py`, `tests/test_preview_page.py`
**Reads:** `ctx/preview.py`, `ctx/preview_html.py`, `tests/support.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_html.py`, `ctx/plain.py`, `ctx/cli.py`, `commands/`

The built-in page: the tested floor that always exists.

**Interfaces produced:**
```python
preview_page.render(vm) -> str                 # complete HTML document
preview_page.write(layout, slug) -> Path       # render + write preview.html
```

**Acceptance criteria**
1. Output is one self-contained document: no `http://`, no `https://`, no
   `//cdn`, no external `<link>`, no `fetch(`, no `XMLHttpRequest`, no
   `<script src=`. Asserted as a string test over the rendered output.
2. Opening it needs no server: all CSS inline in `<style>`, all JS inline, the
   view-model embedded via `preview_html.embed_json`.
3. The default view contains none of `owns`, `forbid`, `depends_on`,
   `budget_tokens`, `wave`, `tier`, `subagent`, or any `.py` path *outside*
   elements marked with the technical-detail class. Asserted by extracting the
   non-technical DOM regions and running the vocabulary check from `01`.
4. All nine reviewer sections from §4.1 are present, in that order, each with a
   heading a non-technical reader would recognise.
5. Steps are numbered from 1. Slugs appear only inside technical regions.
6. Concurrency is stated in words on every step that has siblings: "happens at
   the same time as Step 2", and dependencies as "waits for Step 1 and Step 2".
7. The technical toggle is CSS-class based and works with JavaScript disabled to
   the extent of leaving the plain view fully readable — a page that turns blank
   without JS fails.
8. `prefers-color-scheme: dark` is honoured; both themes define an explicit
   background and foreground, and neither leaves any text below 4.5:1 contrast.
9. A print stylesheet expands all collapsed regions and removes interactive
   chrome, so `Cmd-P → PDF` yields a complete readable document.
10. Renders correctly at 400px wide: no horizontal page scroll, tables and the
    SVG graph each in their own scroll container.
11. `render(vm)` twice yields byte-identical output.
12. When `plain.present` is False, a visible banner says the plain-language
    summary has not been written, and the page still renders every step.
13. **Verified by looking.** The unit is done only after the page is opened in a
    browser at desktop and 400px widths, in light and dark, and the print
    preview inspected. The report includes what was seen, not only that the
    assertions passed.

---

#### `06-design-brief`
**Depends on:** `02-view-model`
**Owns:** `ctx/preview_design.py`, `commands/preview.md`, `tests/test_preview_design.py`
**Reads:** `ctx/preview.py`, `commands/plan.md`, `commands/start.md`, `tests/support.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_page.py`, `ctx/preview_html.py`, `ctx/cli.py`

The bespoke-design path and its slash command.

**Interfaces produced:**
```python
preview_design.brief(vm) -> str          # the text `ctx preview --design` prints
preview_design.check(html, vm) -> list   # [] when the page faithfully covers vm
```

**Acceptance criteria**
1. `brief` states, in order: where `preview.data.json` was written; that the
   design must start from a low-fidelity sketch; that `ui-ux-pro-max` is the
   skill to invoke; the data contract (read from the embedded JSON, never
   retype plan content); the offline constraint; the plain-language rules from
   §4.2; and the exact `ctx preview --check` command that judges the result.
2. `check` returns a problem for each of: a unit name absent from the page, an
   `owns` path absent from the technical regions, an acceptance-criteria count
   that disagrees with the model, a missing plain-language entry that the model
   has, and any external reference or `fetch(`.
3. `check` returns `[]` for the baseline page from `04` — the tested renderer
   must pass its own checker, which is what proves the checker is calibrated.
4. Every "this is rejected" test has a positive control: a sibling assertion
   showing `check` returns `[]` once the defect is removed.
5. `commands/preview.md` follows the house frontmatter (`description`,
   `allowed-tools`, `argument-hint`), shells out via
   `"${CLAUDE_PLUGIN_ROOT}/bin/ctx" preview $ARGUMENTS || true`, and instructs
   the sketch-first `ui-ux-pro-max` flow, ending with: open the page and look at
   it before reporting done.
6. Only `$ARGUMENTS` is used for substitution, and no `!` line can exit non-zero
   and kill the command.
7. Python 3.8 compatible. Stdlib only.

---

### Wave 4

#### `05-cli-wiring`
**Depends on:** `02-view-model`, `04-baseline-page`, `06-design-brief`
**Owns:** `ctx/cli.py`, `tests/test_preview_cli.py`
**Reads:** `ctx/preview.py`, `ctx/preview_page.py`, `ctx/preview_design.py`,
`ctx/plain.py`, `ctx/plan.py`, `tests/support.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_page.py`, `ctx/preview_html.py`,
`ctx/preview_design.py`, `ctx/plain.py`

⚠️ **This is the only unit that touches `ctx/cli.py`.** A concurrent session is
editing that file for the wave-1 audit work. Do not dispatch this unit until
that work has landed on `main`. Everything in waves 1–3 is new files and can
proceed in parallel with it safely.

Keep the footprint small — roughly fifteen lines plus the subparser.

**Acceptance criteria**
1. `ctx preview [slug]` renders and writes `preview.html`, echoing the relative
   path. With no slug it uses the active plan; with no active plan it prints the
   same "no active plan" guidance the other plan commands print and exits 0.
2. `ctx plan-check` additionally writes `preview.html` and echoes it, after
   `plan.json` and the README. If rendering fails, `plan-check` still succeeds
   and prints a `note:` — a preview problem must never block planning.
3. `ctx start` prints exactly one advisory line: the preview's path when it
   matches the current revision, or that it is missing or behind. It prints
   nothing else new and changes no exit code, no dispatch behaviour, and no
   existing output line.
4. `ctx preview --design` writes `preview.data.json` and prints
   `preview_design.brief`.
5. `ctx preview --check` runs `preview_design.check` against the file on disk,
   printing each problem and exiting non-zero when there are any. It is added to
   `HARD_FAIL`.
6. `ctx preview --open` opens the file with `webbrowser.open`, and on a headless
   machine prints the path instead of raising.
7. `ctx preview --scaffold-plain` writes the `plain.md` form via
   `plain.scaffold`, refusing to overwrite an existing one without `--force`.
8. Every existing test in `tests/` still passes, unmodified. Any change to an
   existing test is a defect in this unit.
9. Python 3.8 compatible. Stdlib only.

---

### Wave 5

#### `07-document-preview`
**Depends on:** all
**Owns:** `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json`, `ctx/__init__.py`
**Forbid:** `ctx/` except `__init__.py`, `tests/`, `commands/`

**Acceptance criteria**
1. README gains a section covering the page, `plain.md`, the `--design` flow and
   the offline guarantee, in the README's existing voice.
2. CHANGELOG gains a 0.9.0 entry.
3. Version bumped to `0.9.0` in **both** `ctx/__init__.py` and
   `.claude-plugin/plugin.json` — a `ctx` change shipped without bumping
   `plugin.json` leaves every installed copy stale.
4. `ctx doctor` output and the briefing budget are unchanged by this feature.

---

## 7. Waves

| Round | Units | Why together |
|---|---|---|
| 1 | `01-plain-source`, `03-safe-html` | Independent leaf modules, no shared files |
| 2 | `02-view-model` | Needs both interfaces above |
| 3 | `04-baseline-page`, `06-design-brief` | Both consume the view-model, own different files |
| 4 | `05-cli-wiring` | Single owner of `ctx/cli.py`; also gated on the other session |
| 5 | `07-document-preview` | Documents what actually shipped |

---

## 8. Verification

Every unit: `python3 -m unittest discover -s tests -q`, plus `kind: diff`.

Whole-feature checks, run after wave 4:

```bash
ctx plan-check                 # writes preview.html as a side effect
ctx preview --check            # faithfulness + offline guarantees
python3 -m unittest discover -s tests -q
ctx ci
```

And the check no command can make: **open `preview.html` and read it as if you
did not write it.** If a non-technical reader could not say what is about to
happen, why, and what could go wrong, the feature has not shipped — regardless
of what the suite says.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Committed generated HTML makes noisy diffs | Byte-determinism: the file changes only when the plan does |
| `plain.md` goes stale as the plan is revised | Revision stamp, visible staleness banner, reported by `--check` |
| A bespoke design silently misrepresents the plan | `--check` asserts faithfulness against the view-model |
| The page becomes an unreviewed XSS vector | Escape-then-markup, hostile-input tests, no raw HTML passthrough |
| `05` collides with the in-flight session | It is the only unit touching `cli.py`, and it is last |
| Scope creep into an approval gate | Explicit non-goal; `start` prints one line and never blocks |

---

## 10. Out of scope

- Any blocking approval gate or stored approval state.
- Interactive review, comments, or verdicts exported back into the ledger.
- Hosting, uploading, or sharing the file anywhere. It is a file on disk.
- Previews for tasks (L1) or specs — plans only, for now.
- Localisation.
