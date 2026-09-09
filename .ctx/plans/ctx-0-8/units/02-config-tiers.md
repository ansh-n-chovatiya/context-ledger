---
ctx_schema: 1
unit: 02-config-tiers
plan: ctx-0-8
tier: subagent
depends_on: []
owns:
  - ctx/config.py
  - tests/test_config_tiers.py
reads: []
forbid: []
budget_tokens: 30000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Add the ordered model-tier vocabulary, the complexity weights and thresholds, and the escalation flag to `config.DEFAULTS` — the only place a model name may appear.

## Interfaces
Produces, in `config.DEFAULTS`:
`models.tiers: [haiku, sonnet, opus]` (cheapest-first),
`models.escalate_on_failed_round: false`,
`complexity.weights: {budget_per_15k: 1.0, owns_per_path: 0.5,
reads_per_2paths: 0.5, depends_on_each: 0.5, judged_verify: 2.0,
publishes_iface: 2.0, kind_bug: 2.0}`,
`complexity.thresholds: {standard: 3.0, deep: 6.0}`.
Also a helper `tier_up(config, model)` returning the next dearer model, or the
same model when it is unknown or already the dearest.
Consumed by `complexity-score`, `dispatch-selection`, `findings-rounds`.

## Acceptance criteria
1. The new keys are present in `DEFAULTS` and survive a `ctx.yaml` override
   merge, including a project that reorders `models.tiers`.
2. `tier_up` returns the next entry for a model in `tiers`, and returns the
   input unchanged for a model absent from `tiers` — an explicit unit `model:`
   is never auto-escalated.
3. `tier_up` on the dearest tier returns it unchanged rather than raising.
4. `config.render` explains tiers, weights and the escalation flag inline, the
   way the existing block explains `briefing_chars`.
9. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
