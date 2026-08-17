---
description: Turn an intent into checkable criteria, asking before assuming
allowed-tools: Bash, Read, Grep, Glob, Edit, AskUserQuestion
argument-hint: «short-name» — «what you want»
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" spec $ARGUMENTS`

Now do the work this command exists for: **surface what you would otherwise assume.**

1. **Read enough of the repo** to know what the request touches. Delegate broad
   exploration to a subagent — you need conclusions, not file contents.

2. **Write the spec file.** Intent as one paragraph. Then acceptance criteria that
   are *checkable*: name the observable, not the implementation. "Handles errors"
   is not a criterion; "a failed refresh surfaces AuthExpiredError, never a raw
   network error" is. Add an **Out of scope** section — it is often the most
   valuable part, because it is where silent scope creep gets caught.

3. **Write down every question whose answer would change what you build.** Use
   `ctx question <slug> "..."` for blocking ones, `--non-blocking` for the rest.
   Be honest about which is which: a blocking question is one where guessing wrong
   means rework.

4. **Ask them.** Use AskUserQuestion, recommending an option where you have a
   view. Then record each answer:
   `ctx resolve <slug> --question "<substring>" --answer "<what they chose>"`

Do not write implementation code in this command. The spec is not ready to plan
while any blocking question is open — `ctx spec-ready <slug>` is the check, and it
exits non-zero until every blocking question is resolved.

Automated decomposition (`/ctx:plan`) is not built yet. Once the spec is ready,
work its criteria as individual `/ctx:task` items.

If the request is genuinely unambiguous and small, say so and suggest
`/ctx:task` instead. A spec for a two-line fix is how this system gets abandoned.
