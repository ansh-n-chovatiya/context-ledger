# Plan — Plan Briefing: a shareable page that explains a plan in plain words

**Status:** proposed. Not yet a ledger plan — see §11 before dispatching.
**Target version:** 0.10.0 (0.9.0 belongs to `make-the-ctx-cli-safe-to-automate`)
**Written:** 2026-09-10
**Supersedes:** the first draft (`plan.md`), which four reviews found defective in
eleven confirmed ways. Every one is closed below; §12 records them so the same
mistakes are not re-made.

---

## 1. The problem

A `ctx` plan exists as `plan.json`, a terse `README.md`, and unit contracts
written for an engineer or a subagent. There is no artefact a person can read to
find out what is about to happen.

Claude normally solves this with an artifact — but an artifact needs a claude.ai
account. The people who most need to understand a plan often do not have one, and
may not be technical.

Context Ledger's job is to run work in parallel and organise it for somebody who
is not writing prompts by hand. That job is unfinished if the only readable
description of the work is a file full of `owns:` globs and `_check_cmd`
internals.

**So:** every plan can produce a self-contained HTML page, written in plain
language, that opens by double-clicking — no account, no server, no network.

## 2. What this is, and what it is not

It is a **briefing**, not an approval. The page explains what is about to
happen and ends with one line of recourse: *"This page describes work. It never
asks you to run, install, or authorise anything. If something looks wrong, tell
whoever sent it before they run the next step."*

That wording is load-bearing. The first draft called itself a page a reviewer
could *approve* while its own non-goals removed every approval mechanism — an
artefact soliciting a decision it could not receive. Either it grows an approval
state machine (a sixth stage in a five-stage process) or it stops claiming to be
one. It stops claiming.

**Non-goals, held firmly:**

- Nothing blocks. `ctx start` prints one advisory line and dispatches as always.
- No approval state, no `reviewed:` flag, no checkboxes, no round-trip.
- Nothing enters the SessionStart briefing. That budget is the project's core value.
- No server, no CDN, no fonts, no `fetch`. It opens with the wifi off.

## 3. Files

```
.ctx/plans/<slug>/
  plan.json                 structure, for machines          (committed, exists)
  README.md                 the board, for the orchestrator  (committed, exists)
  units/*.md                contracts, for engineers         (committed, exists)
  plain.md                  NEW — plain language, authored   (committed)
  revisions/                                                 (committed, exists)

.ctx/runtime/preview/
  <slug>-briefing.html      NEW — the generated page         (gitignored)
  <slug>-model.json         NEW — the view-model, --design   (gitignored)
```

`plain.md` is committed: it is authored, small, diffable, reviewable in a pull
request, and useful with no HTML at all. It is the artefact with content.

The HTML is **not** committed. Every tracked file under `.ctx/` today is
`.md`/`.json`/`.yaml`; no build output has ever been committed there, and
`.ctx/.gitignore` is exactly `runtime/`, so `runtime/preview/` needs no new
rule. Three facts killed the committed version: `write_graph` increments
`revision` on every `plan-check` even when nothing changed (`plan.r5.json` and
`plan.r6.json` are byte-identical apart from it), `plan.json` stamps
`date.today()`, and the page is large. A committed file that re-diffs on no-op
runs is noise, and determinism is an argument *against* committing, not for it:
anyone with the repo regenerates it byte-identically.

Sharing is unchanged — you send the file.

The filename carries the slug because five files called `preview.html` in a
downloads folder are indistinguishable, and `<title>` carries the plan's
plain-language title because that is the browser tab and the PDF filename.

## 4. The one rule

**The page never sees the plan. It sees a view-model.**

```
plan.json ─┐
units/*.md ─┼─► view_model(layout, config, slug) ─► dict, JSON-safe, sole truth
plain.md  ─┘                                         │
                        ┌────────────────────────────┴───────────────┐
                        ▼                                            ▼
              preview_page.render()                    <slug>-model.json + brief
              built-in page, tested                    the --design path
```

Both renderers consume the same dict. A bespoke design can change every pixel
and still cannot invent, drop or reword a fact — *provided* the tripwire in §8
holds, which is a weaker guarantee than the first draft claimed.

### 4.1 The pipeline, in the only correct order

**`sanitize` → `redact` → `escape` → `markup`.** Four steps. The draft said two
and got the order wrong, which both leaked secrets and corrupted text.

**`sanitize(text)`** — strip C0/C1 controls except `\n` and `\t`; strip
U+200B–200F, U+202A–202E, U+2060–2064, U+2066–2069, U+FEFF; strip U+2028/2029
(which also break `str.splitlines`, a corruption vector `miniyaml` already
documents); NFC-normalise. This is what stops a bidi override making
`Step 3 — delete\u202E)sdrocer remotsuc(` read as something else while every
substring check passes.

**`redact`** runs on plain text, before escaping, **once**, and only over
free-prose fields. Verified against the real module, the draft's order simply
does not work — `_ASSIGNMENT`'s value class excludes `&`, so once `"` is `&quot;`
nothing matches:

```
'"password": "hunter2"'   scrub→escape: '&quot;password&quot;: &quot;<<redacted>>&quot;'
                          escape→scrub: '&quot;password&quot;: &quot;hunter2&quot;'   ← LEAKS
```

And redaction over structured or numeric data destroys it:

```
budget_tokens: 60000      → budget_tokens: <<redacted>>       ← the page's own data
{"budget_tokens": 60000}  → {"budget_tokens": <<redacted>>    ← brace eaten, JSON dies
credential: none needed   → credential: <<redacted>> needed   ← "none" deleted
```

The third is the dangerous one. Redaction is irreversible and runs on the write
path, so a hostile unit contract can use it to delete the half of a sentence the
reader needed: `"deletes the archive; retention token: keep-90-days"` becomes
`retention token: <<redacted>>`. Precision-over-recall is right for the journal
and insufficient here.

Therefore: **a field allowlist**, not a blanket scrub. Only prose fields are
scrubbed; numbers, paths, unit names, counts and budgets never are. And when
redaction fires, the page shows a visible marker that text was removed, so the
reader knows something was taken rather than reading a mutated sentence.
`config["redact"]` patterns are threaded through — a project that added a house
pattern must get it honoured here too, not only in the journal.

**Redaction is a backstop, not a control.** It matches only named shapes. An
internal hostname, a ticket id, a customer name, a bare 40-char hex, or
"the staging login is admin/spring2024" all pass straight through. The control
is the field allowlist plus a check that no absolute path, `$HOME`, `/Users/`,
`/home/` or `C:\Users\` reaches the output.

**`escape`** is `html.escape(s, quote=True)` — not a hand-rolled replace chain,
whose classic ordering bug turns `&lt;` into `&amp;lt;`.

**`markup`** consumes only already-escaped text. It never re-reads the source and
never unescapes — that rule is what closes the code-fence info-string hole
(```` ```py" onload="alert(1) ````). Truncation happens *before* escaping, never
after, or entities get cut in half.

### 4.2 Escaping is context-blind, so contexts are removed

`escape()` is safe in exactly one place: element content. Everything below
survives it untouched, so the design removes the context rather than sanitising it.

- **No links.** `SUBSET` has no link construct; `href` is not a channel. The
  draft demanded `javascript:` be safe "in link position" while declaring no
  links exist — a vacuously-passing test of exactly the fail-green shape this
  repo's audit exists to remove. `preview_html.href(v)` returns `#` unless
  `^#[A-Za-z0-9_-]+$`. No autolinking of bare URLs. This also kills
  `file:///Users/victim/.ssh/id_rsa` (same-origin-ish from `file://`) and
  `\\attacker.example.com\share\x.png`, which on Windows fetches over SMB and
  leaks the reviewer's NTLM hash on page load.
- **No interpolation into `<script>` or `<style>`.** They are raw-text elements
  where escaping is inert and `\` is unescaped. The only channel is `embed_json`.
- **Attributes carry their own quotes.** `attr()` returns the value *including*
  quotes so a caller cannot omit them — `class=step-{name}` with
  `name="x autofocus onfocus=alert(1)"` contains no escapable character.
- **The SVG graph is built from structure only.** Node ids from the step index,
  never the slug. Inside `<svg>` the tokeniser changes: CDATA is honoured,
  `<script>`/`<style>` execute, `<foreignObject>` re-enters HTML. None permitted.
- **Fences and inline code are extracted first** and exempted from every other
  rule, or `pytest -k "a*b*c"` in a verify command renders as `a<em>b</em>c` and
  the reader approves a command different from the one that runs.
- **The markup scanner is single-pass and line-oriented**, no nested quantifiers,
  with a hard input cap. A regex subset over attacker text running inside a
  `Stop` hook is the same unbounded-`re.search` bug `01-verify-kinds` just fixed
  in `verify.py`. Payload: 50,000 `*` characters in an objective.

### 4.3 `embed_json`

`json.dumps` alone is not sufficient. Verified: it emits `</script>` and
`<!--<script>` raw, and bare `Infinity`.

```python
def embed_json(obj):
    text = json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
```

`</script>` terminates the element. `<!--<script>` enters double-escaped state
where the document's *real* `</script>` no longer terminates — a whole-page
content-hiding primitive. Bare `Infinity` makes `JSON.parse` throw, so the page
loads with no data and **fails open, silently**. Read side:
`JSON.parse(el.textContent)` — never `innerHTML`, never `eval`.

### 4.4 Paths

`plan_dir(layout, slug)` gives zero confinement — `Path(base) / "/etc/evil"`
discards the base. It is safe today only because every caller routes through
`bundle.slugify`. The new path-producing functions reopen it, and the slug can
come from data: `plain.md` frontmatter carries `plan:`, and `plan.json` carries
`"plan"`. Neither is slugified anywhere. `plan: ../../../.github/workflows/ci.yml`
would be a write primitive.

One helper, called by every path-producing function:

```python
def _slug(raw):
    s = str(raw)
    if not s or s != bundle.slugify(s):
        raise SystemExit("plan slug must be [a-z0-9-] — refusing to resolve %r" % s)
    return s
```

Plus `commonpath` confinement after `resolve()` (which follows symlinks), and
`view_model` refuses a `plan.json` whose `"plan"` disagrees with the directory it
came from, and re-validates every unit name against `plan._UNIT_NAME`.

This is the `verify.py` oracle in mirror image: `--check` prints per-problem
detail, so an unconfined slug is a one-bit read oracle over any readable file.

**`layout.rel()` must not be used.** Verified: it resolves relative paths against
`os.getcwd()`, so the same call returns `ctx/verify.py` from the repo root and
`ctx/ctx/verify.py` from inside `ctx/`; and its `except` branch returns absolute
paths verbatim, leaking `/Users/<name>/`. Use `plan._normalise` — pure string,
forward slashes, no filesystem access — and treat any absolute or `..` entry in
`owns` as a problem rather than rendering it.

## 5. Writing for the reader

### 5.1 Section order

The draft's order was an author's outline. A reader asks different questions,
in a different order, and three of the first four were missing entirely.

1. Title, and one sentence
2. **What this is** — a briefing; how to object; who to tell
3. **The problem, and what happens if we do nothing** — often the strongest fact
4. **What we'll do, and what you'll notice**
5. **What it costs** — money, wall-clock, how many agents at once
6. **What could go wrong**, the two steps to read hardest, and what we are
   deliberately *not* fixing
7. **Can this be undone** — blast radius: tool vs. your data, branch vs. main
8. **The steps** — reference material, which is why it is here and not third
9. **How we'll know it worked** — machine checks vs. what *you* could check
10. Glossary, provenance, and a pointer to the source files

Cost is not "390k tokens". That is a budget, not a bill, and to this reader it
is not a number. It is money (tokens × a stated rate) and wall-clock, and real
data exists — the journal shows this repo's last plan ran 19:47→21:13, first
results 26 minutes in.

### 5.2 Drop the wave spine

The draft made round structure the page's spine. Three arguments against, all
from real data:

- **It is not interesting.** This plan's wave shape is 2,1,2,1,1 — maximum
  concurrency two, five of seven steps serial.
- **It encodes file ownership, not meaning.** Two steps share a round because
  they own different files, not because they belong together — and conversely
  `01` and `03` are one story split across rounds only to avoid a collision.
  Round structure actively scrambles the grouping a reader wants.
- **It is the least stable thing in the plan.** Across `ctx-0-8`'s 15 revisions
  the wave shape changed four times.

Replace with what a reader actually wants, all derivable: **when do I hear
something and when is it over**; **what has to work first** (here, step 3 blocks
five of seven); **how many agents run at once, and what that costs**; **which
steps touch the same files**; and **what happens if one fails mid-run** — for
which the honest answer is reassuring and true: one did, the gate caught it, and
the plan did not collapse.

### 5.3 `plain.md`

The draft's five fields were tested by writing two real entries and failed on
six counts. Revised format:

```markdown
---
ctx_schema: 1
plan: <slug>
---

## Summary
## Why now
## What you'll notice
## What could go wrong
## What we're not doing
## What this costs

## Unit: 01-safe-html
digest: <contract.combine(field_digests(doc))>
status: pending
**Problem 1 — <short name>**
**What it does:** …
**Why it matters:** …
**What we're choosing not to fix here:** …
**Problem 2 — …** (repeat; a unit may close several)
**What you'll notice:** …
**Machine checks:** …
**What you could check yourself:** …
**Source:** units/01-safe-html.md:41
```

What changed and why:

- **Multiple problems per step.** Both real units close two distinct findings.
  One entry cannot carry them, and the draft's own worked example proved it:
  written deliberately, as the showcase, it **dropped a security finding
  entirely**, said "the single file" when the unit owns three, and rated it
  *"Risk: Low"* when the contract authorises rewriting an existing test. Every
  error ran toward reassurance. The proposed `--check` would have passed it.
- **`risk` is deleted, not extended.** A severity word written by the AI that
  wrote the plan is worth less than nothing — it discharges the reader's
  attention onto the judgement of the thing being overseen. It is replaced by
  **derived blast radius** (file count; tool vs. user data; branch vs. main;
  **whether existing tests are being amended**) and **what a failure would look
  like to you**. "This step is authorised to rewrite an existing test" is
  mechanically derivable and worth ten *"risk: low"*s.
- **Attention is derived, not asserted.** Rank steps by files owned, existing
  tests amended, downstream steps blocked, contested files, budget, and security
  vocabulary in the objective; print *"if you read two things, read these."* On
  the real plan that surfaces `03` and `01` — correctly.
- **`how we'll know` splits in two.** "The tests pass" is the AI grading its own
  homework. **What you could check yourself** is the only line on the page a
  non-technical reader can independently falsify.
- **A place for deliberate exclusions.** Real units carry "Decisions already
  taken" and accepted holes. Those are exactly what a reader might object to,
  and the draft dropped them silently.
- **Status per step, in the plain view.** Right now all seven unit files read
  `done` while the committed README still says `pending` for four. A page
  written in the future tense about finished work lies by grammar.
- **A source line per field** — turns "trust me" into "check me".

**Provenance is shown inline per field: `authored | drafted | generated`.** When
`plain.md` is missing, the page still renders from generated structural
sentences with a visible banner. That is what lets the page exist with no agent
in the loop.

**Staleness is per-step and content-based**, not the plan revision. The revision
is broken in both directions: it bumps on no-op re-checks (so the banner becomes
permanent wallpaper) and `plan.json` records no unit prose (so every acceptance
criterion can be rewritten with the revision unchanged). `ctx/contract.py`
already ships `field_digests`, `combine`, `digest` and `compare` — the exact
mechanism already trusted to catch agents editing their own contracts. Stamp each
`## Unit:` block with the digest it was written from. A stale field renders
struck through and replaced inline by the generated fallback — a reader who skims
past a banner cannot skim past a struck-out sentence.

### 5.4 Disclosure, and accessibility

**Per-claim `<details>`, not a global mode.** The draft's technical toggle serves
neither audience: the engineer has the unit file (richer, and the source of
truth), all-or-nothing punishes a reader who wants one fact, it creates a place
to hide things, and its test — a regex deciding which side of a DOM boundary a
string is on — is gamed by markup rather than meaning. Each plain sentence
resting on a fact gets an inline `<details>` revealing that fact and its source
line. The affordance changes from *"here is the scary stuff you're spared"* to
*"here is my evidence"*. One global "open everything" checkbox remains, for print.

`<details>`/`<summary>` also solves no-JS properly: it opens without script, is
announced by screen readers, and prints open under `details[open]`.

Required and currently absent: strict heading hierarchy with no skips; the SVG
graph with `role="img"`, `<title>`/`<desc>` and a text equivalent; `lang="en"`;
**no meaning carried by colour alone** (the draft's `● low risk` dot is the worst
possible encoding for a page about pass/fail); focus visibility;
`prefers-reduced-motion`; 200% reflow; a **light ground forced for print**
(dark mode + print is an ink brick); and a reading-level floor over the plain
regions — median sentence under ~20 words, none over 40, no undefined acronyms.

Honest translation is *longer* than the draft's sample. The criteria must not
reward brevity.

## 6. Units

Three modules, five units, four waves. `preview_html.py` stays separately
**ownable** so the page unit cannot quietly weaken the escaper; `plain.md`
parsing folds into the view-model (it has no other consumer); the tripwire folds
into the page unit (it is calibrated against `render()`'s output, so they are one
unit); docs stay separate, per the repo's own precedent.

Every unit carries the house frontmatter — `ctx_schema`, `unit`, `plan`, `tier`,
`status: pending`, `depends_on`, `owns`, `reads` as `- path:`/`symbols:`,
`forbid`, `budget_tokens`, `verify` — and the house sections: `## Objective`,
`## Decisions already taken (do not relitigate)`, `## Constraints`,
`## Interfaces`, `## Acceptance criteria`, `## Return contract`. The draft had
none of these, and `plan.validate` would have rejected all seven units on
missing `tier` and `verify` alone.

| # | Unit | Wave | depends_on | Owns | Tier | Budget | Model |
|---|---|---|---|---|---|---|---|
| 01 | `01-safe-html` | 1 | — | `ctx/preview_html.py`, `tests/test_preview_html_safety.py` | subagent | 55k | opus |
| 02 | `02-view-model` | 1 | — | `ctx/preview.py`, `tests/test_preview_model.py`, `tests/fixtures/preview_plan/` | subagent | 80k | sonnet |
| 03 | `03-page-and-tripwire` | 2 | 01, 02 | `ctx/preview_page.py`, `tests/test_preview_page.py`, `tests/test_preview_tripwire.py` | subagent | 95k | opus |
| 04 | `04-cli-wiring` | 3 | 03 | `ctx/cli.py`, `tests/test_preview_cli.py` | subagent | 55k | sonnet |
| 05 | `05-document-preview` | 4 | 01–04 | `README.md`, `CHANGELOG.md`, `commands/preview.md`, `ctx/__init__.py`, `.claude-plugin/plugin.json` | subagent | 40k | sonnet |

Wave sums 135k / 95k / 55k / 40k, all under the 250k `wave_budget_tokens` cap.
`opus` on `01` because the escape-ordering argument is subtle, and on `03`
because the design judgement *is* the feature — a default model produces a page
that passes every assertion and reads like a config dump.

`commands/preview.md` sits in `05`, after the subparser lands in `04`. In the
draft it sat two waves *earlier*, which would have broken
`test_every_bang_line_names_a_real_subcommand` — asserting every `!` line names a
registered subcommand — and since that suite is every unit's verify command, the
unit would have failed its own gate.

`05`'s `forbid` is enumerated, not `ctx/ except __init__.py`. The list has no
exclusion syntax, `hooks.on_pre_tool_use` tests `forbid` before `owns`, and
`_matches("ctx/__init__.py", ["ctx/"])` is True — the runner would have been
nudged off the one thing that must not be missed: the version bump in both
`ctx/__init__.py` and `.claude-plugin/plugin.json`.

### 6.1 Verify blocks, and the two judged checks

Every unit:

```yaml
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
```

`03` and `05` additionally carry the checks that make this feature's real
done-bar visible to the gate. The draft stated both in prose, where
`_gate_before_done` — which reads the `verify:` block, not acceptance criteria —
could never see them.

```yaml
# 03-page-and-tripwire
  - kind: human
    about: page opened at 1440px and 400px, light and dark, print preview
           inspected, and opened once with the network interface down

# 05-document-preview
  - kind: rubric
    about: open the page and read it as if you did not write it. If a
           non-technical reader could not say what is about to happen, why,
           and what could go wrong, this has not shipped.
```

Both return PENDING, `verdict_of` ranks PENDING above ERROR, and the transition
is refused until sign-off. Adding a judged kind also raises `complexity.score`
via `weights["judged_verify"]`, which feeds `dispatch.model_for` — a more
expensive model for the units needing judgement. That is the correct incentive.

### 6.2 Criteria are written with positive controls

Roughly 57 of the draft's 60 criteria were fail-green — assertable without the
feature working. The pattern and its repair, which every unit's criteria follow:

| Draft (fail-green) | Honest |
|---|---|
| "no `http://`, no `fetch(`… asserted as a string test" — `render()` returning `""` passes | (a) output contains every step title and is ≥ N bytes; (b) `_external_refs(html) == []` **and** `_external_refs(html_with_injected_cdn) == ['https://cdn.example/x.css']`, proving the scanner fires |
| "renders twice, byte-identical" — true of any constant | pair with sensitivity: two models differing in one title produce output differing at that title |
| "no `datetime.now()` — asserted by grepping the module" — blind to `date.today()`, `time.time()`, `uuid4()`, and an empty module passes | monkeypatch the clock to raise, rebuild, assert equality with a frozen build — **and** assert bumping the fixture's revision *does* change it |
| "yields text containing no `<script`" — `""` passes | `assertEqual(markup(payload).strip(), '<p>&lt;/script&gt;…</p>')`. An equality is its own positive control |
| "20 hostile inputs, no unescaped `<`" — `""` scores 20/20 | per input, `html.unescape(strip_tags(markup(s))) == s` — the text survives *and* nothing became markup |
| "`check` returns a problem for each of…" — a constant non-empty list passes all five | assert the problem list by **equality**, naming the specific missing unit |
| "if rendering fails, `plan-check` still succeeds" — a `plan-check` that never calls the renderer passes | (a) renderer patched to raise → exit 0 + `note:`; (b) unpatched → exit 0 **and** the file exists with an mtime after the run started |
| "prints exactly one advisory line" | capture `ctx start` stdout with the page absent and present; assert the symmetric difference of the line sets is exactly one line naming the path |
| "on a headless machine prints the path" — no headless machine in CI | monkeypatch `webbrowser.open` to raise `webbrowser.Error` → exit 0 + path; positive control asserts one call with the correct `file://` URI |

**Mutation verification** is required for the two security-critical units, as
the repo's own `06-merge-preflight` does: mutate the escaper to a passthrough,
confirm the new tests die, restore, and report both directions verbatim.

Criteria never cite this document by section number — the draft's did, and
`commands/start.md` forbids the orchestrator from reading source. The nine
section headings become `preview_page.SECTIONS`; the banned vocabulary becomes a
constant; both travel in the unit file.

## 7. Contrast, print, and width — mechanical where possible

The draft asserted things a string test cannot decide. Where a formula exists,
use it; an agent eyeballing a screenshot is strictly worse.

- **Contrast:** parse hex tokens out of `<style>`, compute WCAG relative
  luminance for each declared foreground/background pair, assert ≥ 4.5:1 — and
  assert the CSS actually contains those tokens.
- **No-JS:** stripping every `<script>` element still leaves every step title and
  every plain paragraph present.
- **Print:** every collapse class has a `@media print` counterpart that unsets
  it, enumerated from the render; light ground forced.
- **400px:** no `min-width` above 400px, no fixed pixel widths on layout
  containers, `overflow-x:auto` on table and SVG wrappers.
- **"Correctly" / "readable"** fold into the `kind: human` check. That is what it
  is for.

## 8. The tripwire (formerly "the faithfulness checker")

It is renamed because it cannot prove faithfulness and should not claim to. As
drafted it was substring containment over HTML source, defeated by a single
`<div hidden>` holding every unit name and path. It could not see visibility,
association, or **invention** — and for a briefing, a fabricated fact is more
dangerous than a missing one. It checked omission only.

`ctx preview --check`:

1. **Equality on the data.** Exactly one `<script type="application/json"
   id="ctx-view-model">`; parse it; require `== view_model(...)`. The only
   unforgeable anchor in the file.
2. **Check rendered text, not source.** Walk with `html.parser.HTMLParser`; drop
   `<script>`, `<style>`, `<template>`, comments, and anything `hidden`,
   `aria-hidden="true"`, or inline-styled `display:none|visibility:hidden|
   opacity:0|font-size:0`. Assert against *that* text.
3. **Ban the hatches by construction:** no `<template>`; no long HTML comments;
   no `content:` declaration containing letters; no `<script>` but the JSON blob
   and an allowlisted, byte-pinned toggle script.
4. **Require anchors.** Each step's subtree carries `data-step="<slug>"`, each
   fact `data-field="…"`. Then association is checkable: this step's paths appear
   under *this* step and nowhere else. No anchors → fail.
5. **Check for invention.** Every path-shaped token, every integer and every
   `Step \d+` in visible text must be traceable to a view-model value. This is
   the only assertion that catches a fabricated fact.
6. **Count what the reader sees:** exactly `len(steps)` step headings.
7. **Bind the file to its inputs:** embed and display a sha256 over the sorted
   bytes of `plan.json` + every `units/*.md` + `plain.md`; `--check` recomputes.
8. **Coverage, not presence.** Fail when a unit objective contains `**(a)**`/
   `**(b)**` sub-findings and the plain entry describes fewer; when a security
   word (`execution`, `credential`, `arbitrary`, `oracle`, `secret`, `remote`)
   appears in the objective and nowhere in the plain entry; when `what you'll
   notice` names fewer files than `owns` holds. Each of these would have caught
   this document's own first draft.
9. **Social engineering:** flag shell-command shapes and imperative
   "run / install / enter your" phrasing in the plain view. Perfect escaping
   still renders attacker-chosen prose to a non-technical person in a file that
   looks authoritative.

**Offline is an allowlist, not a blocklist.** Six substrings cannot cover it:
`<base href>` alone rewrites every relative URL in the document. Parse; for every
URL-bearing attribute (`src`, `srcset`, `imagesrcset`, `href`, `data`, `poster`,
`background`, `action`, `formaction`, `cite`, `ping`, `usemap`, `manifest`,
`archive`, `codebase`, `longdesc`, `profile`, SVG `xlink:href`/`href`) require
`^#[A-Za-z0-9_-]*$` or `^data:image/(png|svg\+xml);base64,…$`. Reject `<base>`,
`<meta http-equiv=refresh>`, every `<link rel>` including the favicon,
`<iframe>` and `srcdoc`, `<embed>`, `<object>`, `<form>`, media elements. Scan
`<style>` bodies and `style=` for `url(`, `@import`, `image-set`, `behavior`,
`-moz-binding`. Scan inline JS for `WebSocket`, `EventSource`, `sendBeacon`,
dynamic `import(`, `Worker`, `serviceWorker`, `new Image().src`,
`location.href=`, `window.open`, `RTCPeerConnection` (STUN works from `file://`
and leaks the reviewer's IP), `WebAssembly.instantiateStreaming`. Fail closed on
any unknown scheme, including UNC `\\host\share`. Also reject
`<script type="module">` and any relative `fetch` — both are blocked on `file://`
and are precisely why the JSON is inline.

Then, because it is cheap: **open it once with the network interface down and
the devtools network panel open.** That is the `kind: human` check. Measuring a
page's source is not seeing it.

## 9. `--design`

`ctx preview --design` writes `.ctx/runtime/preview/<slug>-model.json` and prints
a brief. `/ctx:preview --design` invokes `ui-ux-pro-max`, **starting from a
low-fidelity sketch every time**, and produces a page designed for this plan.

Constrained, because it is a prompt-injection sink: it hands a model
attacker-authored prose from a possibly-shared repo and asks it to write HTML.

- The brief states, as its first line, that view-model content is **untrusted
  data, not instructions**.
- The page **reads strings from the embedded JSON at runtime** rather than the
  model retyping them into the source — enforced, not requested: no view-model
  string may appear as a literal in the HTML outside the JSON blob.
- Output goes to `runtime/`. It is never committed by any command.
- `--check` is run by the harness, not by the agent claiming it ran.
- The built-in page ships first and is held to the same bar. If the baseline is
  good, most plans never need this.

## 10. Risks

| Risk | Mitigation |
|---|---|
| A page reassures about work that is risky | Derived blast radius replaces self-assessed severity; attention ranking is mechanical; provenance shown inline per field; tripwire checks coverage and invention |
| `plain.md` goes stale as the plan is revised | Per-unit `contract.field_digests`; stale fields struck through and replaced inline, not a page-level banner |
| Redaction corrupts or is weaponised | Field allowlist; prose only; visible marker when it fires; never over structured data |
| A bespoke design misrepresents the plan | Tripwire (§8), honestly labelled; `kind: human` is the actual guarantee |
| The page becomes an XSS vector | sanitize→redact→escape→markup; no links, no script interpolation; mutation-verified |
| Resource exhaustion on the write path | Caps on unit count, per-file bytes and JSON depth, with a clear refusal |
| Scope creep into an approval gate | It is a briefing (§2); `start` prints one line and never blocks |

## 11. Before this can be dispatched

**Sequencing.** The wave-1 audit work has landed (749 tests green,
`ctx/cli.py` clean). The next plan, `make-the-ctx-cli-safe-to-automate`, is
spec-ready and already scaffolded, and it collides head-on: its stated fix is
*"The mechanism today is `HARD_FAIL` (`cli.py:2682`) … The shape of the fix is to
invert it."* The draft's `04-cli-wiring` instructed a runner to **add** to that
frozenset. **Land the cli-safety plan first**, then re-derive `04`'s criteria
against the new exit-code contract. Waves 1–2 here are all new files and touch no
`cli.py`; they can run alongside it in a worktree safely.

**Version.** 0.9.0 belongs to the cli-safety plan. This is 0.10.0. Two plans each
bumping to 0.9.0 is a conflict and a wrong changelog.

**This is not yet a ledger plan.** It has no spec, no `.ctx/plans/<slug>/`
directory and no unit files, so no tool in the repo can see it. Converting it is
deliberate — it would flip the active-plan pointer, and another session is
mid-plan. When the time comes: `ctx spec`, answer the questions below, then
`ctx plan` and one unit file per row in §6.

**Three questions a spec must answer before planning:**

1. Should `plan-check` write the page at all, given it now lands in `runtime/`?
   (Leaning yes — it is free and the advisory line needs something to point at.)
2. Who authors `plain.md`, and when? The flow assumed is `plan-check` scaffolds
   the form, `/ctx:preview` fills it in. That keeps `commands/plan.md` untouched.
3. Should `ctx ci` gain a preview row? It already iterates plans. A missing page
   must be `ok`, not `FAIL`, or every pre-0.10.0 plan turns CI red.

**One upstream fix worth doing separately:** `write_graph` should not increment
`revision` on a no-op re-check — compare the graph minus `revision`/`generated`
and keep the revision if identical. It benefits the whole ledger, not just this
feature, and it is not this plan's to make.

## 12. What the first draft got wrong

Recorded so it is not re-made. All eleven were verified against real code.

1. Redaction after escaping — leaked; and over structured data — corrupted.
2. `json.dumps` treated as `</script>`-safe. It is not, and `Infinity` fails open.
3. `layout.rel()` — cwd-dependent output, and absolute home paths leaked verbatim.
4. Every unit missing `tier` and `verify`; `plan.validate` rejects all seven.
5. `commands/preview.md` two waves before its subparser — the unit fails its own gate.
6. Staleness keyed on a revision that bumps on no-ops and is blind to prose edits.
7. `HARD_FAIL` misread, and colliding with the next plan.
8. The worked example dropped a security finding and mis-rated risk downward.
9. Tripwire defeated by `<div hidden>`; checked omission, never invention.
10. Offline as a six-item blocklist; `<base href>` alone defeats it.
11. ~57 of 60 criteria fail-green.

Plus three structural errors: modules cut for the dispatcher rather than the
reader; a wave spine that is neither meaningful nor stable; and an artefact that
called itself an approval while removing every means of approving.

## 13. Out of scope

- Any blocking gate, stored approval, or `reviewed:` state.
- Interactive review, comments, verdicts exported back into the ledger.
- Hosting or uploading. It is a file on disk.
- Previews for tasks (L1) or specs — plans only.
- Localisation.
- **The results page.** Regenerating this page *after* a run — promised vs.
  actual, built from the journal, `contract.compare` and the findings ledger —
  is the obvious sequel and probably more valuable than this. Every input
  already exists. It is named here precisely so it stays out of this plan.
