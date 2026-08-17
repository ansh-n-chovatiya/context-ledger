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
itself also adds **~557 tokens** of always-on context in every session — the
descriptions Claude reads to know these commands exist. So a real L0 session costs
roughly **587 tokens**, L1 about 655, and L2 up to ~1,280.

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
| `/ctx:doctor [--verify]` | Check layout, budgets, verify commands, gate |
| `/ctx:spec «name»` | Escalate to L2: intent → checkable criteria → questions |
| `/ctx:ask [name]` | Show what must be answered before building, and ask it |
| `/ctx:decide «title»` | Record an ADR so a settled choice is not re-argued |
| `/ctx:verify` | Run the done-gate by hand; `--sign-off rubric\|human` |
| `/ctx:plan «name»` | Decompose a ready spec into dispatchable units |
| `/ctx:start [--wave N]` | Dispatch brief for the next wave |
| `/ctx:handoff [name]` | Resume packet for another session, person or model |
| `/ctx:merge «unit»` | Land a unit's worktree branch after its gate passes |

Plus CLI-only helpers the commands above drive: `ctx question`, `ctx resolve`,
`ctx spec-ready` (Gate 1 as an exit code, for CI), `ctx plan-unit`,
`ctx plan-check`, `ctx unit`, `ctx worktree list|remove`, `ctx digest`,
`ctx level`, `ctx journal`, and the phase-7 additions below.

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

### Standing context

Name bundles in `auto_load` and every session gets their content:

```yaml
auto_load: [house-conventions]
```

Only **Constraints** and **Established facts** are injected. `Situation` and
`Resume here` describe one piece of work, so they are noise as standing context.

It is emitted last in the briefing, so if there isn't room the cap truncates
*this* rather than dropping your active task. At L0 the 220-char cap leaves almost
none — raise `briefing_chars.l0` if you want house rules there, and check
`ctx doctor` for truncation afterwards. A name that resolves to nothing is
reported in the briefing rather than silently skipped.

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

## CI, migration and measurement

Four CLI-only commands. None has a slash command, because none of them is a
conversation — and every slash command costs always-on context.

| Command | Does | Exit |
|---|---|---|
| `ctx ci [--plan X]` | Every headless check in one run | 0 / 1 |
| `ctx verify --plan X` | Run every unit's gate in a plan | 0 / 1 |
| `ctx migrate [--check]` | Upgrade ledger files; `--check` never writes | 0 / 1 |
| `ctx budget [--plan X]` | Predicted **and measured** context cost | 0 |
| `ctx telemetry` | Hook durations and injected briefing sizes | 0 |

```yaml
# .github/workflows/ledger.yml
name: ledger
on: [push, pull_request]
jobs:
  ledger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: git clone --depth 1 https://github.com/…/context-ledger /tmp/ctx
      - run: /tmp/ctx/bin/ctx migrate --check   # ledger schema is current
      - run: /tmp/ctx/bin/ctx ci                # layout, budgets, spec, plan
```

`ctx ci` fails on a stale schema, an unanswered blocking question, a plan with
ownership collisions, or a briefing that has to truncate. `ctx verify --plan`
runs each unit's mechanical checks and reports `rubric`/`human` as awaiting
sign-off rather than pretending an unattended run can judge them.

**On truncation rather than overflow.** `ctx doctor`, `ctx ci` and `ctx budget`
report whether a briefing was *truncated*, not whether it exceeded its cap. The
cap can never be exceeded — the fitting code clamps — so "within cap" is a
tautology that always passes. Truncation is the actionable signal: it means state
a session needed got dropped to fit.

**Migration** stamps and upgrades every ledger file. It is idempotent, `--check`
writes nothing, a ledger stamped *newer* than the plugin is refused rather than
downgraded, and `ctx.yaml` is edited line-wise so your comments survive.

**Measurement.** `ctx doctor` predicts what a briefing would cost; the hooks
record what sessions actually paid, to `.ctx/runtime/telemetry.jsonl` (gitignored,
size-capped). `ctx budget` shows both side by side, and points at
`claude plugin details ctx` for the always-on cost it cannot measure itself.

---

## Plans, waves and units

`/ctx:plan` decomposes a **ready** spec — it refuses outright while any blocking
question is open, which is what makes Gate 1 more than advice. The output is one
file per unit, and the test each must pass is: *could an agent that has never seen
the conversation execute this?* That property is what makes a unit dispatchable,
and its absence is what produces half-finished work.

```yaml
unit: 03-token-refresh
tier: subagent            # inline | subagent | session
depends_on: [01-key-store]
owns:  [src/auth/refresh.ts]   # exclusive write scope
reads: [{path: src/auth/key-store.ts, symbols: [KeyStore]}]
forbid: [src/auth/session.ts]  # a concurrent sibling owns this
budget_tokens: 45000
verify: [{kind: cmd, run: pnpm vitest run src/auth/refresh.test.ts}]
```

**Waves are computed, never authored.** `depends_on` is the only place ordering
lives; `ctx plan-check` derives wave numbers from it, writes them back, and
generates `plan.json`. Two checks run before anything is dispatched:

| Check | Catches |
|---|---|
| Disjoint ownership | two units in one wave writing the same path |
| No read/write races | a unit reading a path a **concurrent** unit rewrites |

The second matters because `owns` sets can be disjoint and the plan still be
wrong. Both are scoped to a wave — the same overlap across *different* waves is
just ordinary sequential work and is not flagged.

Neither is auto-repaired. Each reports the exact line that fixes it:

```
wave 1: 01-key-store and 03-rotate both own src/keys.ts
        — add `depends_on: [01-key-store]` to 03-rotate or split the paths
wave 1: 04-refresh reads src/clock.ts while 02-clock rewrites it
        — add `depends_on: [02-clock]` to 04-refresh
```

Rewriting someone's dependency graph silently is not a favour. Nothing is written
while problems remain, and `plan.json` archives prior revisions rather than
overwriting, so an in-flight wave can't be pulled out from under itself.

`/ctx:start` prints a dispatch brief and spawns nothing itself — what to hand a
subagent is the harness's decision. It groups units by tier, names the
`unit-runner` agent, and repeats the rule that keeps the whole thing affordable:
**the orchestrator reads unit files and unit reports, never source.**

---

## The worktree tier

A unit with `tier: session` gets its own checkout and branch, so several units can
*write* at once and a unit that goes wrong is discarded by deleting a directory
rather than untangled out of a shared tree.

```
/ctx:start                       # prepares a worktree per session unit
cd .ctx/runtime/worktrees/01-charge
ctx unit 01-charge               # arms the done-gate for this unit
claude                           # work it
git commit -am "…"
cd -  &&  /ctx:merge 01-charge    # preflight, gate, merge, cleanup
```

`start` prepares the tree and hands you the command rather than driving sessions
headlessly — parallel writes are where you most want to stay in the loop.

`ctx merge` refuses in four situations, and refusing changes nothing:

| Refusal | Why |
|---|---|
| Integration tree dirty | a merge would entangle unrelated work |
| Uncommitted work in the worktree | a merge would silently drop it |
| Changed a path outside `owns` | breaks the isolation siblings relied on |
| Done-gate fails **in the unit's own worktree** | the work isn't finished |

**One correction to the original design.** It called for "sequential fast-forward
merges in wave order", which is wrong from the second merge onward — once the
first branch lands, the integration branch has moved and the next is no longer a
fast-forward. This uses a real merge commit, and treats **any conflict as a
violated ownership contract**: if `owns` sets were disjoint and honoured, git has
nothing to reconcile. On conflict it aborts and names the paths.

`.ctx/` is excluded from the dirty-tree preflight. That is not a workaround —
those files are merge-safe by construction (append-only journal partitioned by
date, one file per unit, immutable ADRs), and without the exclusion the ledger's
own bookkeeping would make the tree permanently dirty and `ctx merge`
unreachable. A regression test pins both halves: ledger noise never blocks, and a
stray write is still caught alongside it.

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
| `symbol` | named signatures still appear verbatim | one file read |
| `cmd` | shell command exits 0 | subprocess |
| `rubric` | a `verifier` subagent judges the criteria met | model, recorded |
| `human` | you sign off explicitly | you, recorded |

`symbol` is interface freeze made enforceable: list the signatures later units
code against, and the gate fails if one is renamed or deleted. Crude on purpose —
it needs no parser per language and catches the two cases that actually break a
sibling.

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
Phases 5–6 then added ~132 back for four commands and the `unit-runner` agent —
`plan-check`, `unit` and `worktree` were deliberately left CLI-only for the same
reason.

`/ctx:doctor` prints measured briefing size against the cap for every level, so
the budget is observable rather than aspirational. When a level reports OVER,
the fix is to shorten the objective and criteria on disk — raising the cap
recreates the problem the ledger exists to solve.

---

## Status

**All seven phases are implemented.** Scaffold, cross-session continuity,
portable memory, the L0/L1/L2 levels, both gates, plan generation with wave
scheduling and subagent dispatch, the git-worktree tier with merge protocol and
enforced interface freeze, and hardening — migration, CI mode, budget accounting
and hook telemetry.

## Tests

```bash
python3 -m unittest discover -s tests
```

170 tests, no dependencies. The ones worth keeping green are the risk guards:

- briefing caps hold at every level under deliberately bloated input
- briefings are byte-identical for identical state (prompt-cache hits)
- journal cost does not grow with history
- hooks stay silent in untracked projects, and fail open on unexpected errors
- a vague spec produces blocking questions, and the gate exits non-zero
- incomplete work cannot end its session, and the gate escalates after 3 tries
- a missing tool never blocks, but a real test failure always does
- a judged sign-off is cleared by any subsequent edit
- overlapping `owns` and read/write races are caught *within* a wave and
  deliberately *not* flagged across waves
- a broken plan writes no graph, and re-checking archives the prior revision
- two units in one wave both merge cleanly (the second is not a fast-forward)
- nothing merges past a failed gate or a write outside `owns`
- the ledger's own writes never block a merge, but a stray write still does
- `migrate --check` writes nothing, applying twice is a no-op, and a newer
  ledger is refused rather than downgraded
- `ctx.yaml` keeps its comments through a migration
- telemetry never raises and never grows without bound
- `auto_load` injects bundle content, and is truncated before active work
