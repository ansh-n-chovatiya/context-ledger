---
ctx_schema: 1
spec: close-the-routes-around-the-gate
status: ready
created: 2026-09-10
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Wave 1 made the gate strong where it runs. These findings all have one shape:
the routes *around* running it are not themselves gated. None appear in
report.md — they were found by using the tool on itself, and two of them partly
reopen what wave 1 just closed. A gate that a dispatched runner can step around
using tools it was legitimately given is not a gate; it is a convention.

## Acceptance criteria

1. A dispatched runner cannot silently re-seal a contract it edited. `ctx start
   --rebaseline <unit>` refuses, or requires an explicit acknowledgement naming
   what changed, when the unit's `verify` block, `owns` list, or criteria body
   differs from the sealed contract. Retaking a review *baseline* stays allowed;
   re-sealing a changed *contract* is the thing that must not be quiet.
2. A unit that was never sealed cannot be gated. `ctx unit <name> --status done`
   refuses when no dispatch seal exists, and names `ctx start` as the route that
   creates one. A direct Task dispatch that skips `ctx start` is therefore
   detectable at the gate rather than invisible.
3. The refusal in criterion 2 is not silent anywhere else either: `ctx status`
   and the wave board show an unsealed unit as unsealed, not as pending.
4. The diff check no longer passes trivially once work is committed. Whatever
   mechanism is chosen, this must hold: a runner that edits a file outside its
   `owns` and commits the change still fails its gate.
5. Concurrent siblings stop failing each other's gates. A unit's diff check
   ignores changes confined to a sibling unit's `owns` within the same wave, and
   still fails on a change to a path owned by nobody in the wave.
6. Criterion 5 does not become a hole: a change to a *sibling's* path is ignored
   only while that sibling is in flight in the same wave. It is not ignored once
   the sibling is gated, and it is never ignored for a unit outside the wave.
7. `ctx spec` with a long intent produces a usable spec or a clean refusal, never
   an uncaught OSError. The slug is capped where every caller benefits, not only
   the CLI path, and the cap is deterministic — the same intent yields the same
   slug across runs and platforms.
8. An unrecognised `level:` in `ctx.yaml` still coerces to L0 and now says so on
   stderr. The coercion is unchanged; only its silence is fixed.
9. A refusal reported as success is fixed in the three known places: `ctx start`
   printing "not ready to dispatch — nothing was started", and `ctx load` /
   `ctx merge` given a name that does not resolve. All exit non-zero.
10. Every guard added carries a positive control: the unit report names the
    mutation applied and the test that died. A test that still passes when its
    mechanism is disabled is not coverage.
11. `python3 -m unittest discover -s tests` is green, the count has not dropped
    below 818, and `ctx doctor` and `ctx ci` exit 0.

## Out of scope

- Roadmap items 12-26 (waves 3-5): packaging, release artifacts, the policy
  layer, atomic writes, locking, the `cli.py` extractions, the README split.
  Those follow this spec and get their own plans.
- `--json` output (roadmap item 9), still deferred.
- Rewriting `verify.is_ledger`. Wave 1 deliberately closed contract forgery by
  comparing against a dispatch seal rather than by narrowing that function, and
  this spec does not revisit that decision.
- Making a finished task release the active slot. Real, observed this session,
  but it is a usability defect in the ledger rather than a route around the gate.

## Notes

Findings 1-5 are the user's, found by using the tool on itself. Finding 8 was
found while writing tests for the hollow level guard in the previous wave;
finding 9 was found by the unit that removed the `HARD_FAIL` allowlist and left
deliberately alone because the fix touched files outside its ownership.

Criterion 4 is the one with real design freedom, and the reason is finding 3's
root cause: `verify.changed_files` (`verify.py:721`) shells out to
`git status --porcelain --untracked-files=all`, which by construction can only
ever see the working tree. Once a runner commits, its changes are invisible to
the very check meant to police them.
