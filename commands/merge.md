---
description: Land a unit's worktree branch after its gate passes
allowed-tools: Bash, Read
argument-hint: «unit-name»
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" merge "$ARGUMENTS"`

Read the result above.

- **Merged** — the worktree and branch are gone and the unit is `done`. Move on;
  if a next wave is named, `/ctx:start` it.
- **Gate failed** — the unit's own checks failed *in its own worktree*. Nothing
  merged. Go into that worktree and fix it there; do not fix it in the main tree.
- **Wrote outside `owns`** — the unit broke the isolation its siblings relied on.
  Do not widen `owns` to make the error go away. Either discard the worktree
  (`ctx worktree remove <unit> --force`) or treat it as a planning error and
  re-plan, which is a decision to surface rather than absorb.
- **Merge conflicted** — ownership was disjoint, so git had nothing to reconcile;
  a unit wrote somewhere it should not have. The merge was aborted and nothing
  changed. Report which unit and which paths.

Never pass `--skip-gate` to get past a failure. It exists for the case where the
checks genuinely cannot run in a worktree, and using it to bypass a real failure
defeats the only mechanism stopping half-finished work from landing.
