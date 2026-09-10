---
ctx_schema: 1
unit: 04-baseline-survives-restart
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on:
  - 03-ungated-is-not-done
owns:
  - ctx/cli.py
  - ctx/review.py
  - ctx/snapshot.py
  - ctx/contract.py
  - tests/test_audit_review_baseline.py
  - tests/test_audit_ungated_gate.py
  - tests/test_flow_end_to_end.py
reads:
  - path: ctx/contract.py
    symbols:
      - digest
      - baseline
      - compare
  - path: ctx/dispatch.py
    symbols:
      - prepare
      - instructions
      - prepare_worktrees
  - path: ctx/plan.py
    symbols:
      - find_unit
      - apply_waves
      - Unit
  - path: ctx/state.py
    symbols:
      - update
      - load
  - path: tests/support.py
  - path: report.md
forbid:
  - ctx/verify.py
  - ctx/hooks.py
  - ctx/worktree.py
budget_tokens: 70000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 3
---

## Objective

Stop a re-run of `ctx start` from destroying the evidence `ctx review` judges
against.

`cmd_start` calls `review_mod.capture_before(...)` unconditionally for every unit
in the wave, and `snapshot.capture` begins with
`shutil.rmtree(directory, ignore_errors=True)`. Meanwhile `ctx start` never sets
`status: running` — a unit stays `pending` until an explicit `--status done`. So
the documented crash-recovery path ("run `/ctx:start` again") re-snapshots units
that are mid-flight or already finished, over their completed state. `ctx review`
then diffs post-work against post-work, hands the reviewer an empty package, and
gets `verdict: approved` for work nobody looked at. `commands/review.md` warns
against exactly this in prose, with nothing enforcing it.

## The seal is the same finding

`03-ungated-is-not-done` added `contract.seal(...)` in `cmd_start`, on the line
after `review_mod.capture_before(...)`. It overwrites unconditionally, exactly as
`snapshot.capture` does. So a re-run of `ctx start` does not merely destroy the
review baseline — it **re-seals a forged contract as legitimate**, silently
undoing the forgery refusal that just shipped. Whatever guard you put around the
re-capture must cover the seal on the next line. They are one finding with two
call sites, and fixing only the visible half leaves the more dangerous one open.

Two things to know before you touch it, from the unit that wrote it:

- `seal()` deliberately preserves the existing `findings` block across a re-seal,
  because a fix round is still answerable to the findings that caused it. Do not
  fix the re-capture by skipping the whole call for a unit that already has a
  seal without deciding what round 2 should do.
- `contract.baseline` falls back to `snapshot.stored_text(before_key, unit path)`
  when no seal exists, and `seal()` fills those bytes in via
  `snapshot.store_extra` — so the before-snapshot now carries the unit file and
  its findings file too. A guard on `snapshot.capture` therefore has reach into
  the forgery path whether you intend it or not.

## Decisions already taken (do not relitigate)

- `ctx start` **does** set `status: running` on the units it dispatches.
- A re-run reports already-dispatched units as in flight and does **not**
  re-snapshot them.
- An explicit `--rebaseline` deliberately re-captures a named unit, for the real
  crash case where the baseline genuinely should be retaken.
- `running` is a status no code path has ever set. The board (`_list_units`,
  `cmd_status`), the next-action advisor (`_next_action`) and the gate must each
  be checked against it — that check is part of this unit, not a follow-up.

## Interfaces

Consumes, unchanged, from `03-ungated-is-not-done`:
`contract.digest(unit)`, `contract.compare(layout, slug, unit)`, and
`_gate_before_done(layout, config, slug, unit)` → `int | None`.

Produces:

- `review.capture_before(layout, config, unit, slug, root, force=False)` — the new
  keyword defaults to `False` and refuses to overwrite an existing baseline.
- `snapshot.capture(layout, config, key, root, content_paths=(), force=False)` —
  same defaulting. Existing callers that pass no `force` keep working.
- `ctx start --rebaseline <unit>` — repeatable, names units to re-capture.

## Acceptance criteria

1. Running `ctx start` twice for the same wave does not overwrite an existing
   `before` snapshot for any unit that already has one. Assert on the stored
   manifest bytes or its digest, not merely on mtime.
1b. Nor does it re-seal the contract. A unit whose contract was forged after
    dispatch is still refused at the gate after a second `ctx start` — assert the
    refusal, and name the changed field, exactly as the unforged re-run case
    passes. This is the criterion that keeps the forgery fix from being undone by
    the documented crash-recovery step.
2. After that second run, `ctx review` for a unit with real changes still diffs
   against the pre-work baseline and produces a non-empty package. Assert the
   package is non-empty and names a changed file — an empty package that reviews
   as `approved` is the failure this unit exists to prevent.
3. `ctx start` sets `status: running` on each unit it dispatches, and the unit
   file on disk reflects it.
4. A second `ctx start` reports the already-dispatched units as in flight, by
   name, and says how to deliberately re-baseline one.
5. `ctx start --rebaseline <unit>` re-captures exactly that unit's baseline **and
   re-seals its contract**, for that unit only, and journals that it did so. After
   a deliberate re-baseline the unit's current contract becomes the new promise —
   that is what makes it an escape hatch rather than a loophole, so the journal
   entry must record it plainly enough to be audited later.
5b. A re-seal preserves the existing `findings` block, so a unit re-baselined for
    a second round is still answerable to the findings that sent it back.
6. `ctx unit <name> --status done` still works on a unit in `running`, and the
   gate from `03` still applies to it unchanged.
7. `_next_action` gives sensible advice for a plan whose units are `running`
   rather than `pending` — it must not report the wave as undispatched, and must
   not loop advising `ctx start` forever.
8. `cmd_status` and the wave board render `running` units distinctly from
   `pending` and `done`.
9. `dispatch.prepare` still selects the right units for a wave now that
   dispatched ones are `running` — a wave already dispatched must not be reported
   as "nothing left to dispatch" when a unit legitimately needs re-dispatching
   after a failed gate.
10. A snapshot that fails with `OSError` still does not block the dispatch — the
    existing "a snapshot must never block a dispatch" behaviour is preserved.
11. `tests/test_audit_review_baseline.py` covers criteria 1-9 with positive
    controls: notably, a test proving `--rebaseline` *does* replace the manifest,
    so criterion 1's refusal is shown to be a real guard rather than a capture
    that silently stopped happening.
12. `python3 -m unittest discover -s tests` is green. Criterion 3 makes `running`
    a value that did not exist when the suite was written, and eight assertions
    use `pending` as a synonym for "the transition was refused". Amending exactly
    those eight is authorised, and nothing else:
    - `tests/test_audit_ungated_gate.py:169` — assert `status: done` is absent
      rather than asserting `status: pending` is present.
    - `tests/test_audit_ungated_gate.py` lines 340, 352, 364, 489, 503 — assert
      the status is not `done` rather than that it equals `pending`.
    - `tests/test_flow_end_to_end.py:161` and `:228` — expect `running`.
    Prefer "is not done" over "is running" wherever the assertion's intent is
    that the transition was refused: that intent is what the test is for, and it
    survives the next status this product grows. Every other test in
    `tests/test_review.py`, `tests/test_dispatch.py` and
    `tests/test_dispatch_selection.py` must pass untouched.
13. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · every
place `running` required a behaviour change that was not listed above · any
interface you were forced to change.
