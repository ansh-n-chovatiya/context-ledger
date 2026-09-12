# Plan — plan-preview

Spec: `.ctx/specs/plan-preview/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units




**Wave 1** — these may run concurrently

- `01-plain-source` (subagent, pending) — `ctx/plain.py`: read the authored plain-language file for a plan, say precisely which parts a human has actual
  - owns: ctx/plain.py, tests/test_plain_source.py
- `02-safe-html` (subagent, pending) — `ctx/preview_html.py`: the security-critical primitives that turn untrusted prose into HTML that is safe to do
  - owns: ctx/preview_html.py, tests/test_preview_html_safety.py

**Wave 2** — these may run concurrently

- `03-view-model` (subagent, pending) — `ctx/preview.py`: assemble the one JSON-safe dict that every rendered page is built from, so the HTML never se
  - owns: ctx/preview.py, tests/test_preview_model.py

**Wave 3** — these may run concurrently

- `04-baseline-page` (subagent, pending) — `ctx/preview_page.py`: render the view-model into one self-contained HTML page that a non-technical person can
  - owns: ctx/preview_page.py, tests/test_preview_page.py

**Wave 4** — these may run concurrently

- `05-cli-wiring` (subagent, pending) — Wire `ctx preview` into the CLI, make `plan-check` produce the page and the `plain.md` form without anyone ask
  - owns: ctx/cli.py, ctx/commands.py, commands/preview.md, tests/test_preview_cli.py, tests/test_json_output.py, tests/test_commands_registry.py, tests/test_docs_currency.py, tests/test_command_bodies.py, tests/test_cli_exit_codes.py, tests/test_commands.py, tests/test_advice.py

**Wave 5** — these may run concurrently

- `06-document-preview` (subagent, pending) — Document what actually shipped, and ship it — reference, walkthrough, changelog, the two version numbers that 
  - owns: docs/reference.md, docs/walkthroughs.md, README.md, CHANGELOG.md, .claude-plugin/plugin.json, ctx/__init__.py, tests/test_ci_floor.py, .github/workflows/ci.yml

## Out of scope
