---
ctx_schema: 1
unit: 11-proofs
plan: ctx-0-8
tier: subagent
depends_on:
  - 10-cli-wiring
owns:
  - tests/test_unproven_claims.py
reads:
  - ctx/review.py
  - ctx/snapshot.py
  - ctx/findings.py
  - ctx/trust.py
  - ctx/dispatch.py
forbid: []
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 5
---

## Objective
Assert the three claims this project has been making without evidence. Each is a load-bearing argument for a design decision already shipped.

## Interfaces
Consumes the finished system. Adds no production code — if a proof cannot be
written without changing `ctx/`, that is a finding to report, not a licence to
edit outside `owns`.

## Acceptance criteria
1. **Disjoint packages.** Two units in one wave, run concurrently, produce two
   review packages with disjoint contents. The case for snapshots over commit
   ranges rests on this and it is currently unproven.
2. **Resume mid-fix-loop.** A session killed mid-loop resumes with the round
   number, the open findings and the unit under review intact. Kill it for
   real — do not simulate the interruption by calling the resume path directly.
3. **Escalation cannot widen trust.** Escalating a unit's model leaves the
   trust store and the resolved verify-command set byte-identical. This is the
   answer to open question 6, asserted rather than argued.
10. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
