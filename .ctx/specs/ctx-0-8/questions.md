---
ctx_schema: 1
spec: ctx-0-8
---

## Blocking






<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] Tier vocabulary: models are bare strings with no ordering, so 'one tier up' is undefined. How does 0.8 introduce ordering, and who maintains it as model names change?
- [x] Weights and thresholds: what weight does each complexity signal carry, what values separate the tiers, and are they shipped defaults or per-project?
- [x] bug unit kind vs a phases list any unit can declare — the second is more general and may subsume the first.
- [x] Is escalation actually cheaper? A3 assumes one escalated round beats two more cheap ones. Should A3 ship behind a config flag, default off, until telemetry says?
- [x] G7 harness portability: decline outright, or keep it open? Deciding not to do it is also an answer and should be recorded as an ADR.
- [x] Escalation and trust: does an automatic runner escalation interact with trust.py per-command acceptance? Confirm an escalated dispatch cannot widen what a machine has agreed to run.

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved






- Tier vocabulary: models are bare strings with no ordering, so 'one tier up' is undefined. How does 0.8 introduce ordering, and who maintains it as model names change? → An ordered models.tiers list in config.DEFAULTS, cheapest-first: [haiku, sonnet, opus]. Ordering is position in the list; 'one tier up' is the next entry. Keeps criterion 12 (no model name outside config.DEFAULTS). A project reorders or extends it in ctx.yaml. A model NOT in the list — an explicit unit-level model: — is never auto-escalated, because the author chose it deliberately. (2026-09-09)
- Weights and thresholds: what weight does each complexity signal carry, what values separate the tiers, and are they shipped defaults or per-project? → Shipped defaults in config.DEFAULTS, overridable per-project in ctx.yaml. weights: budget_per_15k 1.0, owns_per_path 0.5, reads_per_2paths 0.5, depends_on_each 0.5, judged_verify 2.0, publishes_iface 2.0, kind_bug 2.0; thresholds standard 3.0, deep 6.0. The dispatch line prints the score and every contributing input so a wrong tier is diagnosable without a re-run. Values are reasoned, not yet measured — A4 telemetry is what tunes them. (2026-09-09)
- bug unit kind vs a phases list any unit can declare — the second is more general and may subsume the first. → Both: implement phases: as the general mechanism any unit may declare, and ship kind: bug as a named preset expanding to the four gated phases (reproduce/locate/fix/guard) with a required reproduction command. Generality without losing the machine fact that no fix may precede a failing reproduction. (2026-09-09)
- Is escalation actually cheaper? A3 assumes one escalated round beats two more cheap ones. Should A3 ship behind a config flag, default off, until telemetry says? → Ship A3 fully implemented and tested but gated on models.escalate_on_failed_round, default false. The mechanism is proven by tests with the flag on; nobody pays for an unmeasured hypothesis until A4 telemetry says it pays. (2026-09-09)
- G7 harness portability: decline outright, or keep it open? Deciding not to do it is also an answer and should be recorded as an ADR. → Decline. Eight harness variants multiply the maintenance surface and no P0/P1 value depends on it. Recorded as an ADR via ctx decide so the refusal is durable and reversible on evidence. (2026-09-09)
- Escalation and trust: does an automatic runner escalation interact with trust.py per-command acceptance? Confirm an escalated dispatch cannot widen what a machine has agreed to run. → No interaction, and it gets a test rather than a paragraph. trust.py keys acceptance on the command string (hash of run), while escalation changes only the model on a dispatch line. An escalated dispatch runs no command the unaccelerated one would not. Asserted by a test that escalates a unit and diffs the trust store and the resolved verify command set. (2026-09-09)
