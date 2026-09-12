# Plan Preview — a shareable page a non-technical reviewer can approve

**Status:** proposed, not dispatched
**Target version:** 0.9.0
**Written:** 2026-09-10 · **Revised:** 2026-09-12, against `main` at `057a055`

> **Revision note.** The first draft predated the `report.md` remediation.
> Four of its assumptions are now false and one of its decisions conflicts
> with a later one; §11 lists every change and why. Read §11 before §6 if you
> have seen the earlier version.

---

## 1. The problem

A `ctx` plan exists as `plan.json`, a terse `README.md`, and unit contracts
written for an engineer or a subagent. **There is no artefact a person can look
at and say "yes, do that" before work starts.**

Claude normally reaches for an artifact, but that needs a claude.ai account.
The people who most need to approve a plan often do not have one and may not be
technical.

The product's thesis is running work in parallel and organising it so somebody
who is not writing prompts by hand can still direct it. That is unfinished while
the only readable description of the work is a file of `owns:` globs.

**So:** every plan gets a self-contained HTML file, in plain language, that
opens by double-clicking — no account, no server, no network.

---

## 2. What we are building

```
.ctx/plans/<slug>/
  plan.json          structure, for machines          (exists)
  README.md          the board, for the orchestrator  (exists)
  units/*.md         contracts, for engineers          (exists)
  plain.md           NEW — plain language, authored
  preview.html       NEW — the generated page, committed
  preview.data.json  NEW — the view-model, written by --data
```

### Non-goals

- **Nothing blocks.** `ctx start` prints one advisory line and dispatches as
  always. No gate, no approval state.
- The page is read-only. No checkboxes, no verdicts, no round trip.
- No server, no CDN, no fonts, no `fetch`. It opens from `file://` with the
  wifi off.

---

## 3. The one rule that makes this safe

**The HTML never sees the plan. It sees a view-model.**

```
ctx plan-check --json ─┐
units/*.md ────────────┼─► view_model() ─► dict, JSON-safe, single source
plain.md ──────────────┘                    │
                                            ▼
                                   render() → preview.html
```

Three properties hold for every generated page:

1. **Escape first, mark up second.** Unit prose can arrive from a shared
   repository and the output is a file somebody double-clicks. Every string is
   HTML-escaped, and only then is a tiny markdown subset applied to the
   already-escaped text. Embedded JSON is `</script>`-safe.
2. **Redaction on the write path.** `ctx/redact.py` scrubs all prose before it
   reaches disk. This file is committed and shared; a secret that reaches disk
   has already leaked. Note the ordering lesson already recorded in this repo:
   **scrub before escaping**, and only prose — redaction run the other way
   eats `budget_tokens` and inverts `credential: none`.
3. **Byte-deterministic.** No wall-clock, no random ids, sorted keys. Same plan
   revision in, identical file out — otherwise every `plan-check` produces a
   spurious diff on a committed artefact.

### Why this one is committed, when `DIGEST.md` is not

Wave 4 untracked `DIGEST.md` because a derived artefact that is committed
conflicts between concurrent authors. That precedent has to be answered, not
ignored.

`preview.html` is committed anyway, and the difference is real: `DIGEST.md` is
regenerated at **session end by every agent**, so two agents finishing at once
conflict on a file neither authored. `preview.html` regenerates only when
`plan-check` runs, is byte-deterministic, and changes only when the plan
changes — the same conditions under which `plan.json` is already committed
without complaint. And the entire point is handing the file to somebody who
cannot run `ctx` to regenerate it.

---

## 4. Writing for the reviewer

### 4.1 What the page says, in order

| Section | Answers |
|---|---|
| Heading | What this plan is called, in words, not a slug |
| What we're going to do | 3–5 sentences of narrative |
| Why we're doing it | The problem, without jargon |
| What will be different afterwards | Observable outcomes, not file changes |
| How the work is split up | The steps, and **which happen at the same time** |
| Each step | What · why · what changes · risk · how we'll know |
| What could go wrong | Risks in plain words |
| What we are *not* doing | Out of scope, so silence is not a promise |
| How we'll check it worked | Verification, described not commanded |

The parallelism story gets real estate, because that is the product. A reviewer
should see "steps 1 and 2 happen together, step 3 waits for both" without
knowing what a wave is.

### 4.2 Language rules the renderer enforces

Not style suggestions — unit `04` tests them.

- **No unexplained jargon in the default view.** `owns`, `forbid`,
  `depends_on`, `wave`, `tier`, `budget_tokens`, `subagent` and file paths live
  behind the technical toggle. The default says "runs at the same time as
  Step 2", not "wave 1".
- **Steps numbered from 1**, not `01-kebab-name`. The slug appears only under
  the toggle.
- **Every stated risk names a consequence**, not a severity word alone.
- **A glossary** covers terms that cannot be avoided.

### 4.3 Where the plain words come from

A renderer can translate *structure*. It cannot translate *substance* — nothing
mechanical turns "`_check_cmd` calls `_missing_tool(output)`" into "we're fixing
a safety check that sometimes hides real breakages".

So substance is authored into `plain.md` at planning time:

```markdown
---
ctx_schema: 1
plan: close-the-wave-1-audit-blockers
revision: 4
---

## Summary
We are fixing seven safety problems we found in our own checking system...

## Why now
## What changes for you
## What could go wrong
## Out of scope

## Unit: 01-verify-kinds
**What it does:** Makes the safety check report a real breakage as a real
breakage, instead of quietly filing it as a setup problem.
**Why it matters:** Right now the one failure the check exists to catch is the
one it can miss.
**What changes:** The single file that decides whether work passed or failed.
**Risk:** Low — it makes an existing check stricter; nothing new runs.
**How we'll know:** New tests deliberately break something and confirm the
check now notices.
```

`frontmatter.Document.sections()` already splits on `##`. `revision:` is
compared against `plan.json` — a mismatch marks entries "may be out of date"
and is reported by `ctx preview --check`.

**When `plain.md` is missing the page still renders**, falling back to generated
structural sentences plus a visible banner. The feature degrades to "less
useful", never "broken".

### 4.4 Baseline layout

Plain by default; one **Technical detail** switch reveals paths, `owns`/`forbid`,
dependencies, criteria verbatim, verify commands, tier, model and budget — in
place, not on a separate page.

```
┌──────────────────────────────────────── [ ● Technical detail ] ┐
│  Closing seven safety gaps in our checking system              │
│  7 steps · 5 rounds · about 390k tokens of work                │
├────────────────────────────────────────────────────────────────┤
│  How the work is split up                                      │
│   ┌ Round 1 ── Step 1 ─┬─ Step 2 ─┐   these happen together    │
│   └ Round 2 ─────────── Step 3 ───┘   waits for both           │
│                                                                │
│  Step 1 — Make the safety check honest            ● low risk   │
│  ▸ Happens at the same time as Step 2                          │
│  ▸ owns ctx/verify.py, tests/test_gates.py         ← toggled   │
└────────────────────────────────────────────────────────────────┘
```

Plus: wave timeline as the spine, dependency graph as inline SVG, print
stylesheet producing a clean PDF, dark mode via `prefers-color-scheme`, readable
at 400px.

---

## 5. Units

Five units, four waves. Cut along file ownership.

### Wave 1

#### `01-plain-source`
**Owns:** `ctx/plain.py`, `tests/test_plain_source.py`
**Reads:** `ctx/frontmatter.py`, `ctx/plan.py`, `tests/support.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_html.py`, `ctx/commands.py`, `ctx/cli.py`

```python
plain.PLAN_SECTIONS = ("summary", "why now", "what changes for you",
                       "what could go wrong", "out of scope")
plain.UNIT_FIELDS   = ("what it does", "why it matters", "what changes",
                       "risk", "how we'll know")
plain.path(layout, slug) -> Path
plain.load(layout, slug) -> Plain          # never raises on absence
plain.scaffold(layout, slug, units) -> Path

class Plain:
    exists: bool; revision: int|None; stale: bool
    plan: dict; units: dict; missing: list; unknown: list
    def unit(self, name, fallback_facts) -> dict
```

**Acceptance criteria**
1. `load` with no `plain.md` returns `Plain(exists=False)`, every section `""`,
   every unit in `missing`. Does not raise.
2. Parses `## Unit: <name>` and the five bolded fields, whitespace stripped.
   An unrecognised field is ignored, not an error — a human wrote this by hand.
3. A `## Unit:` naming a unit not in the plan lands in `unknown`, not silently.
4. `stale` is True when frontmatter `revision` differs from `plan.json`'s;
   a `plain.md` with no `revision` is stale.
5. `unit()` returns authored text when present, else sentences built only from
   facts: step position, how many files change, what it waits for, what runs
   alongside, how it is checked. **Generated text never claims a purpose or a
   risk level.**
6. Generated text contains none of `owns`, `forbid`, `depends_on`, `wave`,
   `tier`, `budget_tokens`, `subagent`, or any path separator. Asserted by an
   explicit vocabulary test **plus a positive control** proving the assertion
   fires when a banned word is introduced.
7. `scaffold` writes every plan section and one `## Unit:` block per unit, each
   field present and empty — a form to fill in, not a blank page.
8. Python 3.8 compatible, stdlib only, `ruff` clean.

---

#### `02-safe-html`
**Owns:** `ctx/preview_html.py`, `tests/test_preview_html_safety.py`
**Reads:** `ctx/redact.py`, `tests/support.py`
**Forbid:** `ctx/plain.py`, `ctx/preview.py`, `ctx/preview_page.py`, `ctx/commands.py`

Security-critical primitives. Pure functions, no I/O, no knowledge of plans.

```python
preview_html.escape(text) -> str
preview_html.markup(text) -> str        # escape, THEN the tiny subset
preview_html.embed_json(obj) -> str     # </script>-safe, sorted, stable
preview_html.attr(value) -> str
preview_html.SUBSET = ("paragraph","list","fence","code","bold","italic")
```

**Acceptance criteria**
1. `markup("</script><img src=x onerror=alert(1)>")` contains no `<script`, no
   `<img`, no `onerror`, and renders as that literal string. Same for
   `javascript:` and `data:text/html` in link position.
2. The subset is exactly `SUBSET`. Raw HTML is escaped, never passed through.
3. **Escape precedes markup**, proven by a case whose output differs if the
   order reverses, e.g. `` `<b>` ``.
4. `embed_json` survives `</script>`, `<!--`, and lone surrogates inside
   `<script type="application/json">`, parsing back to an identical object.
5. `embed_json` is deterministic: byte-identical, keys sorted.
6. Redaction applies — an AWS-shaped key or `postgres://user:pw@host` emits
   `<<redacted>>`. **Positive control: an ordinary file path is not redacted,
   and `budget_tokens: 60000` survives intact.**
7. At least 20 hostile inputs, each asserting no unescaped `<` outside
   generated tags.
8. Python 3.8, stdlib only, `ruff` clean.

---

### Wave 2

#### `03-view-model`
**Depends on:** `01-plain-source`, `02-safe-html`
**Owns:** `ctx/preview.py`, `tests/test_preview_model.py`
**Reads:** `ctx/plan.py`, `ctx/plain.py`, `ctx/preview_html.py`, `ctx/complexity.py`
**Forbid:** `ctx/plain.py`, `ctx/preview_html.py`, `ctx/preview_page.py`, `ctx/commands.py`

**Build on `plan-check --json`, do not re-derive it.** That contract already
carries `waves`, `parallelism`, `bottlenecks`, `critical_path` and
`ownership_gaps`. Re-deriving them here creates a second implementation of the
same truth, free to disagree with the first.

```python
preview.SCHEMA = 1
preview.view_model(layout, slug) -> dict
preview.data_path(layout, slug) -> Path
preview.html_path(layout, slug) -> Path
preview.write_data(layout, slug) -> Path
```

**Acceptance criteria**
1. Every wave/graph/ownership fact is taken from the existing plan-check JSON
   derivation, not recomputed. A test asserts the two agree on a fixture — if
   they can disagree, this criterion has failed.
2. `view_model` on this repo's own committed plans returns every unit, in wave
   order, numbered from 1, with `alongside` and `waits_for` derived, never
   hand-written.
3. Called twice on unchanged input the dicts are equal and
   `json.dumps(sort_keys=True)` is byte-identical. **No `datetime.now()` in the
   module** — asserted by parsing the module source.
4. All prose passes through `preview_html` redaction before entering the dict.
5. `plain.present` False and `missing` complete when `plain.md` is absent; the
   model still carries a full `steps` list with `generated == True`.
6. A plan with zero units returns a valid empty model. A missing `plan.json`
   raises a clear `SystemExit` naming `ctx plan-check`, not a `KeyError`.
7. Python 3.8, stdlib only, `ruff` clean.

---

### Wave 3

#### `04-baseline-page`
**Depends on:** `03-view-model`
**Owns:** `ctx/preview_page.py`, `tests/test_preview_page.py`
**Reads:** `ctx/preview.py`, `ctx/preview_html.py`
**Forbid:** `ctx/preview.py`, `ctx/preview_html.py`, `ctx/plain.py`, `ctx/commands.py`

```python
preview_page.render(vm) -> str
preview_page.write(layout, slug) -> Path
preview_page.check(html, vm) -> list      # [] when the page covers vm faithfully
```

**Acceptance criteria**
1. One self-contained document: no `http://`, `https://`, `//cdn`, external
   `<link>`, `fetch(`, `XMLHttpRequest`, or `<script src=`.
2. All CSS and JS inline; the view-model embedded via `embed_json`.
3. The default view contains none of the banned vocabulary from `01` outside
   elements marked technical. Asserted by extracting non-technical DOM regions.
4. All nine reviewer sections from §4.1, in order.
5. Steps numbered from 1; slugs only inside technical regions.
6. Concurrency stated in words on every step with siblings.
7. The technical toggle is CSS-class based; **a page that turns blank without
   JavaScript fails.**
8. `prefers-color-scheme: dark` honoured; both themes define explicit
   background and foreground; no text below 4.5:1 contrast.
9. Print stylesheet expands collapsed regions — `Cmd-P → PDF` is complete.
10. Renders at 400px with no horizontal page scroll; tables and the SVG each in
    their own scroll container.
11. `render(vm)` twice is byte-identical.
12. When `plain.present` is False, a visible banner says so and every step
    still renders.
13. `check()` flags: a unit name absent from the page, an `owns` path absent
    from technical regions, a criteria count disagreeing with the model, a
    missing plain entry the model has, and any external reference. It returns
    `[]` for this unit's own output — **the renderer must pass its own checker,
    which is what proves the checker is calibrated.** Every rejection test has
    a positive control showing `[]` once the defect is removed.
14. **Verified by looking.** Done only after the page is opened in a browser at
    desktop and 400px, light and dark, and the print preview inspected. The
    report says what was seen, not only that assertions passed.

---

### Wave 4

#### `05-cli-wiring`
**Depends on:** `03-view-model`, `04-baseline-page`
**Owns:** `ctx/cli.py`, `ctx/commands.py`, `commands/preview.md`,
`tests/test_preview_cli.py`, `tests/test_json_output.py`,
`tests/test_commands_registry.py`, `tests/test_docs_currency.py`
**Reads:** `ctx/preview.py`, `ctx/preview_page.py`, `ctx/plain.py`

**Ownership note, learned the hard way.** Adding a subcommand touches four
tests that pin the CLI surface, and adding keys to `plan-check --json` touches
the schema test and its golden. Three units blocked mid-wave this session for
exactly this, so those files are in `owns` up front rather than discovered.

`cli.py` holds `build_parser`, the `COMMANDS` registry and `main`;
`commands.py` holds the 41 `cmd_*` bodies. A new command is one registry entry
plus one body.

**Acceptance criteria**
1. `ctx preview [slug]` renders and writes `preview.html`, echoing the relative
   path. No slug uses the active plan; no active plan prints the same guidance
   the other plan commands print and exits 0.
2. `ctx plan-check` additionally writes `preview.html` and echoes it. **If
   rendering fails, `plan-check` still succeeds and prints a `note:`** — a
   preview problem must never block planning.
3. `ctx start` prints exactly one advisory line: the preview's path, or that it
   is missing or behind. No other new output, no exit-code change.
4. `ctx preview --data` writes `preview.data.json`.
5. `ctx preview --check` runs `preview_page.check` against the file on disk,
   printing each problem and exiting non-zero when there are any.
6. `ctx preview --open` uses `webbrowser.open`, printing the path instead of
   raising on a headless machine.
7. `ctx preview --scaffold-plain` writes the form, refusing to overwrite
   without `--force`.
8. `--json` is supported and its shape added to the schema test. `plan-check`'s
   JSON gains the preview path; the golden is updated in the same change.
9. `commands/preview.md` follows house frontmatter, ends its `!` line with
   `|| true`, and appears in the slash-command table (`test_docs_currency`).
10. **Every existing test passes; any assertion changed is reported with what it
    was pinning**, not quietly edited.
11. `SUITE_FLOOR` and `REQUIRED_FLOOR` stay equal and are not lowered; coverage
    floors likewise.
12. Python 3.8, stdlib only, `ruff` clean.

---

### Wave 5

#### `06-document-preview`
**Depends on:** all
**Owns:** `docs/reference.md`, `docs/walkthroughs.md`, `README.md`,
`CHANGELOG.md`, `.claude-plugin/plugin.json`, `ctx/__init__.py`

**Acceptance criteria**
1. `docs/reference.md` documents `ctx preview`, its flags and `plain.md`'s
   format; `docs/walkthroughs.md` shows the flow end to end. **README gains at
   most a few lines and a link** — it is 299 lines by deliberate decision and
   reference material does not go there.
2. CHANGELOG gains a 0.9.0 entry.
3. Version bumped in **both** `ctx/__init__.py` and `.claude-plugin/plugin.json`
   — a `ctx` change shipped without bumping `plugin.json` leaves every
   installed copy stale.
4. Every claim verified against the code, not against a sibling's report.
5. `ctx doctor` output and the briefing budget are unchanged by this feature.

---

## 6. Waves

| Round | Units | Why together |
|---|---|---|
| 1 | `01-plain-source`, `02-safe-html` | Independent leaves, no shared files |
| 2 | `03-view-model` | Needs both interfaces |
| 3 | `04-baseline-page` | Consumes the view-model |
| 4 | `05-cli-wiring` | Sole owner of `cli.py`/`commands.py` and the surface tests |
| 5 | `06-document-preview` | Documents what shipped |

Parallelism 1.2× — low, and unavoidable: this is a dependency chain, not a
fan-out. `plan-check` will say so; that is expected here rather than a defect.

---

## 7. Verification

Every unit: `python3 -m unittest discover -s tests -q`, plus `kind: diff`.

After wave 4:

```bash
ctx plan-check && ctx preview --check && ctx ci && ruff check ctx/ tests/
```

And the check no command can make: **open `preview.html` and read it as if you
had not written it.** If a non-technical reader could not say what is about to
happen, why, and what could go wrong, the feature has not shipped — whatever
the suite says.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Committed generated HTML makes noisy diffs | Byte-determinism; §3 argues the `DIGEST.md` precedent explicitly |
| `plain.md` goes stale | Revision stamp, staleness banner, reported by `--check` |
| The page becomes an XSS vector | Escape-then-markup, 20 hostile inputs, no raw HTML passthrough |
| The view-model disagrees with `plan-check` | It is built from that derivation, and a test asserts they agree |
| Design work consumes the wave | The bespoke `--design` path is cut; see §11 |

---

## 9. Out of scope

- Any blocking approval gate or stored approval state.
- Interactive review, comments, verdicts exported back to the ledger.
- Hosting or uploading. It is a file on disk.
- Previews for tasks (L1) or specs — plans only.
- **A bespoke per-plan design.** Cut deliberately; see §11.
- Localisation.

---

## 10. What this needs from a human

`plain.md` is authored, not generated. The feature's value is exactly as good
as those five sentences per unit. An agent can draft them at planning time, but
somebody who understands *why* the work is being done should read them before
the page is handed to a reviewer.

---

## 11. What changed from the 2026-09-10 draft, and why

| Change | Reason |
|---|---|
| **View-model builds on `plan-check --json`** | That contract now carries `waves`, `parallelism`, `bottlenecks`, `critical_path`, `ownership_gaps`. A parallel derivation would be a second truth free to disagree. |
| **`--design` path cut** (old unit `06`) | The most speculative third of the work. A unique design per plan is a want, not a need, and this repo has already recorded losing time to exactly that. `--check` survives, attached to the baseline. |
| **`05` owns `commands.py` and four surface tests** | `cli.py` is 538 lines and holds only the registry; bodies are in `commands.py`. Adding a command touches the generated parser fixture, the JSON schema test and its golden, and the docs-currency test — none of which any unit owned when three units blocked on this pattern this session. |
| **Docs go to `docs/`, not README** | README is 299 lines by decision; reference material lives in `docs/reference.md`. |
| **§3 argues the committed-artefact question** | Wave 4 untracked `DIGEST.md` for the opposite reason; the precedent has to be answered rather than ignored. |
| **Concurrent-session warning removed** | The wave-1 audit work landed long ago. |
| **Floors and `ruff` added to criteria** | Both are enforced now and both are pinned in pairs. |
| 7 units / 5 waves → **5 units / 5 waves** | Same chain, less speculative surface. |
