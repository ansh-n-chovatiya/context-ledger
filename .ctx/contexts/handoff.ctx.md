---
ctx_bundle: 1
name: handoff
scope: project
created: 2026-09-13
project: context-ledger
tags: []
---

# Context — handoff

## Situation
Level L2 (planned). Spec: plan-preview. Plan: plan-preview. Active task/unit: none.

## Established facts
- wave 1 `01-plain-source` (subagent) — done owns ctx/plain.py, tests/test_plain_source.py, tests/test_shared_paths.py
- wave 1 `02-safe-html` (subagent) — done owns ctx/preview_html.py, tests/test_preview_html_safety.py
- wave 2 `03-view-model` (subagent) — done owns ctx/preview.py, tests/test_preview_model.py, tests/test_preview_cli.py, tests/test_shared_paths.py
- wave 3 `04-baseline-page` (subagent) — done owns ctx/preview_page.py, tests/test_preview_page.py, tests/test_shared_paths.py
- wave 4 `05-cli-wiring` (subagent) — done owns ctx/cli.py, ctx/commands.py, commands/preview.md, tests/test_preview_cli.py, tests/test_json_output.py, tests/test_commands_registry.py, tests/test_docs_currency.py, tests/test_command_bodies.py, tests/test_cli_exit_codes.py, tests/test_commands.py, tests/test_advice.py, README.md, docs/reference.md
- wave 5 `06-document-preview` (subagent) — done owns docs/reference.md, docs/walkthroughs.md, README.md, CHANGELOG.md, .claude-plugin/plugin.json, ctx/__init__.py, tests/test_ci_floor.py, .github/workflows/ci.yml
- touched `tests/test_preview_page.py`
- touched `ctx/preview_page.py`

## Decisions made
_see .ctx/decisions/_

## Open questions
_none blocking_

## Constraints

## Artifacts
- plan: `.ctx/plans/plan-preview/README.md`
- journal digest: `.ctx/journal/DIGEST.md`

## Resume here
_plan is complete_

## What the state cannot show

**The feature is done and shipped at 0.9.1, CI green across the full matrix.**
Six units, waves 1–5. `ctx preview`, `plan-check` writing the page and
scaffolding `plain.md` on every run, `ctx start`'s one advisory line.

### Four decisions, and why they went that way

**`preview.html` is written on every `plan-check`, not on demand.** The first
answer was the opposite and it was wrong for the reason the feature exists: a
page you only get by knowing the command is not available to the people who do
not know the command.

**Staleness rides a content digest, never `plan.json`'s `revision`.**
`write_graph` increments that counter on every run, so a revision scheme reports
`plain.md` stale after a re-check that changed nothing. The same counter was
later removed from the page itself for the same reason — it was quoted twice,
in the footer and in the embedded JSON, so every `plan-check` dirtied a
committed file.

**An empty field in a scaffolded form counts as unwritten.** Without it the
automatic scaffold turns every plan into filler prose that reads as authored.
This is the single most load-bearing rule in `plain.py`.

**Step titles are authored in `plain.md`'s block heading.** Deriving them from
the objective was considered and refused by the unit that would have done it: it
injects `.py` paths into the default view and fails the vocabulary rule.

### What to avoid

**Dispatch every wave through `ctx start`.** A bare Task call records no seal
and the gate refuses the unit afterwards, with no honest way to add one —
`--rebaseline` after the fact diffs finished work against itself and reviews as
approved. Unit 04 was forced for exactly this.

**Do not mark a unit `running` unless it is.** The stop hook gates the active
unit, so an idle unit left `running` fails a gate on the orchestrator's own
ledger writes.

**Amendments only count inside `## Acceptance criteria`.** The contract digest
reads that section alone; criteria added under any other heading are not part of
the sealed promise, and `--reseal` will say "its contract had not changed" —
which reads like reassurance and means the opposite.

**`--reseal` is wave-scoped.** `--reseal <unit>` from the wrong wave's brief is
ignored with a one-line `!` note.

### Open, none blocking

1. **`kind: symbol` fails open on a string `contains`.** `contains: "def foo("`
   passes for a symbol that does not exist, because the check iterates the
   string's characters. `ctx/verify.py::_check_symbol` should treat a bare
   string as one name or refuse it.
2. **`SubagentStop` was dropped in 0.8.0** without moving its registration to
   `stop.py`. Subagent work is no longer gated when a subagent finishes. Confirm
   that was deliberate.
3. **The frontmatter writer flattens what it cannot represent.** Nested
   sequences came back as Python repr strings, which is what corrupted four
   contracts this session. A round-trip assertion on `Unit.set` would make it a
   loud error at the moment of damage. Compounds with (1): the writer produced
   the broken shape and the check accepted it.
4. **`hooks._bash_targets` journals redirection targets.** A read-only command
   with `2>/dev/null` records a "write" to `/dev/null`, and heredoc bodies
   contribute fragments like `Claude-Session:`. Noise in the audit trail, and
   the same function backs the scope guard.
5. **`PREVIEW-PLAN.md` is still at the repository root** and is now fully
   implemented. `docs/history/` is where `AUDIT.md` and `PRODUCTION-AUDIT.md`
   went when they were closed.
