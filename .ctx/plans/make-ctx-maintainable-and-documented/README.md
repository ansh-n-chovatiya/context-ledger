# Plan — make-ctx-maintainable-and-documented

Spec: `.ctx/specs/make-ctx-maintainable-and-documented/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units


**Wave 1** — these may run concurrently

- `01-shared-paths-and-dead-code` (subagent, done) — Put the three copies of `LEDGER_PREFIX` and the five hand-built unit-file paths behind the single definitions 
  - owns: ctx/paths.py, ctx/snapshot.py, ctx/worktree.py, ctx/briefing.py, ctx/work.py, ctx/hooks.py, tests/test_shared_paths.py
- `02-caps-and-reported-spend` (subagent, done) — Give a wave a hard unit cap alongside its token budget, and give the complexity weights a way to be checked ag
  - owns: ctx/cli.py, ctx/config.py, ctx/dispatch.py, ctx/telemetry.py, tests/test_wave_cap.py, tests/test_reported_spend.py

**Wave 2** — these may run concurrently

- `03-verify-dispatch-and-gate` (subagent, done) — Make a new verify kind one dict entry instead of four coordinated edits, and move the two gate-orchestration f
  - owns: ctx/verify.py, ctx/cli.py, tests/test_verify_kinds_table.py, tests/test_contract_digest_agreement.py

**Wave 3** — these may run concurrently

- `04-detect-and-advice` (subagent, done) — Three of the five extractions: the ecosystem detector, the product's decision tree, and the five-way duplicate
  - owns: ctx/detect.py, ctx/complexity.py, ctx/advice.py, ctx/cli.py, tests/test_detect.py, tests/test_advice.py, tests/test_kind_table_reaches_complexity.py

**Wave 4** — these may run concurrently

- `05-commands-registry-and-json` (subagent, running) — Turn `build_parser` into a registry, then use it to add `--json` in one place instead of forty-one — the pairi
  - owns: ctx/cli.py, tests/test_commands_registry.py, tests/test_json_output.py, tests/test_no_import_cycles.py

**Wave 5** — these may run concurrently

- `08-command-bodies` (subagent, pending) — Move the 41 `cmd_*` bodies out of `cli.py` into `ctx/commands.py`, so `cli.py` is the entry point and the regi
  - owns: ctx/cli.py, ctx/commands.py, tests/test_shared_paths.py, tests/test_command_bodies.py

**Wave 6** — these may run concurrently

- `06-docs-split-and-currency` (subagent, pending) — Document the tool that exists after this wave, split the 1,575-line README into something maintainable, and ma
  - owns: README.md, docs/, AUDIT.md, PRODUCTION-AUDIT.md, GUIDE.md, commands/trust.md, tests/test_docs_currency.py

**Wave 7** — these may run concurrently

- `07-ruff-and-coverage` (subagent, pending) — The two checks deferred from wave 2 to wave 3 and from wave 3 to here, now that `pyproject.toml` exists to con
  - owns: pyproject.toml, .github/workflows/ci.yml, tests/test_ci_floor.py, tests/test_lint_and_coverage.py

## Out of scope
