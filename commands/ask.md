---
description: Show and ask the questions still blocking a spec
allowed-tools: Bash, AskUserQuestion
argument-hint: "[spec-name]"
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" ask $ARGUMENTS`

If blocking questions are listed above, ask them now with AskUserQuestion —
batched into one call, each with a recommendation where you have a view, phrased
so the answer is actionable rather than a preference.

Record each answer:
`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" resolve --question "<substring>" --answer "<answer>"`

If nothing is blocking, say so in one line and name the next step. Do not invent
questions to look thorough — a question that does not change what you build
belongs in the non-blocking list or nowhere.
