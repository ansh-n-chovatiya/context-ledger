---
ctx_schema: 1
unit: 05-cli-wiring
plan: plan-preview
tier: subagent
depends_on:
  - 03-view-model
  - 04-baseline-page
owns:
  - ctx/cli.py
  - ctx/commands.py
  - commands/preview.md
  - tests/test_preview_cli.py
  - tests/test_json_output.py
  - tests/test_commands_registry.py
  - tests/test_docs_currency.py
  - tests/test_command_bodies.py
  - tests/test_cli_exit_codes.py
  - tests/test_commands.py
  - tests/test_advice.py
reads:
  - path: ctx/preview.py
    symbols:
      - view_model
      - write_data
      - html_path
      - data_path
  - path: ctx/preview_page.py
    symbols:
      - render
      - write
      - check
  - path: ctx/plain.py
    symbols:
      - load
      - scaffold
      - path
  - path: tests/support.py
forbid:
  - ctx/preview.py
  - ctx/preview_page.py
  - ctx/preview_html.py
  - ctx/plain.py
budget_tokens: 70000
status: pending
verify:
  - kind: diff
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/ tests/
wave: 4
---

## Objective
Wire `ctx preview` into the CLI, make `plan-check` produce the page and the
`plain.md` form without anyone asking, and give `ctx start` one advisory line —
so a normal user gets a reviewable page without knowing the feature exists.

## Why you own seven files
Adding a subcommand to this CLI touches four tests that pin the surface, and
adding keys to `plan-check --json` touches the schema test and its golden.
**Three units blocked mid-wave this session for exactly this pattern**, so those
files are in `owns` up front rather than discovered at the gate.

`ctx/cli.py` is 538 lines and holds `build_parser`, the `COMMANDS` registry and
`main`. The 41 `cmd_*` bodies live in `ctx/commands.py`. A new command is **one
registry entry plus one body**. Follow that shape; do not grow `cli.py`.

## Acceptance criteria

**The command**
1. `ctx preview [slug]` renders and writes `preview.html`, echoing the relative
   path. With no slug it uses the active plan. With no active plan it prints the
   same guidance the other plan commands print and exits 0 — use
   `_plan_or_report`, do not write a fourth copy of that preamble.
2. `--data` writes `preview.data.json`.
3. `--check` runs `preview_page.check` against the file **on disk**, printing
   each problem and exiting non-zero when there are any.
4. `--open` uses `webbrowser.open`, and on a headless machine prints the path
   instead of raising.
5. `--scaffold-plain` writes the form via `plain.scaffold`, refusing to
   overwrite an existing `plain.md` without `--force`.
6. `--json` is supported, routed through `_emit`, and its shape added to
   `tests/test_json_output.py`.

**What runs without being asked — this is the feature's reach**
7. `ctx plan-check` writes `preview.html` on **every** run, after `plan.json`
   and the README, and echoes the path. The page is default-on deliberately: it
   exists for people who do not know the command, and a default-off artefact is
   not available to them.
8. **If rendering fails, `plan-check` still succeeds** and prints a `note:`
   line. A preview problem must never block planning. Asserted by a test that
   makes rendering raise and checks the exit code is unchanged.
9. `ctx plan-check` scaffolds `plain.md` when absent, and appends a blank block
   for any unit that has none, **never modifying authored text**. A test authors
   prose, adds a unit, re-runs `plan-check`, and asserts the prose survived.
10. `ctx plan-check --json` gains the preview path; the golden is updated in the
    same change, not left for a later unit.
11. `ctx start` prints **exactly one** new advisory line: the preview's path, or
    that it is missing or behind. No other new output, no exit-code change, no
    change to dispatch behaviour. Assert the line count, not just its presence.

**The slash command**
12. `commands/preview.md` follows house frontmatter (`description`,
    `allowed-tools`, `argument-hint`), shells out via
    `"${CLAUDE_PLUGIN_ROOT}/bin/ctx" preview $ARGUMENTS || true`, and appears in
    the slash-command table that `tests/test_docs_currency.py` checks.
13. Only `$ARGUMENTS` expands — no other `$VAR` substitution — and **no `!` line
    may exit non-zero**, which kills the command. That is why `|| true` is not
    optional.

**Not breaking what exists**
14. `tests/test_command_bodies.py` pins `EXPECTED_BODIES = 41` in three
    assertions. You are adding the 42nd command, so that constant moves to 42 —
    this is a *correct* change, and it is in your `owns` because `plan-check`'s
    ownership-gap report flagged it before dispatch. Say in your report that you
    moved it and why.
15. `tests/test_cli_exit_codes.py` holds `ORDINARY`, the commands that must exit
    2 on a refusal with no ledger. Decide whether `preview` belongs there, add
    it if so, and say which you chose. `tests/test_commands.py` validates every
    `commands/*.md`; your new file comes under it automatically — if it needs no
    edit, say that rather than leaving it unmentioned.
16. `tests/test_advice.py` asserts `cli._PLAN_ARGUMENT == READ_BEFORE` by exact
    equality. `ctx preview [slug]` takes the plan as its positional `name`, like
    `plan-check` and `start`, so you add a row to `_PLAN_ARGUMENT` and must add
    the matching row to `READ_BEFORE`. Getting this wrong the other way —
    reading `--plan` instead of `name` — would make `ctx preview 01-api` look
    for a plan called `01-api`, which is the exact bug that table exists to
    prevent. Add the comment line in the house style.
17. Every existing test passes. **Any assertion you change is reported with what
    it was pinning and why the new value is correct** — never quietly edited.
    `test_commands_registry.py` pins all 41 commands against a generated
    fixture; adding the 42nd legitimately changes it, and saying so is the
    point.
18. `SUITE_FLOOR` and `REQUIRED_FLOOR` stay **equal** and are not lowered;
    coverage floors likewise stay pinned in pairs. Raising one alone is red by
    design.
19. Python 3.8 compatible, standard library only, `ruff` clean across `ctx/` and
    `tests/`.
20. No file outside `owns` is modified. If you need `preview.py`,
    `preview_page.py`, `plain.py` or `preview_html.py` changed, **stop and
    report the exact edit** — do not widen scope. Those units are done and
    their interfaces are frozen.
21. Every test is shown failing before it passes. Report the failure output.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim verify
output · **every existing assertion you changed, with what it was pinning** ·
any interface you needed from a sibling and could not have.
