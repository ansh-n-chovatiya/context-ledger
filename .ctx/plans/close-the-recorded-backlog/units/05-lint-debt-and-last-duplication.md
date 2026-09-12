---
ctx_schema: 1
unit: 05-lint-debt-and-last-duplication
plan: close-the-recorded-backlog
tier: subagent
depends_on:
  - 01-small-correctness
  - 02-lock-reclaim
  - 03-logging-and-retention
  - 04-command-surface-and-wave-gating
owns:
  - pyproject.toml
  - ctx/lock.py
  - ctx/paths.py
  - ctx/migrate.py
  - ctx/trust.py
  - ctx/commands.py
  - ctx/hooks.py
  - ctx/verify.py
  - ctx/config.py
  - docs/operations.md
  - docs/reference.md
  - docs/walkthroughs.md
  - tests/test_shared_paths.py
budget_tokens: 75000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Turn on the ruff rules wave 5 parked, remove the last of the hand-built unit
globs, and place the documentation the four sibling units could not write.

## Why last
Every sibling rewrites files this unit lints. Running earlier would mean
fixing violations in code about to move — which is how a lint pass becomes
busywork that later gets reverted.

## Acceptance criteria

**The parked rules.** Wave 5 put each of these in `ignore` with its violation
sites named in a `pyproject.toml` comment, deliberately, because the files were
outside that unit's scope. They are inside yours.

1. `F401` (unused imports — `commands.py`, `hooks.py`, and six test modules),
   `F841`, `F541`, `B007`, `B904` and `E741` are **selected**, and their
   violations fixed.
2. Each fix is a real correction. **A `# noqa` is not a fix.** If a violation
   turns out to be load-bearing — the intentional `# noqa: E731` on the
   registry lambda is the precedent — it stays ignored *with its reason
   recorded*, and you say which and why.
3. Some sites may have moved or vanished: four sibling units just rewrote
   `commands.py`, `hooks.py`, `verify.py` and `config.py`. Re-derive the list
   from a real ruff run; do not trust the comment.
4. `E501` and the formatting families (`Q`, `COM`, `UP`, `PT`) stay out, for
   the reason already recorded: a rule a regeneration can break is a rule that
   gets switched off in a hurry rather than obeyed. Do not re-litigate it.
5. `ruff check ctx/ tests/` exits 0, and the version stays pinned.

**The last duplication.**
6. `Layout.unit_files()` exists, and the three modules that still glob
   `*/units/*.md` directly — `commands.py`, `migrate.py`, `trust.py` — go
   through it.
7. `tests/test_shared_paths.py`'s `UNITS_GLOB_SWEEPS` shrinks to empty, or
   documents per entry what genuinely cannot move. Its completeness assertion
   must still be able to fail — that guard caught a real case in wave 5 and
   must not be loosened to accommodate you.

**The documentation four units could not write.** Each sibling reports exact
text; take it from their reports, but **verify every claim against the code**
rather than pasting it. Wave 3 recorded that a copied-forward summary is how a
false claim arrives.
8. `docs/operations.md` documents the logging default and how to raise it
   (unit 03).
9. `docs/reference.md` and `docs/walkthroughs.md` stop describing `test_first`
   as having no CLI surface, and document the new one (unit 04).
10. `docs/reference.md` documents the `keep_days` decision (unit 03), and
    `ctx prune`'s advisory text in `commands.py` matches it.
11. Wave gating is documented if unit 04 shipped it — and **not** documented if
    unit 04 stopped and reported. Check its report; do not assume it landed.
12. The wave-5 anti-rot tests still pass: every subcommand in a CLI table,
    every `commands/*.md` in the slash table, every `config.DEFAULTS` leaf
    documented, the verify-kind count matching `len(verify.KINDS)`.

**A third instance of the device-name hole, handed over by unit 01.**
13. `ctx/lock.py` has its **own** `slugify` and builds
    `.ctx/runtime/locks/<slug>.lock`, so a plan or unit named `con` still
    produces a Windows device path. Unit 01 fixed `bundle.slugify` and
    `spec.normalise_slug` and could not touch this one — `lock.py` belonged to
    a concurrent sibling. Route it through `spec.avoid_reserved_name` (unit 01
    exported `RESERVED_DEVICE_NAMES`, `RESERVED_SUFFIX` and that function), or
    say why a lock path is exempt. A test covers a lock named `con`.
    Check whether `lock.slugify` duplicates `spec`'s logic closely enough to be
    folded in entirely rather than merely called — that is the same
    one-definition rule this unit's criterion 6 applies to unit paths.

**All.**
14. `python3 -m unittest discover -s tests -q` passes; `ctx doctor`, `ctx ci`
    and `ruff` all exit 0. `SUITE_FLOOR`/`REQUIRED_FLOOR` and the coverage
    floors stay equal to their copies — raising one is a deliberate act to
    report, not a silent adjustment.
15. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the rules you
enabled and any you left ignored with reasons · what `UNITS_GLOB_SWEEPS` holds
at the end · which sibling claims you verified against code and any you found
wrong · whether unit 04's wave gating landed.
