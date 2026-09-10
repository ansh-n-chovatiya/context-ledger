# Plan — close-the-wave-1-audit-blockers

Spec: `.ctx/specs/close-the-wave-1-audit-blockers/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units


**Wave 1** — these may run concurrently

- `01-verify-kinds` (subagent, pending) — Close two audit findings that both live in `ctx/verify.py`: **(a) Real regressions are laundered as infrastruc
  - owns: ctx/verify.py, tests/test_audit_verify_kinds.py, tests/test_gates.py
- `02-probe-isolation` (subagent, pending) — Close the audit's only remote-code-execution finding. `_availability(command)` in `ctx/cli.py` decides whether
  - owns: ctx/cli.py, tests/test_audit_probe_isolation.py

**Wave 2** — these may run concurrently

- `03-ungated-is-not-done` (subagent, pending) — Make `status: done` mean a check actually passed. Two findings, both landing in `_gate_before_done`. **(a) An 
  - owns: ctx/cli.py, ctx/hooks.py, ctx/contract.py, tests/test_audit_ungated_gate.py

**Wave 3** — these may run concurrently

- `04-baseline-survives-restart` (subagent, pending) — Stop a re-run of `ctx start` from destroying the evidence `ctx review` judges against. `cmd_start` calls `revi
  - owns: ctx/cli.py, ctx/review.py, ctx/snapshot.py, tests/test_audit_review_baseline.py

**Wave 4** — these may run concurrently

- `05-document-wave-1` (subagent, pending) — Wave 1 adds user-visible surface: a per-check `optional: true` key, a `ctx start --rebaseline` flag, a `status
  - owns: README.md, CHANGELOG.md

## Out of scope
