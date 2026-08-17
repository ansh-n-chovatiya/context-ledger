---
description: Load prior work state on demand
allowed-tools: Bash
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" resume`

Using only the state above, tell the user in three lines or fewer: what was last
being worked on, and the single most useful next action. If a saved context looks
relevant, name it and offer `/ctx:load «name»` rather than loading it unasked.
