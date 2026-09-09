---
ctx_schema: 1
unit: 14-sibling-scope
plan: ctx-0-8
tier: subagent
depends_on:
  - 03-plan-unit-fields
  - 04-test-first-kind
  - 07-review-telemetry
  - 11-proofs
owns:
  - ctx/review.py
  - tests/test_sibling_scope.py
reads:
  - ctx/snapshot.py
  - ctx/plan.py
forbid: []
budget_tokens: 40000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 6
---

## Objective
A wave of two or more units sharing one working tree produces N−1 **false**
Critical scope violations per review package: `snapshot.out_of_scope(delta,
unit.owns)` knows only the reviewed unit's `owns`, so a sibling's write to its
own declared path is reported as a violation the package calls "not open to
argument". Because `review` is a mechanical gate check and Critical findings
block, every multi-unit wave deadlocks its own gate. Found by `11-proofs`.

## Interfaces
`review.build`'s signature and package format stay as they are. What changes is
the set of paths passed to `snapshot.out_of_scope`: the reviewed unit's `owns`
plus the `owns` of the other not-done units in the same wave.

## Acceptance criteria
1. Two units in one wave, each editing only its own declared path, produce
   review packages with **zero** scope violations. This is the regression;
   assert it end-to-end, the way `11-proofs` reproduced it.
2. The check is not weakened. A path owned by **nobody** in the wave is still a
   violation, and is still reported as Critical. Assert this in the same wave
   as criterion 1, so the two cannot be satisfied by simply disabling the check.
3. A sibling's path counts as legitimate only for units in the **same wave**
   that are not `done`. A path owned by a unit in a different wave is not a
   free pass — that unit is not running now.
4. A single-unit wave behaves exactly as before, byte-identical package.
5. The package tells the truth about what it checked. If the scope check now
   tolerates sibling-owned paths, the wording a reviewer reads must say so —
   do not leave prose claiming an absolute rule the code no longer applies.
6. `tests/test_unproven_claims.py` is NOT yours. `11-proofs` pinned today's
   behaviour in `TestSiblingWritesAreReportedNotSilentlyBlended`; that test
   will now be wrong. Do not edit it — report that it needs updating and say
   exactly which assertion changes.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
exact assertion in `test_unproven_claims.py` that must change · any interface
change.
