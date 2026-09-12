# Plan — close-the-recorded-backlog

Spec: `.ctx/specs/close-the-recorded-backlog/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units





**Wave 1** — these may run concurrently

- `01-small-correctness` (subagent, done) — Two P2 defects the prior audits recorded and nobody has contradicted: a reserved Windows device name can becom
  - owns: ctx/bundle.py, ctx/spec.py, ctx/briefing.py, tests/test_input_limits.py, tests/test_reserved_names.py, tests/test_briefing_truncation.py
- `02-lock-reclaim` (subagent, done) — Settle the stale-lock reclaim double-holder path: reproduce it and fix it, or report honestly that it does not
  - owns: ctx/lock.py, ctx/state.py, tests/test_lock_reclaim.py
- `03-logging-and-retention` (subagent, done) — Give `ctx` a way to say that something failed, and stop `keep_days: 0` meaning the committed journal grows for
  - owns: ctx/log.py, ctx/telemetry.py, ctx/journal.py, ctx/config.py, ctx/hooks.py, tests/test_logging.py, tests/test_journal_retention.py
- `04-command-surface-and-wave-gating` (subagent, done) — Give `test_first` a CLI surface, and let a wave's units gate against one suite run instead of one per unit — w
  - owns: ctx/commands.py, ctx/cli.py, ctx/verify.py, ctx/snapshot.py, tests/test_test_first_cli.py, tests/test_wave_gating.py

**Wave 2** — these may run concurrently

- `05-lint-debt-and-last-duplication` (subagent, running) — Turn on the ruff rules wave 5 parked, remove the last of the hand-built unit globs, and place the documentatio
  - owns: pyproject.toml, ctx/lock.py, ctx/paths.py, ctx/migrate.py, ctx/trust.py, ctx/commands.py, ctx/hooks.py, ctx/verify.py, ctx/config.py, docs/operations.md, docs/reference.md, docs/walkthroughs.md, tests/test_shared_paths.py

**Wave 3** — these may run concurrently

- `06-test-isolation-guard` (subagent, pending) — Make it impossible for a test to write into the real project's `.ctx/`, and find the one that did.
  - owns: tests/support.py, tests/test_isolation_guard.py
- `07-wave-scope-after-done` (subagent, pending) — Resolve the tension between two rules in `review.wave_scope` that are each correct on their own: a `done` sibl
  - owns: ctx/review.py, tests/test_wave_scope_after_done.py
- `08-test-lint-debt` (subagent, pending) — Turn on the last four ruff rules by fixing the 25 violations left in test modules, so the ignore list holds on
  - owns: pyproject.toml, tests/test_audit_wave1.py, tests/test_core.py, tests/test_flow_end_to_end.py, tests/test_plan.py, tests/test_plan_lock.py, tests/test_cli_wiring.py, tests/test_sibling_scope.py, tests/test_supply_chain.py, tests/test_ci_floor.py, tests/test_dispatch.py, tests/test_dispatch_selection.py, tests/test_gate_bypass.py, tests/test_journal_authors.py, tests/test_tier_from_the_graph.py

## Out of scope
