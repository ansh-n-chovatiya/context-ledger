---
ctx_schema: 1
unit: 07-wave-scope-after-done
plan: close-the-recorded-backlog
tier: subagent
depends_on:
  - 05-lint-debt-and-last-duplication
owns:
  - ctx/review.py
  - tests/test_wave_scope_after_done.py
reads:
  - path: ctx/plan.py
    symbols:
      - load_units
      - next_wave
  - path: ctx/verify.py
    symbols:
      - gate_check
forbid:
  - tests/support.py
  - ctx/verify.py
  - ctx/commands.py
budget_tokens: 55000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Resolve the tension between two rules in `review.wave_scope` that are each
correct on their own: a `done` sibling is not running, but its uncommitted work
is still in the tree.

## This is a tension, not an oversight — read `wave_scope`'s docstring first
The exclusion of `done` siblings is **deliberate and documented**. The
docstring explains the whole design: `snapshot.capture` fingerprints the entire
project on purpose, so a concurrent sibling's legitimate write to its own
declared path would otherwise read as a scope violation — mechanical, Critical,
gate-blocking — and *"every wave of two or more units deadlocked its own gate
on N−1 false Criticals"*. The scope is widened "by exactly the amount
concurrency costs it, and no more", and a `done` unit is excluded because it is
no longer writing.

What unit 04 found while testing wave gating: gate a wave one unit at a time
and the first unit becomes `done` **while its work is still uncommitted**.
Units two and three then see those files change and fail their own `diff`
check on a sibling's legitimate, finished work.

So both rules are right and they meet at a case neither anticipated.

## Acceptance criteria
1. Gating a wave one unit at a time no longer makes a later unit fail on an
   earlier sibling's **uncommitted** work. A test gates three units in
   sequence, without committing between them, and all three pass.
2. **The protection is not widened into uselessness.** The docstring rejects
   degrading to "anything any unit in the plan ever claimed", and that
   rejection stands. A path no unit in the wave declared is still a violation;
   a unit in a *different* wave is still not an excuse. A test pins each.
3. Pick the narrowest rule that closes criterion 1 and say why you picked it.
   Candidates, not a menu to pick blindly from: excuse a `done` sibling's
   `owns` only while its work is uncommitted; excuse it only for the remainder
   of the current gating pass; or keep the exclusion and have the gate compare
   against the dispatch commit rather than the working tree for `done`
   siblings. Each has a different failure mode — name the one you accept.
4. Positive control: show the failure first. Three units, gated in sequence
   with no commit between, must fail on the current code, and pass after.
   Quote both runs. This one is behavioural and worth the full control.
5. Unit 04 documented the workaround — commit the wave's work before the first
   `ctx unit --status done` — in `WaveFixture.ready`'s docstring in
   `tests/test_wave_gating.py`. That file is **not** yours. If your fix makes
   the workaround unnecessary, report the exact docstring change rather than
   making it.
6. Every existing gate and review test passes **unedited**: `test_gates`,
   `test_audit_ungated_gate`, `test_gate_window`, `test_review*`,
   `test_wave_gating`. If one fails, that is criterion 2 being violated.
7. `python3 -m unittest discover -s tests -q` passes; `ctx doctor`, `ctx ci`
   and `ruff` exit 0. Do not move any floor.
8. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the rule you chose
and the failure mode you accepted with it · the before/after for criterion 4 ·
the docstring change unit 04's file needs, if any.
