# Plan — close-the-routes-around-the-gate

Spec: `.ctx/specs/close-the-routes-around-the-gate/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units

**Wave 1** — these may run concurrently

- `01-input-limits` (subagent, pending) — Cap the spec slug where every caller benefits, and make the level fail-down say so instead of demoting a proje
  - owns: ctx/spec.py, ctx/config.py, tests/test_input_limits.py

**Wave 2** — these may run concurrently

- `02-seal-and-diff-scope` (subagent, pending) — Close the four routes around the gate: re-sealing an edited contract, dispatching without a seal at all, hidin
  - owns: ctx/contract.py, ctx/cli.py, ctx/verify.py, tests/test_gate_bypass.py

## Out of scope
