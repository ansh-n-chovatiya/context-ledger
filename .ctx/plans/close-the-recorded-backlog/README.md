# Plan — close-the-recorded-backlog

Spec: `.ctx/specs/close-the-recorded-backlog/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units


**Wave 1** — these may run concurrently

- `01-small-correctness` (subagent, pending) — Two P2 defects the prior audits recorded and nobody has contradicted: a reserved Windows device name can becom
  - owns: ctx/bundle.py, ctx/spec.py, ctx/briefing.py, tests/test_input_limits.py, tests/test_reserved_names.py, tests/test_briefing_truncation.py
- `02-lock-reclaim` (subagent, pending) — Settle the stale-lock reclaim double-holder path: reproduce it and fix it, or report honestly that it does not
  - owns: ctx/lock.py, ctx/state.py, tests/test_lock_reclaim.py
- `03-logging-and-retention` (subagent, pending) — Give `ctx` a way to say that something failed, and stop `keep_days: 0` meaning the committed journal grows for
  - owns: ctx/log.py, ctx/telemetry.py, ctx/journal.py, ctx/config.py, ctx/hooks.py, tests/test_logging.py, tests/test_journal_retention.py
- `04-command-surface-and-wave-gating` (subagent, pending) — Give `test_first` a CLI surface, and let a wave's units gate against one suite run instead of one per unit — w
  - owns: ctx/commands.py, ctx/cli.py, ctx/verify.py, ctx/snapshot.py, tests/test_test_first_cli.py, tests/test_wave_gating.py

**Wave 2** — these may run concurrently

- `05-lint-debt-and-last-duplication` (subagent, pending) — Turn on the ruff rules wave 5 parked, remove the last of the hand-built unit globs, and place the documentatio
  - owns: pyproject.toml, ctx/paths.py, ctx/migrate.py, ctx/trust.py, ctx/commands.py, ctx/hooks.py, ctx/verify.py, ctx/config.py, docs/operations.md, docs/reference.md, docs/walkthroughs.md, tests/test_shared_paths.py

## Out of scope
