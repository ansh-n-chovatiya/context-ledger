---
ctx_schema: 1
unit: 08-dispatch-selection
plan: ctx-0-8
tier: subagent
depends_on:
  - 05-complexity-score
  - 07-review-telemetry
owns:
  - ctx/dispatch.py
  - ctx/config.py
  - tests/test_dispatch_selection.py
reads:
  - ctx/complexity.py
  - ctx/config.py
  - ctx/review.py
forbid: []
budget_tokens: 55000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Make `model_for` answer to the task instead of the seat: score-driven for the runner, package-driven for the reviewer, with the score and its inputs printed on every dispatch line.

## Interfaces
Consumes `complexity.score`/`tier_for`, `config.tier_up`,
`review.dispatch_stats`.

**You own the band-to-model bridge.** `complexity.tier_for` returns a *band*
(`light`/`standard`/`deep`); `config.models.tiers` holds *model names*
cheapest-first. Nothing maps one to the other yet — that is yours, per ADR
0002: map positionally by index, clamped to the last entry, so a project
running a two-entry `models.tiers` gets `deep` clamped onto the dearer of the
two rather than an IndexError. Assert the clamp with a two-entry list; it is
the case an unclamped index silently gets wrong.
Produces `model_for(config, unit=None, role="runner", *, stats=None,
round=1)`, preserving the existing positional signature so no caller breaks.
Produces the dispatch-line format carrying score and breakdown.

## Acceptance criteria
1. Precedence holds exactly: explicit `model:` > score-derived tier >
   `models.<role>` floor > `DEFAULTS`. A unit naming its own model is immune to
   every heuristic.
2. The dispatch line prints the score and each contributing input, so a wrong
   tier is diagnosable without re-running anything.
3. A review package under the small threshold with zero scope violations picks
   the cheaper reviewer tier; over the threshold, or with any violation, it
   does not.
4. With `models.escalate_on_failed_round: true`, round 2 dispatches one tier
   up. With the flag at its default `false`, all three rounds dispatch at the
   same model. Both asserted.
5. No model name appears in this module — every name comes from config.
6. `test_dispatch.py` passes untouched, including
   `test_every_concurrent_dispatch_names_a_model`.
15. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
