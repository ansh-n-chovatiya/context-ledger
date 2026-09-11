# Plan — make-ctx-tell-you-the-plan-is-slow

Spec: `.ctx/specs/make-ctx-tell-you-the-plan-is-slow/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units

**Wave 1** — these may run concurrently

- `01-plan-time-intelligence` (subagent, pending) — Make `ctx plan-check` say how much of a plan can actually run at once, what is serialising it, and where a uni
  - owns: ctx/plan.py, ctx/complexity.py, ctx/dispatch.py, ctx/commands.py, tests/test_plan_intelligence.py, tests/test_tier_from_the_graph.py
- `02-gate-warns-on-untracked` (subagent, pending) — Warn at the done-gate when a unit leaves new files untracked, because the gate runs before the commit and a wh
  - owns: ctx/verify.py, tests/test_gate_untracked_warning.py

## Out of scope
