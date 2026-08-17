---
description: Run the done-gate for the active work
allowed-tools: Bash, Read, Grep, Glob, Task
argument-hint: "[--sign-off rubric|human] [--note …]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" verify $ARGUMENTS`

Read the result above.

- **PASS** — say so in one line. Nothing else to do.
- **FAIL** — fix the specific failing criterion. Do not touch anything the
  failure does not implicate, and do not weaken the check to make it pass. If you
  believe the criterion or the check itself is wrong, say that instead of
  working around it.
- **PENDING on `rubric`** — delegate to the `verifier` subagent with the work
  file path and the diff. It judges each criterion independently and defaults to
  fail without evidence. Only if it returns `verdict: pass` do you record it:
  `ctx verify --sign-off rubric --note "<verifier summary>"`
- **PENDING on `human`** — ask the user directly, then
  `ctx verify --sign-off human`. Never sign off on their behalf.
- **warn lines** — a check could not run at all. That is a configuration bug, not
  a work failure. Report it and fix `ctx.yaml`; it is not blocking you.

Any edit after a sign-off clears it automatically, so verify last.
