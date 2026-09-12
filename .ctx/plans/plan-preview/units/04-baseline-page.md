---
ctx_schema: 1
unit: 04-baseline-page
plan: plan-preview
tier: subagent
depends_on:
  - 03-view-model
owns:
  - ctx/preview_page.py
  - tests/test_preview_page.py
  - tests/test_shared_paths.py
reads:
  - path: ctx/preview.py
    symbols:
      - SCHEMA
      - view_model
      - html_path
  - path: ctx/preview_html.py
    symbols:
      - escape
      - markup
      - embed_json
      - attr
  - path: ctx/atomic.py
    symbols:
      - write_text
  - path: tests/support.py
forbid:
  - ctx/preview.py
  - ctx/preview_html.py
  - ctx/plain.py
  - ctx/commands.py
  - ctx/cli.py
budget_tokens: 70000
status: pending
verify:
  - kind: diff
  - kind: symbol
    path: ctx/preview_page.py
    contains:
      - def render(
      - def write(
      - def check(
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/preview_page.py tests/test_preview_page.py
  - kind: human
wave: 3
---

## Objective
`ctx/preview_page.py`: render the view-model into one self-contained HTML page
that a non-technical person can read and approve — and a checker that proves the
page did not quietly drop anything.

## Who this page is for
Somebody who does not read `plan.json`, has no claude.ai account, and may not be
technical. They open the file by double-clicking it, with the wifi off, and it
must tell them: what is about to happen, why, what will be different afterwards,
which steps run at the same time, what could go wrong, and what is deliberately
not being done. **That is the product.** A page that is technically correct and
unreadable to that person has failed, whatever the assertions say.

## Interfaces
```python
preview_page.render(vm) -> str            # a complete HTML document
preview_page.write(layout, slug) -> Path  # render + atomic write of preview.html
preview_page.check(html, vm) -> list      # [] when the page covers vm faithfully
```

## Layout
Plain by default; one **Technical detail** switch reveals paths, `owns`/`forbid`,
dependencies, criteria verbatim, verify commands, tier and budget — in place,
not on a separate page.

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

Plus: the wave timeline as the spine, the dependency graph as inline SVG, a
print stylesheet, dark mode via `prefers-color-scheme`, readable at 400px.

## The module enumeration — read before you finish
`tests/test_shared_paths.py` holds `ALL_MODULES`, a hand-written tuple of every
module in `ctx/`, and asserts `sorted(ALL_MODULES) == sorted(ctx/*.py)` by
**exact equality in both directions**. It is deliberately not a glob: a new
module is either listed there or the completeness test goes red.

You create a new module, so **you must add its name to `ALL_MODULES` in the same
change**. The file is in your `owns` for that reason. Add only the module you
created — a name listed before its file exists fails the same assertion from the
other side.

Check the other per-module rules in that file still hold for your module: it
must not build a `units` path segment (only `plan.py` and `paths.py` may) and
must not glob `*/units/*.md`.

## Acceptance criteria

**Self-contained**
1. No `http://`, `https://`, `//cdn`, external `<link>`, `fetch(`,
   `XMLHttpRequest` or `<script src=`. Asserted over the rendered string.
2. All CSS and JS inline; the view-model embedded via
   `preview_html.embed_json`.

**What it says, and in what language**
3. All nine sections, in this order, each under a heading a non-technical reader
   would recognise: the plan's name in words · what we're going to do · why
   we're doing it · what will be different afterwards · how the work is split
   up · each step · what could go wrong · what we are not doing · how we'll
   check it worked.
4. The default view contains none of `owns`, `forbid`, `depends_on`, `wave`,
   `tier`, `budget_tokens`, `subagent`, or any `.py` path **outside** elements
   marked technical. Asserted by extracting the non-technical DOM regions and
   running the vocabulary check over them — not by searching the whole document,
   which would pass trivially.
5. Steps are numbered from 1. Slugs like `01-plain-source` appear only inside
   technical regions.
6. Every step that has siblings states its concurrency **in words**: "happens at
   the same time as Step 2", "waits for Step 1 and Step 2". Not "wave 1".
7. A glossary covers the few terms that cannot be avoided.

**How it behaves**
8. The technical toggle is CSS-class based. **A page that turns blank with
   JavaScript disabled fails** — render the plain view as static HTML and let
   the toggle reveal, never let JS construct the content.
9. `prefers-color-scheme: dark` is honoured. Both themes set an explicit
   background *and* foreground — a transparent body borrows the host's theme and
   produces black-on-black. No text below 4.5:1 contrast.
10. The print stylesheet expands collapsed regions and drops interactive chrome,
    so `Cmd-P → PDF` is a complete document.
11. Renders at 400px with no horizontal page **page** scroll. Tables and the SVG
    each sit in their own `overflow-x: auto` container; they are the only things
    allowed to scroll sideways.
12. `render(vm)` twice is byte-identical.
13. When `plain.present` is False a visible banner says the plain-language
    summary has not been written yet, and every step still renders from its
    generated text. Degraded, never broken.

**The checker**
14. `check()` returns one problem for each of: a unit name absent from the page ·
    an `owns` path absent from the technical regions · a criteria count
    disagreeing with the model · a plain entry the model has but the page lacks ·
    any external reference.
15. `check()` returns `[]` for this module's own output. **The renderer must pass
    its own checker** — that is what proves the checker is calibrated rather
    than vacuous.
16. Every rejection test has a positive control showing `[]` once the defect is
    removed.

**Verified by looking — this unit is not done when the assertions pass**
17. The page must be opened in a real browser at desktop width and at 400px, in
    light and in dark, and the print preview inspected. You are a subagent and
    cannot do this; **render a page from a real fixture plan, write it to a path
    you name in your report, and say so.** The orchestrator performs the
    inspection and signs off `kind: human`. Your report must name that file.
    Measuring the page's source is not seeing it.

**Project rules**
18. Python 3.8 compatible, standard library only, `ruff` clean.
19. Every test is shown failing before it passes. Report the failure output.
20. No file outside `owns` is modified. If you need `preview.py` or
    `preview_html.py` changed, **stop and report the exact edit**.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim verify
output · **the absolute path of a rendered sample page for visual inspection** ·
any interface you were forced to change.
