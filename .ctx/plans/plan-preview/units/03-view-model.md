---
ctx_schema: 1
unit: 03-view-model
plan: plan-preview
tier: subagent
depends_on:
  - 01-plain-source
  - 02-safe-html
owns:
  - ctx/preview.py
  - tests/test_preview_model.py
reads:
  - path: ctx/plan.py
    symbols:
      - plan_dir
      - graph_path
      - load_units
      - check
      - apply_waves
      - parallelism
      - bottlenecks
      - critical_path
      - ownership_gaps
  - path: ctx/plain.py
    symbols:
      - load
      - digest
      - Plain
  - path: ctx/preview_html.py
    symbols:
      - markup
      - embed_json
  - path: ctx/complexity.py
  - path: ctx/paths.py
    symbols:
      - Layout
  - path: tests/support.py
forbid:
  - ctx/plain.py
  - ctx/preview_html.py
  - ctx/preview_page.py
  - ctx/commands.py
  - ctx/cli.py
budget_tokens: 60000
status: pending
verify:
  - kind: diff
  - kind: symbol
    path: ctx/preview.py
    contains:
      - SCHEMA
      - def view_model(
      - def data_path(
      - def html_path(
      - def write_data(
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/preview.py tests/test_preview_model.py
wave: 2
---

## Objective
`ctx/preview.py`: assemble the one JSON-safe dict that every rendered page is
built from, so the HTML never sees a plan directly and cannot invent, drop or
reword a fact.

## The rule that defines this unit
**Build on the derivation `plan-check --json` already performs. Do not
recompute it.**

`ctx/plan.py` exposes `parallelism`, `bottlenecks`, `critical_path` and
`ownership_gaps`; `ctx/commands.py::_plan_intelligence` is the caller that
turns them into the `--json` document. Call the same `plan_mod` functions. A
second derivation of waves, ordering or ownership is a second truth free to
disagree with the first, and criterion 5 exists to catch exactly that.

`commands.py` is owned by unit `05` and is on your `forbid` list — read it, do
not edit it.

## Interfaces
Produced — unit `04-baseline-page` and unit `05-cli-wiring` both code against
these:

```python
preview.SCHEMA = 1
preview.view_model(layout, slug) -> dict
preview.data_path(layout, slug) -> Path      # .ctx/plans/<slug>/preview.data.json
preview.html_path(layout, slug) -> Path      # .ctx/plans/<slug>/preview.html
preview.write_data(layout, slug) -> Path
```

Shape (additive changes only once your wave ends):

```python
{
  "schema": 1,
  "plan": {"slug", "title", "spec", "revision", "digest",
           "counts": {"units", "waves", "budget_tokens"}},
  "plain": {"present", "stale", "missing": [...], "unknown": [...],
            "sections": {...}},
  "steps": [
    {"number", "slug", "title",
     "plain": {"what", "why", "changes", "risk", "how_we_know", "generated"},
     "round", "alongside": [...], "waits_for": [...],
     "tech": {"tier", "model", "status", "budget_tokens", "owns", "reads",
              "forbid", "depends_on", "criteria": [...], "verify": [...]}}
  ],
  "graph": {"nodes": [...], "edges": [...]},
  "ownership": [{"path", "steps": [...], "contested": bool}],
  "concurrency": {...},   # from plan_mod.parallelism
  "critical_path": {...}, # from plan_mod.critical_path
}
```

Consume `plain.Plain.unit(name, facts)` from unit `01` for every step's prose.
Its report names the exact `facts` keys — read it before writing the caller.

## Acceptance criteria

**Agreement with the existing derivation**
1. Every wave, ordering, bottleneck and ownership fact is taken from
   `plan_mod`'s functions, not recomputed locally.
2. A test builds a fixture plan, runs `ctx plan-check --json`, and asserts the
   view-model agrees with that document on: wave membership, unit count, wave
   count, the parallelism ratio and the critical-path estimate. **If the two can
   disagree, this unit has failed**, and the test is what proves they cannot.

**Structure**
3. `view_model` returns every unit in wave order, `number` starting at 1.
   `alongside` and `waits_for` are derived from the waves and `depends_on`,
   never hand-written, and are expressed as step *numbers* so the page never has
   to know a slug.
4. `ownership` marks a path `contested` when two steps in the **same round**
   declare it, and not when they are in different rounds.
5. `counts.budget_tokens` sums unit budgets; a unit with no budget contributes 0
   rather than raising.

**Determinism**
6. Called twice on unchanged input the two dicts are equal and
   `json.dumps(vm, sort_keys=True)` is byte-identical.
7. **No `datetime.now()` anywhere in the module**, asserted by parsing the
   module source in a test. `plan.json`'s `generated` date may be carried
   through as data; the module may not read a clock itself.
8. Note that `plan.json`'s `revision` increments on every `plan-check` run. Carry
   it as a technical-view fact, but **nothing user-visible may depend on it** —
   staleness comes from `plain.digest`, per unit `01`.

**Safety and degradation**
9. All prose passes through `preview_html.markup` — and therefore redaction —
   before entering the dict. Structured values (`budget_tokens`, paths, tiers)
   do not: they are not prose, and scrubbing them is a recorded past defect.
10. With no `plain.md`: `plain.present` is False, `missing` lists every unit,
    and `steps` is still complete with `generated` true on each entry. The model
    is never empty because prose is missing.
11. A plan with zero units returns a valid model with empty `steps` and does not
    raise. A missing `plan.json` raises a `SystemExit` naming `ctx plan-check`,
    not a `KeyError`.
12. `write_data` writes through `atomic.write_text` and round-trips through
    `json.load` unchanged.

**Project rules**
13. Python 3.8 compatible, standard library only, `ruff` clean.
14. Every test is shown failing before it passes. Report the failure output.
15. No file outside `owns` is modified. If you find yourself needing to edit
    `commands.py`, `cli.py`, `plain.py` or `preview_html.py`, **stop and report
    the exact edit** — do not widen scope.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim verify
output · the final view-model shape as shipped, because `04` renders it · any
interface you were forced to change.
