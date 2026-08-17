---
description: Decompose a ready spec into independently dispatchable units
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Task
argument-hint: «plan-name» [--spec «spec-name»]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" plan $ARGUMENTS`

If that refused because the spec has unanswered blocking questions, stop and run
`/ctx:ask`. Planning around an assumption is the failure this whole system exists
to prevent.

Otherwise, cut the work up and write one unit file per piece. **The test every
unit must pass: could an agent that has never seen this conversation execute it?**
That is the same property that makes it dispatchable and the property whose
absence produces half-finished work.

Scaffold each with
`ctx plan-unit NN-kebab-name --tier subagent --owns path --owns path`,
then fill in:

- **`owns`** — the exclusive write scope. Two units in the same wave may not
  overlap here; that is what makes parallel execution safe rather than hopeful.
- **`reads`** — what it must read, as `- path: x` with `symbols:` where you can.
  Naming a file a sibling unit owns is a race, and the check will say so.
- **`forbid`** — paths a concurrent sibling owns.
- **`depends_on`** — units that must finish first. Waves are computed from this,
  so it is the only place ordering is expressed. Do not hand-write `wave`.
- **`tier`** — `subagent` for anything read-heavy or independently writable,
  `inline` when you need the result immediately.
- **`## Interfaces`** — exact signatures this consumes and produces. Siblings code
  against them, so changing one mid-wave breaks their assumptions.
- **`## Acceptance criteria`** and **`verify`** — how the done-gate judges it.

Cut along **file ownership**, not along phases of thought. Three units that each
own distinct files run in parallel; three that all edit one file are one unit with
a numbered criteria list.

Then check it:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/ctx" plan-check
```

That computes the waves, detects ownership collisions and read/write races, and
derives `plan.json`. Fix what it reports — it names the exact `depends_on` line
that resolves each collision. Nothing is dispatched until it is clean.
