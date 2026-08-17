---
description: Scaffold the ledger in this project and propose verify commands
allowed-tools: Bash
argument-hint: "[--profile code|docs|research|infra|data] [--verify-now]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" init $ARGUMENTS`

Report what was created in one or two lines. If no verify commands were configured,
say so plainly — the L1/L2 gates cannot work without them, and inventing commands
that have never been run is how gate thrash starts.
