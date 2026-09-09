# Plan — ctx-0-8

Spec: `.ctx/specs/ctx-0-8/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units
















**Wave 1** — these may run concurrently

- `01-telemetry-fields` (subagent, done) — Teach the telemetry store to carry `model` and `role`, and to aggregate spend per role. This is A4's substrate
  - owns: ctx/telemetry.py, tests/test_telemetry.py
- `02-config-tiers` (subagent, done) — Add the ordered model-tier vocabulary, the complexity weights and thresholds, and the escalation flag to `conf
  - owns: ctx/config.py, tests/test_config_tiers.py
- `03-plan-unit-fields` (subagent, done) — Expose the unit signals the complexity score and the phase gate need: `kind`, `reproduction`, `phases`, and wh
  - owns: ctx/plan.py, tests/test_plan_fields.py
- `04-test-first-kind` (subagent, done) — Add a `test_first` verify kind that fails when the implementation snapshot precedes any captured failing run o
  - owns: ctx/verify.py, ctx/snapshot.py, tests/test_test_first.py
- `13-list-continuations` (subagent, done) — Markdown list items silently lose every line after their first. Fix it at the two sources — `frontmatter.Docum
  - owns: ctx/frontmatter.py, ctx/spec.py, tests/test_list_continuations.py

**Wave 2** — these may run concurrently

- `00-init-renders-defaults` (subagent, done) — Make `ctx init` write every `config.DEFAULTS` key into the generated `ctx.yaml`, so a new top-level default ca
  - owns: ctx/cli.py, tests/test_init_renders_defaults.py
- `05-complexity-score` (subagent, done) — Score a unit's difficulty from signals already on disk, and map the score to a tier — legibly, as a weighted s
  - owns: ctx/complexity.py, tests/test_complexity.py
- `06-phases-core` (subagent, done) — The general `phases:` mechanism, plus `kind: bug` as a preset expanding to four gated phases — so "no fix befo
  - owns: ctx/phases.py, tests/test_phases.py
- `07-review-telemetry` (subagent, done) — Give `review` the package-size and violation signals the reviewer's model choice needs, as a reusable function
  - owns: ctx/review.py, tests/test_review_telemetry.py
- `09-findings-rounds` (subagent, done) — Record round escalation durably: when a fix round fails and escalation is enabled, the ledger names the new ti
  - owns: ctx/findings.py, tests/test_findings_rounds.py, tests/fixtures/

**Wave 3** — these may run concurrently

- `08-dispatch-selection` (subagent, done) — Make `model_for` answer to the task instead of the seat: score-driven for the runner, package-driven for the r
  - owns: ctx/dispatch.py, ctx/config.py, tests/test_dispatch_selection.py

**Wave 4** — these may run concurrently

- `10-cli-wiring` (session, done) — Wire the new surfaces into the CLI and the command/agent docs: phase commands, dispatch telemetry, model/role 
  - owns: ctx/cli.py, commands/, agents/, tests/test_cli_wiring.py

**Wave 5** — these may run concurrently

- `11-proofs` (subagent, done) — Assert the three claims this project has been making without evidence. Each is a load-bearing argument for a d
  - owns: tests/test_unproven_claims.py

**Wave 6** — these may run concurrently

- `14-sibling-scope` (subagent, done) — A wave of two or more units sharing one working tree produces N−1 **false** Critical scope violations per revi
  - owns: ctx/review.py, tests/test_sibling_scope.py

## Out of scope
