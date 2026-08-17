---
description: Record an architectural decision so it is not re-argued later
allowed-tools: Bash, Edit, Read
argument-hint: «title of the decision»
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" decide "$ARGUMENTS"`

Fill in the ADR that was just created:

- **Context** — what forced a choice. Include the constraint or the disagreement,
  not a summary of the feature.
- **Decision** — one sentence, active voice, stating what was chosen.
- **Consequences** — what this costs and what it rules out. An ADR with no cost
  listed is a decision that was never really weighed.

ADRs are immutable. To reverse one, write a new ADR that supersedes it rather
than editing this file — the record of having changed your mind is the point.
