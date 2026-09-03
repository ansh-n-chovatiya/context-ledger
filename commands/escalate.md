---
description: Promote the active task to a spec, carrying its criteria across
allowed-tools: Bash, Read, Edit, AskUserQuestion
argument-hint: "[task-name]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" escalate $ARGUMENTS`

The objective and acceptance criteria came across from the task file, so start
from what is already there rather than rewriting it.

Now do the part escalation exists for: **the work turned out to be bigger than
one task, so say what the extra pieces are.** Write down every question whose
answer would change what gets built — `ctx question <slug> "..."` for blocking
ones — and ask them with AskUserQuestion.

The task file is kept and marked `escalated`. It is the record of why this grew,
which is worth more than a tidy directory.

If escalating turns out to be wrong — the work is one coherent change after all —
say so and `/ctx:drop` back down. Ceremony that stops paying for itself is the
failure mode this system cares most about.
