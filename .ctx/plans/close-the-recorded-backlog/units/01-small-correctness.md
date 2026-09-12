---
ctx_schema: 1
unit: 01-small-correctness
plan: close-the-recorded-backlog
tier: subagent
depends_on: []
owns:
  - ctx/bundle.py
  - ctx/spec.py
  - ctx/briefing.py
  - tests/test_input_limits.py
  - tests/test_reserved_names.py
  - tests/test_briefing_truncation.py
reads:
  - path: ctx/paths.py
    symbols:
      - Layout
forbid:
  - ctx/commands.py
  - ctx/journal.py
  - ctx/lock.py
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Two P2 defects the prior audits recorded and nobody has contradicted: a
reserved Windows device name can become a ledger path, and a briefing can come
back empty while reporting that nothing was truncated.

## Acceptance criteria

**Reserved device names.** `bundle.slugify` lowercases and strips to
`[a-z0-9-]`, so `con`, `nul`, `prn`, `aux`, `com1`–`com9` and `lpt1`–`lpt9`
survive intact — and on Windows `.ctx/tasks/con.md` is not a file, it is the
console device. The audit named `bundle.py`; `spec.normalise_slug` is in your
`owns` because it may have the same hole.

1. None of those names, with or without an extension, can become a ledger
   filename. Pick a mitigation that keeps the name recognisable — a suffix
   reads better than a hash here, since the user typed a real word.
2. Check `spec.normalise_slug` for the same hole and fix it if present. Say in
   your report which of the two had it.
3. A test asserts `ctx task «con»` and a bundle named `con` both produce a
   usable path, and that it holds on any platform — the test must not skip on
   non-Windows, or it never runs in this project's CI matrix except on one job.
4. Existing slug behaviour for ordinary names is unchanged, including the
   80-byte cap and the sha256 suffix a previous wave added.
   `tests/test_input_limits.py` is in your `owns` because `plan-check`'s new
   ownership-gap report flagged it as pinning `bundle.py` — it is the test most
   likely to constrain a slug change. Change a call, never an assertion: if an
   assertion has to move, that is a finding for your report.

**The empty briefing.** `briefing._fit` (line ~199) returns `""` when the cap
is too small for even one block plus the marker, and `measure()` then reports
`truncated: False` — so a caller cannot distinguish "nothing to say" from
"everything was dropped".

5. When content was dropped, the caller can tell. Either the marker survives at
   any cap, or `measure()` reports it truncated; state which you chose and why.
6. `cap <= 0` keeps returning an empty string with no marker — that is L0's
   documented "no briefing" case, not a truncation, and conflating them would
   make `ctx doctor` report a truncation on every L0 project.
7. A test covers a cap too small for one block, a cap that fits exactly one,
   and `cap <= 0`, asserting what `measure()` reports in each.

**Both.**
8. Contracts in this wave are lighter by design: these are corrections, so a
   test asserting the fixed behaviour is enough. No before/after reproduction
   required.
9. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. Do not move any floor.
10. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · which of `bundle`
and `spec` had the device-name hole · your choice for criterion 5 and why.
