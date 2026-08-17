---
description: Dispatch the next wave of a checked plan
allowed-tools: Bash, Read, Task
argument-hint: "[--wave N]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" start $ARGUMENTS`

Follow the brief above exactly.

**You are the orchestrator. Do not read source files.** Read unit files and unit
reports only. That is the whole reason this session's context stays flat across a
large plan, and it is safe precisely because each unit file is self-contained.

Send all concurrent units in a **single message with multiple Task calls** so they
actually run in parallel — one call per unit, each pointing the `unit-runner`
agent at its unit file path.

As each reports back, check it against the unit's **Return contract** before
accepting: files changed, criteria passed, verify output. A report missing any of
those is incomplete — ask for the rest rather than assuming it went fine.

Stop the wave if any unit reports `interface_changed`. That invalidates what its
siblings were coded against, and deciding what to do about it is a planning
decision, not an implementation one.

Record each outcome with `ctx unit <name> --status done`, then run `/ctx:start`
again for the next wave.
