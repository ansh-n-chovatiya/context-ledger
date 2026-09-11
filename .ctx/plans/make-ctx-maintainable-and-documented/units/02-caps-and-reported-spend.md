---
ctx_schema: 1
unit: 02-caps-and-reported-spend
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on: []
owns:
  - ctx/cli.py
  - ctx/config.py
  - ctx/dispatch.py
  - ctx/telemetry.py
  - tests/test_wave_cap.py
  - tests/test_reported_spend.py
reads:
  - path: ctx/complexity.py
    symbols:
      - score
forbid:
  - ctx/paths.py
  - ctx/verify.py
  - ctx/snapshot.py
  - ctx/worktree.py
  - README.md
budget_tokens: 60000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Give a wave a hard unit cap alongside its token budget, and give the complexity
weights a way to be checked against something other than the reasoning that
produced them.

## Interfaces
You own `ctx/cli.py` first in this plan. Units 03, 04 and 05 own it after you,
in that order, so keep your edits additive and localised — a broad reformat here
becomes a merge problem for three later units.

## Acceptance criteria

**The wave cap (item 26, first half).**
1. `plan.max_wave_units` exists in `config.DEFAULTS` with a shipped default, and
   a wave above it is refused. Mirror how `plan.wave_budget_tokens` already
   refuses at `dispatch.py:131-136`, including the refusal's vocabulary —
   "split the wave or raise plan.max_wave_units in ctx.yaml".
2. Choose the default and **say why in your report**. The largest wave this
   project has itself dispatched is 4 units; a cap that would have refused the
   tool's own history is the wrong cap.
3. A test drives a wave one unit over the cap and asserts the refusal; another
   asserts a wave exactly at the cap dispatches. Positive control: show the
   over-cap wave dispatching before your change.

**Reported spend (item 26, second half — decided, do not redesign).**
4. `ctx telemetry --spend <tokens> --unit <name>` records what a wave actually
   cost. **Spend arrives by report, not observation** — a CLI cannot see a
   model's token usage, which is why this is a command and not a measurement.
5. **Every surface that displays spend labels it as reported and incomplete.**
   This is a criterion, not a nicety. An unlabelled partial number will be used
   to tune the complexity weights as though it were a full measurement, which is
   worse than having no number. A test asserts the label appears wherever a
   spend figure does, including when only some units in a wave reported.
6. `ctx telemetry` shows reported spend against the predicted `score` for the
   same unit, so the two can be compared — that comparison is the entire reason
   item 26 exists.
7. A unit with no reported spend is shown as unreported, never as zero. Pin it:
   zero and absent must not render the same, or the weights get checked against
   a number that means "nobody told us".
8. Telemetry still never raises and never breaks a session — the existing
   guarantee. Assert a read-only runtime directory leaves `--spend` returning
   without an exception.
9. Recording spend takes the telemetry lock unit 06 of the previous wave added;
   it is **not re-entrant**, so do not call it inside another span that holds it.

**Both.**
10. `python3 -m unittest discover -s tests -q` passes; do not lower
    `SUITE_FLOOR` or `REQUIRED_FLOOR` (pinned equal at 1190 against 1199).
11. No file outside `owns` is modified. `README.md` is unit 06's — document
    nothing there; report what needs documenting and it will be picked up.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the cap
default you chose and why · the before/after for criterion 3 · exactly how spend
is labelled, quoted · what unit 06 must document about both features.
