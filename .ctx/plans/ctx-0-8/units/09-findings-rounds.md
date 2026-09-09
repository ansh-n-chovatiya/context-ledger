---
ctx_schema: 1
unit: 09-findings-rounds
plan: ctx-0-8
tier: subagent
depends_on:
  - 02-config-tiers
owns:
  - ctx/findings.py
  - tests/test_findings_rounds.py
  - tests/fixtures/
reads:
  - ctx/config.py
forbid: []
budget_tokens: 35000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Record round escalation durably: when a fix round fails and escalation is enabled, the ledger names the new tier and why, so the cost is visible rather than inferred.

## Interfaces
Consumes `config.tier_up`. Produces a recorded escalation on the findings
ledger — round number, the tier moved from and to, and the reason — readable by
`ctx findings` and written to the journal.
Must not add an `acknowledged`-shaped state: the escalation is a fact about a
round, never a status a finding can sit in.

## Acceptance criteria
1. A failed round with escalation enabled records the tier change and its
   reason; the journal entry names both.
2. With escalation disabled nothing is recorded and behaviour is byte-identical
   to 0.7.0.
3. `MAX_ROUNDS` still caps the loop at 3 — escalation changes the seat, never
   the ceiling.
4. `STATUSES` is unchanged and the no-acknowledgement refusal at `findings.py`
   still rejects `ack`/`noted`/`agreed`.
9. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
