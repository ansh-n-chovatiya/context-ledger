---
description: List or resolve the review findings recorded against a unit
allowed-tools: Bash, Read
argument-hint: «unit» [--plan slug]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" findings $ARGUMENTS || true`

The findings above are what a reviewer raised against this unit. They live in a
file, so they outlive this session — a gate running next week still knows the
change was never signed off.

`critical` and `important` hold the done-gate shut while they are `open`.
`minor` never blocks: it is recorded and deferred, because a loop that reruns for
"coverage could be broader" is a loop people learn to bypass.

## Resolving one

A finding leaves `open` by exactly three routes, and each costs something:

- **Fixed** — change the code, then
  `ctx findings «unit» --set «id» --status addressed`.
- **Refuted** — `--status disputed --evidence «file:line or command output»`.
  A dispute is a technical claim, so it is refused without evidence. Judge it on
  the merits; the reviewer being wrong is a normal outcome, and so is being wrong
  about the reviewer being wrong.
- **Parked** — `--status parked --ruling «what you decided and why»`. Refused
  without a ruling, because parking spends the user's judgement on their behalf.
  Record it with `/ctx:decide` as well, so it stays reviewable rather than buried
  in a status field.

**There is deliberately no way to merely agree with a finding.** "Good catch,
noted" is the performative agreement that makes findings evaporate, so that state
does not exist here. If you agree, fix it.

## What to do now

If nothing is open, say so in one line and move on — the gate will pass.

If something is open, deal with it before anything else: work the `critical`
findings first, then `important`, one at a time, and re-run `/ctx:review «unit»`
when the fixes are in. That dispatches a re-reviewer scoped to the fix, not a
fresh review of the whole unit.

If `ctx review` reported the round cap, stop fixing. The loop is not converging
and more rounds will not change that — rule on each remaining finding, park it,
and record the ruling.

## Escalations

If a listed `escalations:` block shows up, it means a failed round moved the
*fix*'s model tier — `models.escalate_on_failed_round` is on and a round left
a blocking finding open, so the next fix round dispatches on a dearer model
than this one ran on. It is a fact about the round, never a finding's status:
nothing here is `open`/`addressed`/`disputed`/`parked` because of it, and
agreeing with it or disputing it is not a thing to do. Just dispatch the next
fix round on the model it names.
