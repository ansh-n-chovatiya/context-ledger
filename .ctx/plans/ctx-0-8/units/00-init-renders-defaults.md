---
ctx_schema: 1
unit: 00-init-renders-defaults
plan: ctx-0-8
tier: subagent
depends_on:
  - 02-config-tiers
owns:
  - ctx/cli.py
  - tests/test_init_renders_defaults.py
reads:
  - ctx/config.py
forbid: []
budget_tokens: 25000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Make `ctx init` write every `config.DEFAULTS` key into the generated `ctx.yaml`,
so a new top-level default can never again be silently dropped. This is a
pre-existing latent bug, surfaced — not caused — by wave 1 adding the first new
top-level key in the ledger's history.

## Interfaces
Consumes `config.DEFAULTS` only. Publishes nothing.
Deliberately narrow: this unit touches `cmd_init`'s `settings` construction and
nothing else in `ctx/cli.py`. `10-cli-wiring` owns the same file later for the
broad wiring pass and depends on this unit, so the two never run concurrently.

## Acceptance criteria
1. `tests/test_core.py::TestInit::test_generated_config_exposes_every_tunable`
   passes with `complexity` present in the generated `ctx.yaml`.
2. The fix removes the *class* of bug, not the instance: adding a further
   top-level key to `DEFAULTS` requires no corresponding edit in `cli.py`.
   Prove it with a test that adds a synthetic key to `DEFAULTS` and asserts it
   is rendered — not by asserting `complexity` alone.
3. Nesting is copied deeply, not shallowly. `dict(...)` on a two-level block
   like `complexity` shares its inner `weights` mapping with `DEFAULTS`, so a
   project editing its `ctx.yaml` could mutate the process-wide defaults.
4. Detected `verify` commands, `verify_candidates` carried from an existing
   ledger, the chosen `profile` and `level: "0"` all still win over the raw
   default — `ctx init` must not regress into writing `DEFAULTS` verbatim.
5. `tests/test_core.py` otherwise passes untouched, and the `ctx.yaml` header
   comment behaviour asserted by `test_config_header_does_not_quote_a_stale_cost`
   is unchanged.
6. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any
interface change (which stops the wave).
