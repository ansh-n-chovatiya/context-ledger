# Context Ledger

A Claude Code plugin that keeps specs, plans, decisions and memory **on disk**
instead of in the context window. Sessions become disposable, compaction stops
losing state, and work can be handed to a subagent, a fresh session or a
teammate without re-explaining anything.

Python 3 standard library only. Nothing is added to your project's dependency
tree, and the plugin is silent in any project that has not run `/ctx:init`.

---

## Install

```bash
/plugin marketplace add /path/to/context-ledger
/plugin install ctx@context-ledger
```

Then, in a project you want to track:

```
/ctx:init
```

That is the whole setup. You are now at **L0**, which asks nothing of you.

---

## Engagement levels

The single most important design decision: **ceremony is opt-in, and the floor
costs nothing.** A system that demands a spec for a two-line fix gets abandoned.

| Level | You write | Gates | Briefing | Use when |
|---|---|---|---|---|
| **L0 trace** | nothing | none | ~30 tok (cap 61) | default — anything you'd finish in one sitting |
| **L1 tracked** | one task file | done-gate | ~96 tok (cap 250) | criteria worth writing down |
| **L2 planned** | spec + plan + units | ambiguity + done | cap 722 tok | independent pieces, parallel work |

**Total session cost.** The briefing above is what the *hooks* inject. The plugin
itself also adds **~425 tokens** of always-on context in every session — the
descriptions Claude reads to know these commands exist. So a real L0 session costs
roughly **455 tokens**, L1 about 520, and L2 up to ~1,150.

Measure it yourself, don't trust this number as it ages:

```bash
claude plugin details ctx     # component inventory + projected token cost
```

That always-on cost applies in *every* project when installed at user scope, even
ones with no `.ctx/`. Install with `--scope project` if you only want it where you
opt in. The hooks themselves are harness-only and cost nothing.

```
/ctx:task fix-token-refresh     # L0 → L1
/ctx:drop                       # back to L0, nothing deleted
```

At L0 the plugin only journals to disk. It injects a single line at session
start and nothing at all per turn.

---

## Commands

| Command | Does |
|---|---|
| `/ctx:init` | Scaffold `.ctx/`, detect profile, propose verify commands |
| `/ctx:status` | Level, active work, briefing budget, recent journal |
| `/ctx:resume` | Expanded prior state, on demand |
| `/ctx:task «name»` | Escalate to L1 with one task file |
| `/ctx:drop` | Return to L0 |
| `/ctx:save «name»` | Write a portable context bundle |
| `/ctx:load «name»` | Load a bundle: project → global → path |
| `/ctx:list` | Saved bundles, project and global |
| `/ctx:promote «name»` | Copy a bundle to the global store |
| `/ctx:digest` | Regenerate the journal digest |
| `/ctx:doctor [--verify]` | Check layout, budgets, verify commands, gate |
| `/ctx:spec «name»` | Escalate to L2: intent → checkable criteria → questions |
| `/ctx:ask [name]` | Show what must be answered before building, and ask it |
| `/ctx:decide «title»` | Record an ADR so a settled choice is not re-argued |
| `/ctx:verify` | Run the done-gate by hand; `--sign-off rubric\|human` |

Plus three CLI-only helpers that the commands above drive: `ctx question`,
`ctx resolve` and `ctx spec-ready` (Gate 1 as an exit code, for CI).

All of it is also a CLI, which is what makes the same checks runnable in CI:

```bash
bin/ctx status
bin/ctx doctor --verify
```

---

## Portable context bundles

The memory convention is **plain markdown with a fixed section schema**. Nothing
about it depends on this plugin — pasting a bundle into a different tool, or a
different model entirely, is a supported path rather than a fallback.

```markdown
---
ctx_bundle: 1
name: billing-migration
scope: project
tags: [billing, stripe]
---

# Context — billing-migration

## Situation
## Established facts
## Decisions made
## Open questions
## Constraints
## Artifacts
## Resume here
```

Bundles live in the repo, so they land in pull requests and can be reviewed.
`/ctx:promote` copies one to `~/.claude/ctx/` for cross-project recall — kept
manual on purpose, because automatic cross-project memory is how you get a
confident assertion sourced from an unrelated codebase.

---

## What lives where

```
.ctx/
  ctx.yaml            profile, budgets, gate policy, redaction
  tasks/              L1 — one file per tracked change
  specs/              L2 — intent and acceptance criteria
  plans/              L2 — plan.json plus one prompt file per unit
  contexts/           saved bundles + index
  journal/            append-only, date-partitioned, plus DIGEST.md
  decisions/          ADRs; immutable, superseded rather than edited
  runtime/            gitignored: state pointers, hook errors, verify logs
```

Only `runtime/` is gitignored. Everything else is written to be reviewed.

---

## Hooks

| Event | Does | On failure |
|---|---|---|
| `SessionStart` | Inject the budgeted briefing | open |
| `UserPromptSubmit` | **Silent** unless drift was detected | open |
| `PreToolUse` | Queue a nudge on out-of-scope edits (L2) | open |
| `PostToolUse` | Append to the journal — injects nothing | open |
| `PreCompact` | Flush state, write a mechanical autosave | open |
| `SessionEnd` | Finalise journal and digest | open |
| `Stop` / `SubagentStop` | **The done-gate** — refuses completion on a failing criterion | **closed** |

Every hook except the gate **fails open**: an unexpected error is appended to
`.ctx/runtime/hook-errors.log` and the hook exits 0. A bug here must never brick
a session — including a bug in the gate itself, which also fails open when *our*
code throws. It fails closed only on a criterion that genuinely did not pass.

---

## The two gates

**Gate 1 — ambiguity.** `/ctx:spec` writes acceptance criteria and a
`questions.md` holding blocking questions as unchecked boxes. A spec is not
`ready` while any remain, and `ctx spec-ready` is that check as an exit code. The
model cannot assume its way past a file it has to clear.

**Gate 2 — definition of done.** The `Stop` hook runs the active work's checks and
blocks completion on failure, feeding back the failing check and its output. Five
kinds, run cheapest-first and short-circuiting, so a scope violation never pays
for a test run:

| Kind | Passes when | Decided by |
|---|---|---|
| `diff` | changed files ⊆ declared `owns` | git, free |
| `exists` | path present, optional regex match | stat |
| `cmd` | shell command exits 0 | subprocess |
| `rubric` | a `verifier` subagent judges the criteria met | model, recorded |
| `human` | you sign off explicitly | you, recorded |

Three properties keep it from becoming an obstacle:

- **Bounded.** Three blocks, then it stops, marks the work `verify_failed`, and
  escalates to you with what was tried. No grinding.
- **Infrastructure failure is not work failure.** Exit 127, a timeout, or output
  matching `No module named …` / `command not found` / `Missing script` is a
  config bug — it warns and passes rather than blocking every session in a
  project whose toolchain is not installed.
- **Sign-offs do not outlive the code.** Any edit after a `rubric`/`human`
  sign-off clears it automatically, so the gate re-closes.

Escape hatches: `CTX_GATE=off`, `gate.enabled: false` in `ctx.yaml`, and L0 has
no gate at all.

`ctx verify` exit codes: **0** pass · **1** a criterion failed · **2** nothing
could run (a `ctx.yaml` problem).

---

## Cost design

Four decisions do most of the work:

1. **`UserPromptSubmit` is silent by default.** Re-injecting criteria every turn
   would cost ~12k tokens across a long session to repeat something the model
   already has. Instead `PreToolUse` sets a one-shot flag when an edit strays
   out of scope, and the nudge is delivered once.
2. **The digest is a tail, not a summary.** Summarising on a schedule costs
   tokens on a schedule. Journal lines are structured, and the digest is the
   last N plus a count — O(1) regardless of project age, zero inference.
3. **Briefings are deterministic.** No clock time, no drifting counters.
   Identical state produces byte-identical text, so the prompt cache hits.
4. **Scripts, not agents.** Status, digests, budget measurement, scope and
   collision checks are all Python. They cost nothing.

The plugin's own always-on footprint is the one cost these decisions don't touch,
so it was trimmed directly: shortening component descriptions and dropping the
`digest` slash command (still `ctx digest`) took it from ~590 to ~425 tokens. A
slash command whose whole body is one shell call does not earn always-on context.

`/ctx:doctor` prints measured briefing size against the cap for every level, so
the budget is observable rather than aspirational. When a level reports OVER,
the fix is to shorten the objective and criteria on disk — raising the cap
recreates the problem the ledger exists to solve.

---

## Status

Phases 0–4 are implemented: scaffold, cross-session continuity, portable memory,
the L0/L1/L2 level machinery, the ambiguity gate and the done-gate.

Not yet built: plan generation and unit dispatch (`/ctx:plan`, `/ctx:start`), and
the git-worktree tier. The unit contract that those phases will read is already
honoured by everything downstream of it — `owns`/`forbid` drive the `diff` check
and the `PreToolUse` scope nudge today.

## Tests

```bash
python3 -m unittest discover -s tests
```

75 tests, no dependencies. The ones worth keeping green are the risk guards:

- briefing caps hold at every level under deliberately bloated input
- briefings are byte-identical for identical state (prompt-cache hits)
- journal cost does not grow with history
- hooks stay silent in untracked projects, and fail open on unexpected errors
- a vague spec produces blocking questions, and the gate exits non-zero
- incomplete work cannot end its session, and the gate escalates after 3 tries
- a missing tool never blocks, but a real test failure always does
- a judged sign-off is cleared by any subsequent edit
