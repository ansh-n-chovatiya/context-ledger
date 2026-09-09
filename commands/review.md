---
description: Review a completed unit against its criteria, from a snapshot diff
allowed-tools: Bash, Read, Task
argument-hint: «unit» [--plan slug] [--round N]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" review $ARGUMENTS || true`

The line above names a **review package** file. It was built by diffing the
content snapshot taken when the unit was dispatched against one taken just now —
no commit, no branch, no git at all.

**Read the path, not the package.** The package is the reviewer's input, and
reading it here pulls the whole diff into the orchestrator's context, which is
the cost this loop exists to avoid. You do not read source files and you do not
read the diff.

Dispatch the `reviewer` subagent with that path, on the model named in
`models.reviewer` — a Task call with no model inherits this session's, which is
not necessarily the one configured for review.

Record every finding it returns, one call each:

```
ctx findings «unit» --add <severity> --summary "<one line>" --where <file:line> --evidence "<why>"
```

A finding that stays in the transcript is gone by the next round. The file is
what the done-gate reads.

Then act on what came back.

- **`verdict: approved`** — say so in one line and move on. Nothing to record
  beyond the findings themselves; `minor` ones stay open and never block.
- **Blocking findings open** — `critical` and `important` block the `review`
  verify kind while they are open. Fix each one where it lives, then run
  `ctx review «unit»` again. The next round dispatches `re-reviewer`, which
  verdicts the open findings against a package covering only the fix.
- **Round cap reached** — three rounds is the ceiling. A fourth round of the same
  argument is a decision to escalate, not to retry. What remains open must be
  parked with a ruling: `ctx findings «unit» --set <id> --status parked
  --ruling "<what was decided and why>"`. Parking spends the user's judgement on
  their behalf, so tell them, and write the reasoning down as an ADR with
  `/ctx:decide` rather than leaving it in a findings file.
- **A disputed finding** — a dispute is a technical claim, so it needs evidence:
  a `file:line` or command output that shows the finding is wrong.
  `ctx findings «unit» --set <id> --status disputed --evidence "<proof>"`. Judge
  the claim; do not accept it because the implementer made it. "The plan said to"
  and "left it deliberately" are the implementer grading their own work.

There is deliberately **no `acknowledged` status**. Agreeing with a finding does
not close it — fix it, refute it with evidence, or park it with a ruling.

A **scope violation** in the package is already decided. It was computed by
comparing changed paths against the unit's declared `owns`, with no model in the
loop. Do not widen `owns` to make it go away; that is a planning error to
surface, not to absorb.

If the command refused because there is no "before" snapshot, it names the
command that would have made one. Do not review against a snapshot taken after
the fact — an empty diff is not a clean review.
