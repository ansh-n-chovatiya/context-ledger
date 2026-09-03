---
name: ledger
description: Choosing how much process a task deserves in a Context Ledger project — L0 trace, L1 tracked or L2 planned — and what belongs on disk in `.ctx/`. Use for questions about ledger levels, saved contexts, or escalating work.
---

# Engagement levels

A ledger project has three levels. The default is the cheapest one, and **the
common mistake is escalating too eagerly** — a system that demands a spec for a
two-line fix gets abandoned, which is the only failure mode that matters here.

| Level | Use when | Artifacts | Gates |
|---|---|---|---|
| **L0 trace** | Default. Anything you could finish in one sitting without a checklist. | none — journal only | none |
| **L1 tracked** | One coherent change with criteria worth writing down, still one agent's work. | `.ctx/tasks/<slug>.md` | done-gate |
| **L2 planned** | Several independent pieces, or work that must survive across sessions. | spec + plan + units | ambiguity + done |

## Choosing

Stay at **L0** unless one of these is true:

- The user stated acceptance criteria you would otherwise have to remember.
- The work spans more than one session, or you expect compaction mid-task.
- Verification is worth automating because you'll run it repeatedly.

Escalate to **L2** only when the work decomposes into pieces that could run
*independently* — different files, no shared write scope. If the pieces are
sequential steps in one file, that is L1 with a numbered criteria list, not L2.

Never escalate silently. Say why, in one line, and let the user decline.
De-escalate with `/ctx:drop` the moment ceremony stops paying for itself.

## What lives on disk, and why

Everything durable is under `.ctx/`, committed to git. State in a conversation
is lost to compaction; state in a file is not. Practical consequences:

- **Don't restate what a file already says.** Reference the path.
- **Write the decision down when you make it.** `/ctx:decide` for anything you'd
  be annoyed to re-argue. A decision only in the transcript is a decision you
  will make differently next week.
- **Facts go in bundles only once verified.** `/ctx:save` bundles are read by
  future sessions as established truth, so an inferred fact recorded as a fact
  is worse than nothing.
- **A loaded bundle is a snapshot, not the present.** Before acting on a path,
  symbol or flag one names, confirm it still exists.

## Cost discipline

The briefing injected at session start is capped per level — 61 tokens at L0, 250
at L1, 722 at L2 — and typically lands well under the cap. The plugin's own
always-on cost is separate and larger.

Do not quote a figure from memory for either. Two hardcoded numbers in two files
is how they came to disagree in the first place. Run them instead:

- `ctx budget` — this project's briefing, predicted and measured
- `claude plugin details ctx` — the plugin's own always-on footprint

When `/ctx:doctor` reports a budget as OVER, the fix is to shorten the objective
and criteria on disk, not to raise the cap. Raising it recreates the problem the
ledger exists to solve.

Two habits that keep cost down:

- **Delegate reading, not deciding.** Broad exploration belongs in a subagent
  with its own context window; it returns a conclusion, not file dumps.
- **Prefer a script to an agent.** Status, digests, scope and collision checks
  are all `ctx` subcommands. They cost zero tokens.

## When a session is ending or compacting

`PreCompact` and `SessionEnd` already flush a mechanical snapshot — no action
needed. But mechanical snapshots record *what happened*, not *what it meant*. If
the session established anything a fresh one would have to re-derive, run
`/ctx:save «name»` so the reasoning survives too.
