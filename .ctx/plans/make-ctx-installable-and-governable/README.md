# Plan — make-ctx-installable-and-governable

Spec: `.ctx/specs/make-ctx-installable-and-governable/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units

**Wave 1** — these may run concurrently

- `01-packaging` (subagent, pending) — Make "we run ctx 0.8.0" a verifiable statement: real packaging metadata, and a release workflow that produces 
  - owns: pyproject.toml, .github/workflows/release.yml, tests/test_packaging.py
- `02-policy-and-trust-lock` (subagent, pending) — Give the gate a control plane a repository cannot overrule, make disabling it leave a record, and give CI a wa
  - owns: ctx/config.py, ctx/cli.py, ctx/trust.py, tests/test_policy.py
- `03-supply-chain` (subagent, pending) — Pin what CI runs, constrain what it can do, and give the project the governance files a vendor-intake question
  - owns: .github/workflows/ci.yml, SECURITY.md, CODEOWNERS, .github/dependabot.yml, tests/test_supply_chain.py

**Wave 2** — these may run concurrently

- `04-docs-currency` (subagent, pending) — Make the README describe the tool that now exists: a safe CI recipe, the new exit-code and gate behaviour, and
  - owns: README.md, CHANGELOG.md

## Out of scope
