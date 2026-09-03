---
description: Promote a context bundle to the global store
allowed-tools: Bash
argument-hint: «name»
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" promote "$ARGUMENTS"`

If a list of bundles was printed instead, ask which one they meant. Otherwise
confirm in one line. Promotion is deliberately manual: a globally-loadable bundle
can surface a fact from an unrelated codebase, so it should carry a name someone
recognises.
