---
description: Write a resume packet for another session, person or model
allowed-tools: Bash, Read, Edit
argument-hint: "[name]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" handoff $ARGUMENTS`

That packet is mechanical: it records state, not meaning. Now open it and add
what the state cannot show.

- **Situation** — why this work is happening, in a sentence someone with no
  context could act on.
- **Established facts** — only things you verified. A confident wrong fact in a
  handoff is worse than a gap, because the next session will trust it.
- **Constraints** — what must not change, and any dead end you already ruled out.
  Saving someone from repeating a failed approach is most of a handoff's value.
- **Resume here** — one concrete next action.

Keep it short enough that reading it is obviously cheaper than rediscovering it.
