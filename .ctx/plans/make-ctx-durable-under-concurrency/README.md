# Plan — make-ctx-durable-under-concurrency

Spec: `.ctx/specs/make-ctx-durable-under-concurrency/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units


**Wave 1** — these may run concurrently

- `01-atomic-writer` (subagent, done) — Extract the temp-file + fsync + `os.replace` write that `frontmatter.Document.write` already implements into o
  - owns: ctx/atomic.py, tests/test_atomic_writes.py
- `02-plan-scoped-lock` (subagent, done) — Generalise the `O_EXCL` lock that `state.locked` already holds correctly under 8-way contention into a named, 
  - owns: ctx/lock.py, ctx/state.py, tests/test_plan_lock.py
- `03-worktree-plan-scope` (subagent, done) — Give `worktree.path_for` the plan slug that `worktree.branch_for` already has, so `ctx worktree remove 01-api 
  - owns: ctx/worktree.py, tests/test_worktree_plan_scope.py, tests/test_worktree.py, tests/test_merge_safety.py, tests/test_git_invariant.py, tests/test_audit_merge_preflight.py, tests/test_audit_override_journal.py, tests/test_audit_wave1.py, tests/test_flow_end_to_end.py

**Wave 2** — these may run concurrently

- `04-hooks-fail-open` (subagent, pending) — Stop a teammate's newer ledger schema from putting a raw traceback and a non-zero exit on every single tool ca
  - owns: ctx/hooks.py, tests/test_hooks_fail_open.py, tests/test_policy.py
- `05-journal-durability-and-authors` (subagent, pending) — Stop `journal.prune` from being the one operation in the product that can destroy history, and stop two people
  - owns: ctx/journal.py, tests/test_journal_authors.py
- `06-ledger-locks` (subagent, pending) — Serialise the three read-modify-write paths that currently lose updates: the findings ledger between a reviewe
  - owns: ctx/findings.py, ctx/phases.py, ctx/telemetry.py, tests/test_ledger_locks.py

**Wave 3** — these may run concurrently

- `07-migration-and-snapshot-durability` (subagent, pending) — Make an interrupted migration leave a ledger that can still be migrated, and make `ctx migrate --check` stop r
  - owns: ctx/migrate.py, ctx/snapshot.py, ctx/bundle.py, ctx/contract.py, tests/test_migration_durability.py

**Wave 4** — these may run concurrently

- `08-gate-window-and-doctor` (subagent, pending) — Close the read-modify-write window that spans the done-gate and silently erases `base_branch`, and give `ctx d
  - owns: ctx/cli.py, ctx/plan.py, .ctx/.gitignore, README.md, tests/test_gate_window.py, tests/test_doctor_collisions.py, tests/test_durable_writes.py

**Wave 5** — these may run concurrently

- `09-suite-floor` (subagent, pending) — Move the CI test-count floor up with the suite. It was set at 740 against a suite of 749; the suite is now wel
  - owns: .github/workflows/ci.yml, tests/test_ci_floor.py

## Out of scope
