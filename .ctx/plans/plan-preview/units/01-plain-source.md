---
ctx_schema: 1
unit: 01-plain-source
plan: plan-preview
tier: subagent
depends_on: []
owns:
  - ctx/plain.py
  - tests/test_plain_source.py
reads:
  - path: ctx/frontmatter.py
    symbols:
      - Document
      - parse
      - read
  - path: ctx/plan.py
    symbols:
      - plan_dir
      - graph_path
      - load_units
  - path: ctx/contract.py
    symbols:
      - field_digests
      - combine
  - path: ctx/atomic.py
    symbols:
      - write_text
  - path: tests/support.py
forbid:
  - ctx/preview.py
  - ctx/preview_html.py
  - ctx/preview_page.py
  - ctx/commands.py
  - ctx/cli.py
budget_tokens: 55000
status: pending
verify:
  - kind: diff
  - kind: symbol
    path: ctx/plain.py
    contains:
      - PLAN_SECTIONS
      - UNIT_FIELDS
      - def path(
      - def load(
      - def scaffold(
      - def digest(
      - class Plain
      - def unit(
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/plain.py tests/test_plain_source.py
wave: 1
---

## Objective
`ctx/plain.py`: read the authored plain-language file for a plan, say precisely
which parts a human has actually written, and generate honest structural
sentences for the parts nobody has.

## Context you need
A `ctx` plan lives at `.ctx/plans/<slug>/` with `plan.json` (derived),
`README.md` and `units/NN-name.md` contracts. This feature adds `plain.md`
alongside them: plain-English prose, **authored by a person**, that a
non-technical reviewer reads instead of the unit contracts. Your module is the
only thing that parses it.

`plain.md` format — five plan sections and one block per unit:

```markdown
---
ctx_schema: 1
plan: plan-preview
digest: 4f2a1c…
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

`frontmatter.Document.sections()` already splits a body on `##`. Use it rather
than writing a parser.

## Interfaces
Produced — unit `03-view-model` codes against these, so they are frozen once
your wave ends:

```python
plain.PLAN_SECTIONS = ("summary", "why now", "what changes for you",
                       "what could go wrong", "out of scope")
plain.UNIT_FIELDS   = ("what it does", "why it matters", "what changes",
                       "risk", "how we'll know")

plain.path(layout, slug) -> Path            # .ctx/plans/<slug>/plain.md
plain.digest(layout, slug) -> str           # content digest of the plan
plain.load(layout, slug) -> Plain           # never raises on absence
plain.scaffold(layout, slug, units, force=False) -> Path

class Plain:
    exists: bool
    digest: str | None      # as stamped in frontmatter; None when absent
    stale: bool             # stamped digest != the plan's digest now
    plan: dict              # PLAN_SECTIONS -> str ("" when unwritten)
    units: dict             # unit name -> {UNIT_FIELDS -> str}
    missing: list           # unit names with no authored entry
    unknown: list           # `## Unit:` names not in the plan
    def unit(self, name, facts) -> dict
```

`facts` is a plain dict the caller assembles — `{"number": int, "of": int,
"files": int, "waits_for": [int], "alongside": [int], "checks": [str]}` — so
your generator never reaches into a plan itself. Define the exact keys you need
and document them in the module docstring; `03` will fill them.

## Acceptance criteria

**Parsing and absence**
1. `load` on a plan with no `plain.md` returns `Plain` with `exists=False`,
   every `PLAN_SECTIONS` entry `""`, and every unit name in `missing`. It does
   not raise.
2. `## Unit: <name>` blocks parse, and the five bolded fields are extracted
   with surrounding whitespace stripped. An unrecognised bolded field is
   ignored rather than an error — a human wrote this file by hand and will get
   it slightly wrong.
3. A `## Unit:` naming a unit that is not in the plan lands in `unknown`. It is
   never silently dropped, and never counted as authored.
4. **A field present but empty counts as unauthored**: the unit lands in
   `missing` for that field and falls back to generated text. This is what
   keeps a scaffolded-but-unfilled form from being mistaken for real prose, and
   it is the single most important behaviour in this module — `plan-check` will
   scaffold the form automatically, so unfilled forms will be common.

**Staleness — read this carefully, the obvious implementation is wrong**
5. `digest(layout, slug)` returns a stable hex digest over the units'
   *substantive* fields. Reuse `contract.field_digests` / `contract.combine`
   rather than writing a second digest scheme.
6. `Plain.stale` is True when the stamped `digest:` differs from `digest()`
   now, and True when nothing is stamped. It is False when they match.
7. **Do not use `plan.json`'s `revision` counter for staleness.**
   `plan.write_graph` increments `revision` on *every* `plan-check` run
   (`ctx/plan.py:867`), so revision-based staleness would report `plain.md`
   stale after a re-check that changed nothing. A test asserts that running the
   equivalent of a no-op re-check leaves `stale` False.

**Generated fallback**
8. `unit()` returns authored text where present, and otherwise sentences built
   only from `facts`: step position, how many files change, what it waits for,
   what runs alongside it, how it is checked. Each returned dict carries a flag
   saying which fields were generated.
9. **Generated text never claims a purpose or a risk level.** It may say "this
   step changes four files and runs at the same time as step 2". It may not say
   "low risk" or "this improves safety" — nothing mechanical knows either.
10. Generated text contains none of `owns`, `forbid`, `depends_on`, `wave`,
    `tier`, `budget_tokens`, `subagent`, or any path separator. Asserted by an
    explicit vocabulary test, **plus a positive control** that introduces a
    banned word into the generator's input and proves the same assertion fires.
    An assertion that cannot fail is the defect this project has found nine
    times.

**Scaffolding**
11. `scaffold` writes every plan section and one `## Unit:` block per unit, each
    of the five fields present and empty — a form to fill in, not a blank page.
    It stamps the current `digest:`.
12. `scaffold` on an existing `plain.md` **appends blocks only for units that
    have none** and never modifies authored text, unless `force=True`, which
    rewrites the whole form. A test authors prose, adds a unit, re-scaffolds,
    and asserts the prose survived byte-for-byte.
13. Writes go through `atomic.write_text`.

**Project rules**
14. Python 3.8 compatible, standard library only, `ruff` clean.
15. Every test is shown failing before it passes. Say so in your report, with
    the failure output — not "I wrote tests and they pass".
16. No file outside `owns` is modified.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim verify
output · the exact `facts` dict keys you settled on, because `03-view-model`
must fill them · any interface you were forced to change.
