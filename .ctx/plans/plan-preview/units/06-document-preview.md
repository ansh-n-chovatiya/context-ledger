---
ctx_schema: 1
unit: 06-document-preview
plan: plan-preview
tier: subagent
depends_on:
  - 01-plain-source
  - 02-safe-html
  - 03-view-model
  - 04-baseline-page
  - 05-cli-wiring
owns:
  - docs/reference.md
  - docs/walkthroughs.md
  - README.md
  - CHANGELOG.md
  - .claude-plugin/plugin.json
  - ctx/__init__.py
  - tests/test_ci_floor.py
  - .github/workflows/ci.yml
reads:
  - path: ctx/preview.py
  - path: ctx/preview_page.py
  - path: ctx/plain.py
  - path: ctx/commands.py
  - path: commands/preview.md
forbid:
  - ctx/cli.py
  - ctx/preview.py
  - ctx/preview_page.py
  - ctx/preview_html.py
  - ctx/plain.py
budget_tokens: 45000
status: pending
verify:
  - kind: diff
  - kind: exists
    path: docs/reference.md
    matches: ctx preview
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/ tests/
wave: 5
---

## Objective
Document what actually shipped, and ship it — reference, walkthrough, changelog,
the two version numbers that must move together, and the suite floor that should
move with the suite.

## Acceptance criteria
1. `docs/reference.md` documents `ctx preview`, every flag, and `plain.md`'s
   format — the five plan sections, the `## Unit:` block, the five bolded
   fields, and that an empty field counts as unwritten.
2. `docs/reference.md` states that `plan-check` writes `preview.html` on every
   run and scaffolds `plain.md`, and that staleness is measured by a content
   digest rather than `plan.json`'s revision counter — the counter moves on
   every run and would cry wolf.
3. `docs/walkthroughs.md` shows the flow end to end: plan → `plan-check` →
   fill in `plain.md` → `ctx preview --open` → hand the file to a reviewer.
4. **README gains at most a few lines and a link.** It is 299 lines by
   deliberate decision and reference material does not go there.
5. CHANGELOG gains a 0.9.0 entry describing the feature in the file's existing
   voice.
6. Version bumped to 0.9.0 in **both** `ctx/__init__.py` and
   `.claude-plugin/plugin.json`. A `ctx` change shipped without bumping
   `plugin.json` leaves every installed copy stale — this has happened here.
7. **Every claim is verified against the code, not against a sibling's report.**
   Run each command you document and paste what it printed. A report that says a
   flag exists is not evidence the flag exists.
8. `ctx doctor` output and the briefing budget are unchanged by this feature —
   run `ctx doctor` and `ctx briefing` and say so.
9. **Raise the suite floor with the suite.** `REQUIRED_FLOOR` in
   `tests/test_ci_floor.py` is 1420 and its own comment says it "sits just under
   the real count on purpose — what a floor exists to catch is a silent DROP,
   and a floor far below the real count cannot see one." This plan adds a large
   number of tests, so measure the real count and raise the floor to just under
   it.
10. `REQUIRED_FLOOR` and `SUITE_FLOOR` in `.github/workflows/ci.yml` are one
    decision in two files. **Raise them together** — `test_the_two_floors_agree`
    fails if either moves alone, and that is the point. Never lower either.
11. Do **not** touch the coverage floors. `REQUIRED_LINE_FLOOR` (91.0) and
    `REQUIRED_BRANCH_FLOOR` (85.0) are held equal to the workflow's pair by
    assertion. If coverage dropped, the fix is tests, not a lower floor — and
    that is a finding for your report, not something to quietly adjust.
12. No file outside `owns` is modified.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim output
of every command you documented · confirmation that both version numbers moved.
