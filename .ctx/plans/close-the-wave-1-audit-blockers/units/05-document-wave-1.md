---
ctx_schema: 1
unit: 05-document-wave-1
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on:
  - 01-verify-kinds
  - 03-ungated-is-not-done
  - 04-baseline-survives-restart
  - 06-merge-preflight
  - 07-overrides-are-journalled
owns:
  - README.md
  - CHANGELOG.md
reads:
  - path: ctx/verify.py
  - path: ctx/cli.py
  - path: ctx/contract.py
  - path: ctx/__init__.py
    symbols:
      - __version__
  - path: report.md
forbid:
  - ctx/
  - tests/
budget_tokens: 40000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 5
---

## Objective

Wave 1 adds user-visible surface: a per-check `optional: true` key, a
`ctx start --rebaseline` flag, a `status: running` a user will now see on the
board, a refusal that stops `--status done` when nothing passed, and a refusal
that stops a forged contract. The audit's documentation dimension found that the
entire 0.8.0 feature set shipped undocumented — `models:`, `complexity:`,
`kind: bug`, `test_first` and `/ctx:phase` return zero grep hits in `README.md`.
Do not repeat that on the fixes for it.

This unit documents **only** what waves 1's units actually shipped. Read the
merged code first; do not document intent from this file.

## Scope discipline

The README split into `README.md` + `docs/` is **wave 5 and out of scope here**.
Do not restructure. Add to the existing reference sections in their existing
style, and keep the diff small.

## Acceptance criteria

1. The configuration reference documents `optional: true` on a verify check: what
   it does, when to use it, and the explicit warning that it makes a check
   non-blocking when its tool is absent.
2. The CLI table documents `ctx start --rebaseline <unit>`, including why a plain
   re-run no longer re-snapshots.
3. `status: running` is documented wherever unit statuses are listed, as a status
   `ctx start` now sets.
4. The done-gate section states plainly that a gate in which no check reached PASS
   refuses the `done` transition, that `--force` overrides it, and that the `Stop`
   hook still does not block on ERROR.
5. The done-gate section states that a unit's contract is compared against its
   dispatch baseline, and that editing your own unit file or deleting a blocking
   finding is refused.
6. The two directly checkable false claims the audit found are fixed: `README.md`
   says "Seven kinds" of verify check while `verify.py` has eight — `test_first`
   is fully implemented; and the "no slash command by design" list names
   `ctx findings`, which has a complete `commands/findings.md`. Correct both
   against the code as merged.
7. `CHANGELOG.md` gains an entry for this wave naming each of the six findings
   closed, in the project's existing changelog style.
8. Every code fence and command in the sections you touched is accurate against
   the merged tree. Run the commands you document.
9. `python3 -m unittest discover -s tests` is green — including the version-drift
   check, so do not touch a version number unless the suite requires it.
10. No file outside `owns` is modified. In particular, do not edit anything under
    `ctx/` or `tests/`.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · anything
you found documented-but-wrong that you did not fix because it was out of scope.
