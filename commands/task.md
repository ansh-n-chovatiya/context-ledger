---
description: Track one change at L1 with a done-gate
allowed-tools: Bash, Read, Edit
argument-hint: «short-name» [objective]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" task "$ARGUMENTS"`

If it reported that no name was given, ask the user for a short kebab-case
name and a one-sentence objective, run it again, and continue from there.

Otherwise open the task file printed above and fill in two sections:

**Objective** — one sentence, present tense, describing the observable outcome.

**Acceptance criteria** — a numbered list where every item is *checkable*. "Works
correctly" is not a criterion; "a token expiring in under 60s triggers exactly one
refresh" is. Three to five is usually right.

Then confirm the `verify:` block in the frontmatter actually proves those criteria.
If it does not, say so and propose a command instead of silently accepting it.

Keep this to one round trip. L1 exists because the task is small — if filling this
in reveals the work needs several independent pieces, say so and suggest `/ctx:spec`.
