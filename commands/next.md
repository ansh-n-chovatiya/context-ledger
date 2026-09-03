---
description: The single most useful next action, worked out from ledger state
allowed-tools: Bash
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" next`

State that action in one line and do it, unless the user's actual request points
somewhere else — this reads the ledger, not their mind.

If it names a `/ctx:` command, run it. If it names a `ctx` CLI command, run that.
Do not restate the reasoning above; the user can see it.
