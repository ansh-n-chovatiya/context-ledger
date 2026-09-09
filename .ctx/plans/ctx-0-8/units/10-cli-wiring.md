---
ctx_schema: 1
unit: 10-cli-wiring
plan: ctx-0-8
tier: session
depends_on:
  - 00-init-renders-defaults
  - 06-phases-core
  - 08-dispatch-selection
  - 09-findings-rounds
  - 01-telemetry-fields
  - 04-test-first-kind
owns:
  - ctx/cli.py
  - commands/
  - agents/
  - tests/test_cli_wiring.py
reads:
  - ctx/phases.py
  - ctx/dispatch.py
  - ctx/telemetry.py
  - ctx/complexity.py
forbid: []
budget_tokens: 60000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 4
---

## Objective
Wire the new surfaces into the CLI and the command/agent docs: phase commands, dispatch telemetry, model/role on the existing review event, and per-role spend in `ctx telemetry`.

## Interfaces
Consumes everything above. This unit exists so that exactly one unit owns
`ctx/cli.py`, which every other unit would otherwise collide on.
Tier `session` because it touches the widest surface and is the one place a
wrong edit is expensive.

## Acceptance criteria
1. `cmd_start` records a dispatch telemetry event per dispatched unit,
   carrying model, role and the complexity score.
2. The existing `review` record at `cli.py:1340` gains `model` and `role`; its
   `bytes`/`round`/`out_of_scope` fields are untouched.
3. `ctx telemetry` reports spend per role.
4. Phase commands advance and refuse per `phases.can_enter`, surfacing the
   refusal reason verbatim — including the refusal a `bug` unit gets when it
   tries to enter `fix` with no failing reproduction recorded.
5. `commands/` and `agents/` docs describe the new behaviour; no doc claims a
   capability the code does not have.
6. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
