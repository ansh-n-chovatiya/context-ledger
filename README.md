# Context Ledger

**Durable project state for Claude Code.** Specs, plans, decisions and memory live
on disk instead of in the context window — so sessions become disposable,
compaction stops losing your work, and "done" becomes something a gate can refuse
to sign off on.

Python 3 standard library only. Nothing is added to your project's dependency
tree, and the plugin is completely silent in any project that hasn't opted in.

---

## Contents

- [Why this exists](#why-this-exists)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The three levels](#the-three-levels) — the one concept to understand
- [Walkthrough: a small change](#walkthrough-a-small-change) (L1)
- [Walkthrough: a large change](#walkthrough-a-large-change) (L2)
- [Memory that survives sessions](#memory-that-survives-sessions)
- [Command reference](#command-reference)
- [Configuration reference](#configuration-reference)
- [Verification reference](#verification-reference)
- [What lives on disk](#what-lives-on-disk)
- [Continuous integration](#continuous-integration)
- [Operations](#operations)
- [What it costs](#what-it-costs)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Uninstalling](#uninstalling)
- [Development](#development)

---

## Why this exists

Four common complaints about working with an AI coding agent have one shared
cause — **project state lives in a conversation instead of in a repository**:

| What you experience | What's actually wrong |
|---|---|
| It assumes things and misunderstands scope | No spec contract. Gaps get filled silently instead of surfaced. |
| It reports done, but criteria are unmet | No machine-checkable definition of done, so nothing can fail. |
| Long sessions burn context; work happens one item at a time | No retrieval discipline or delegation policy. |
| `/compact` loses everything | State lives in the context window, which compaction destroys. |

Context Ledger's governing constraint:

> **The context window is a scratchpad, not a database.** Intent, plans, decisions
> and progress live on disk as reviewable files. A session is disposable; the
> ledger is not.

Three consequences follow, and they are the whole system:

1. **Ambiguity becomes an artifact.** Unanswered questions are written to a file
   that blocks planning. The agent can't assume past a file it must clear.
2. **Done becomes executable.** Acceptance criteria carry a verification command.
   A hook runs it and refuses to let the session end on failure.
3. **Work becomes shippable in units.** If a task is described completely enough
   to hand to a stranger, it can be handed to a subagent, a fresh session, a
   teammate, or CI — identically.

---

## Requirements

| | |
|---|---|
| **Claude Code** | any recent version with plugin support |
| **Python 3** | 3.8+. Pre-installed on macOS and every Linux. No packages needed. |
| **Git** | required only for the worktree tier and the `diff` verify kind. Levels 0 and 1 work fine without it. |
| **OS** | macOS, Linux. Windows via WSL or Git Bash. |

Nothing is installed into your project. No `node_modules`, no `requirements.txt`
entry, no `package.json` edit.

---

## Installation

In an interactive Claude Code session:

```
/plugin marketplace add ansh-n-chovatiya/context-ledger
/plugin install ctx@context-ledger
```

Or from any shell — VSCode, SSH, CI — where `/plugin` doesn't exist:

```bash
claude plugin marketplace add ansh-n-chovatiya/context-ledger
claude plugin install ctx@context-ledger
```

Verify it:

```bash
claude plugin list                 # ctx@context-ledger  0.1.1  ✔ enabled
claude plugin details ctx          # component inventory + token cost
```

## Updating

```
/plugin                            # → Manage plugins → ctx → Update
```

Or from a shell — and note this is **two commands**, not one:

```bash
claude plugin marketplace update context-ledger   # fetch the new commits
claude plugin update ctx@context-ledger           # install them
```

`claude plugin update` does not fetch anything. It reads the marketplace clone
already on disk, so without the first command it will cheerfully report that you
are on the latest version while sitting on a months-old build. It also compares
**declared version numbers, not commits** — it will follow the version backwards
and downgrade you if the clone is stale.

Restart Claude Code afterwards; the plugin is loaded at session start.

Check what you are actually running, rather than trusting the update output:

```bash
claude plugin list                                # declared version
ls ~/.claude/plugins/cache/context-ledger/ctx/    # one directory per installed version
```

### Choosing an install scope

```
/plugin install ctx@context-ledger --scope project         # this project only
```

```bash
claude plugin install ctx@context-ledger --scope project   # same, from a shell
```

| Scope | Available in | Trade-off |
|---|---|---|
| `user` (default) | every project | Convenient. Its always-on context cost applies everywhere, including projects with no `.ctx/`. |
| `project` | one project | Zero cost elsewhere. Install again per project. |
| `local` | one project, not committed | Same as project, but kept out of shared settings. |

The **hooks** are free everywhere either way — they're harness-side and add no
model context. It's the command descriptions that cost tokens. See
[What it costs](#what-it-costs).

### Installing from a local clone

For hacking on the plugin itself:

```bash
git clone https://github.com/ansh-n-chovatiya/context-ledger.git ~/tools/context-ledger
```

```
/plugin marketplace add ~/tools/context-ledger
/plugin install ctx@context-ledger
```

```bash
claude plugin marketplace add ~/tools/context-ledger
claude plugin install ctx@context-ledger
```

A local-directory marketplace loads **live from that directory**, so your edits
take effect in the next session with no reinstall. The trade-off: there's no
snapshot to update, so `claude plugin update ctx` reports `not found` — which is
expected, not a fault. Use `git pull` instead.

---

## Quick start

In any project you want to track:

```
/ctx:init
```

```
initialised .ctx  profile=code  level=L0
  verify  npm run typecheck  (available; not yet run)
  verify  npm test  (available; not yet run)
L0 is active: work is journalled to disk, and the hook briefing costs
~30 tokens per session (cap 61). See `claude plugin details ctx` for the
plugin's own always-on footprint, which is separate and larger.
```

That's the whole setup. `init` detects your project type, proposes verification
commands it can actually find on your PATH, and creates `.ctx/`.

**You are now at level 0, and it asks nothing of you.** Work normally. Edits are
recorded to disk, nothing is injected per turn, and no gate can block you.

Then, whenever you want it:

```
/ctx:resume      # what was I doing? — expands prior state on demand
/ctx:status      # level, active work, budget, recent activity
```

Commit `.ctx/` — it's designed to be reviewed in pull requests.

---

## The three levels

**This is the one concept worth understanding.** Ceremony is something you opt
*up* into. The default costs almost nothing, because a system that demands a spec
for a two-line fix gets abandoned — and abandonment is the only failure mode that
actually matters here.

| Level | You write | Gates active | Briefing | Reach for it when |
|---|---|---|---|---|
| **L0 · trace** *(default)* | nothing | none | ~30 tok | Anything you'd finish in one sitting without a checklist. |
| **L1 · tracked** | one task file | done-gate | ~96 tok | Criteria worth writing down; still one agent's work. |
| **L2 · planned** | spec + plan + units | ambiguity + done | ≤722 tok | Several pieces that could genuinely run independently. |

```
/ctx:task fix-token-refresh     # L0 → L1
/ctx:spec billing-migration     # L0/L1 → L2
/ctx:drop                       # back to L0 — deletes nothing
```

### When to escalate

Stay at **L0** unless one of these is true:

- The user stated acceptance criteria you'd otherwise have to remember.
- The work spans more than one session, or you expect compaction mid-task.
- Verification is worth automating because you'll run it repeatedly.

Go to **L2** only when the pieces have **disjoint write scopes**. Sequential steps
in one file are L1 with a numbered criteria list — not a plan.

De-escalate with `/ctx:drop` the moment ceremony stops paying for itself. Nothing
on disk is deleted; you just stop being gated.

---

## Walkthrough: a small change

You want a bug fixed, with criteria you care about.

**1 · Open a task.**

```
/ctx:task fix-token-refresh
```

Creates `.ctx/tasks/fix-token-refresh.md` and switches to L1. Claude fills in the
objective and criteria, then confirms the `verify` block actually proves them:

```markdown
---
ctx_schema: 1
task: fix-token-refresh
status: active
verify:
  - kind: cmd
    run: npm test -- auth/refresh
---

## Objective
Renew an expiring access token without interrupting an in-flight request.

## Acceptance criteria
1. A token expiring in under 60s triggers exactly one refresh.
2. Concurrent requests during a refresh share one in-flight promise.
3. A failed refresh surfaces AuthExpiredError, never a raw network error.
```

Write criteria that are **checkable**. "Handles errors properly" is not a
criterion; item 3 above is.

**2 · Work normally.** Every session start now re-states the objective and
criteria, so the agent can't drift off them across a compaction.

**3 · The gate closes on incomplete work.** When the session tries to end:

```
The done-gate blocked completion of `fix-token-refresh` (attempt 1 of 3).

cmd failed — npm test -- auth/refresh
exit 1
  ● refresh() shares an in-flight promise
    Expected 1 call, received 2
(full output: .ctx/runtime/verify/fix-token-refresh.log)

Acceptance criteria:
  1. A token expiring in under 60s triggers exactly one refresh.
  2. Concurrent requests during a refresh share one in-flight promise.

Fix what failed, then finish. After 3 attempts the gate stops and escalates
to the user, so do not guess repeatedly.
```

Output is truncated to 40 head + 20 tail lines with the full log on disk, so a
failing test suite can't flood the context.

**4 · It's bounded.** After three blocked attempts the gate stops, marks the task
`verify_failed`, and tells the agent to explain the problem rather than keep
guessing. You'll never watch it grind.

**5 · Check by hand any time:** `/ctx:verify`

**6 · Done?** `/ctx:drop` returns you to L0.

---

## Walkthrough: a large change

Work that splits into pieces which can run in parallel.

### Step 1 — Specify, and answer questions before building

```
/ctx:spec billing-migration
```

Claude reads the repo, writes acceptance criteria and an **Out of scope** section,
then records every question whose answer would change what gets built:

```
/ctx:ask
```

```
BLOCKING (2) — these must be answered before planning:
  1. Does the legacy /v1/invoices consumer still poll?
  2. Must idempotency keys survive a replay after 24h?
non-blocking (1) — proceed without if needed:
  1. Any preference on log format?
```

Claude asks these interactively and records each answer with its date, giving you
an audit trail of what was asked before work began.

**This gate is load-bearing, not advice:**

```
$ ctx plan billing-migration
refusing to plan: spec billing-migration has 2 unanswered
blocking question(s). Answer them first — /ctx:ask
  - Does the legacy /v1/invoices consumer still poll?
```

### Step 2 — Decompose into units

```
/ctx:plan billing-migration
```

Claude writes one file per unit. **The test each must pass: could an agent that
has never seen this conversation execute it?** That property is what makes a unit
dispatchable — and its absence is what produces half-finished work.

```yaml
---
unit: 03-token-refresh
plan: billing-migration
tier: subagent                 # inline | subagent | session
depends_on: [01-key-store]
owns:  [src/auth/refresh.ts]   # exclusive write scope
reads:                         # budgeted required reading
  - path: src/auth/key-store.ts
    symbols: [KeyStore, rotate]
forbid: [src/auth/session.ts]  # a concurrent sibling owns this
budget_tokens: 45000
verify:
  - kind: cmd
    run: npm test -- auth/refresh
---

## Objective
## Interfaces          ← exact signatures siblings code against
                      ← enforce them with a `symbol` check
## Acceptance criteria
## Return contract
```

**Cut along file ownership, not along phases of thought.** Three units that own
distinct files run in parallel; three that all edit one file are one unit with a
numbered criteria list.

### Step 3 — Check for collisions

```bash
ctx plan-check
```

Waves are **computed, never authored** — `depends_on` is the only place ordering
lives. Two checks run, both scoped to a single wave:

| Check | Catches |
|---|---|
| Disjoint ownership | two concurrent units writing the same path |
| No read/write races | a unit reading a path a **concurrent** unit rewrites |

The second matters because `owns` sets can be disjoint and the plan still be
wrong. Nothing is written while problems remain, and each names the exact fix:

```
plan billing-migration: 2 problem(s) — nothing was written
  - wave 1: 01-key-store and 03-rotate both own src/keys.ts
            — add `depends_on: [01-key-store]` to 03-rotate or split the paths
  - wave 1: 04-refresh reads src/clock.ts while 02-clock rewrites it
            — add `depends_on: [02-clock]` to 04-refresh
```

Collisions are **never auto-repaired**. Rewriting your dependency graph silently
isn't a favour. The same overlap across *different* waves is ordinary sequential
work and is deliberately not flagged.

Once clean:

```
plan billing-migration: 4 unit(s) in 2 wave(s) · graph r1
  wave 1: 01-key-store, 02-clock
  wave 2: 03-rotate, 04-refresh
```

### Step 4 — Dispatch a wave

```
/ctx:start
```

This prints a brief and **spawns nothing itself** — what to hand a subagent is
the harness's decision. Claude then sends the whole wave in a single message with
multiple Task calls, so the units genuinely run in parallel.

The brief repeats the rule that makes large plans affordable: **the orchestrator
reads unit files and unit reports, never source.** That's what keeps its context
flat across a twenty-unit plan.

Tiers:

| Tier | Runs as | Use for |
|---|---|---|
| `inline` | this session | trivial work, or a result needed immediately |
| `subagent` | own context window | analysis, review, research, most writing |
| `session` | own git worktree + branch | writes you want physically isolated |

For `session` units, `start` creates the worktree and prints the command to run:

```
cd .ctx/runtime/worktrees/03-rotate
ctx unit 03-rotate          # arms the done-gate for this unit
claude
```

A human stays in the loop on parallel writes by design. Use `--no-worktree` to
skip preparation.

### Step 5 — Land the work

```
/ctx:merge 03-rotate
```

Runs the done-gate **inside the unit's own worktree**, refuses to merge anything
that touched a path outside `owns`, merges on success, and removes the worktree.
A conflict here means an ownership contract was violated, so it stops and reports
rather than resolving.

```bash
ctx worktree list            # what's outstanding
ctx worktree remove 03-rotate --force   # discard a unit that went wrong
```

### Step 6 — Track and hand off

```
/ctx:status                  # wave board
/ctx:handoff mid-migration   # resume packet for another session or person
```

```
wave board — plan billing-migration:
  wave 1
     01-key-store             session   done
     02-clock                 subagent  done
  wave 2
   → 03-rotate                subagent  running
     04-refresh               subagent  pending
   next: wave 2 — /ctx:start
```

---

## Memory that survives sessions

### Cross-session continuity is automatic

You don't have to do anything. Hooks write state to disk as you work;
`PreCompact` leaves a resumable snapshot **without making a model call**, so
compaction stops being destructive. A new session reads a short briefing back.

`/ctx:resume` expands it on demand when you want more than the briefing carries.

### Context bundles — the portable convention

A bundle is **plain markdown with a fixed section schema**. Nothing about it
depends on this plugin: pasting one into a different tool, or a different model
entirely, is a supported path rather than a fallback.

```
/ctx:save billing-migration      # snapshot current understanding
/ctx:load billing-migration      # in any later session
/ctx:list                        # what exists
/ctx:promote billing-migration   # make it loadable from other projects
```

```markdown
---
ctx_bundle: 1
name: billing-migration
scope: project
tags: [billing, stripe]
---

# Context — billing-migration

## Situation           ← 2–5 sentences, present tense
## Established facts   ← only things you verified
## Decisions made      ← with pointers to ADRs
## Open questions      ← as `- [ ]` checkboxes
## Constraints         ← what must not change
## Artifacts           ← paths, diff ranges, tickets
## Resume here         ← one concrete next action
```

Bundles live in the repo, so they land in pull requests and get reviewed.
`/ctx:promote` copies one to `~/.claude/ctx/` for cross-project recall — kept
**manual on purpose**, because automatic cross-project memory is how you get a
confident assertion sourced from an unrelated codebase.

Resolution order for `/ctx:load`: project store → global store → treat the
argument as a file path.

### Standing context

Bundles named in `auto_load` have their content injected into every session:

```yaml
auto_load: [house-conventions]
```

Only **Constraints** and **Established facts** are carried — `Situation` and
`Resume here` describe one piece of work, so they're noise as standing context.

It's emitted last in the briefing, so if there isn't room the cap truncates
*this* rather than dropping your active task. At L0's 220-character cap almost
nothing fits; raise `briefing_chars.l0` if you want house rules there, then check
`ctx doctor` for truncation. A name that resolves to no bundle is reported in the
briefing rather than silently skipped.

### Decisions

```
/ctx:decide "Idempotency keys over a dedupe table"
```

Writes a numbered ADR to `.ctx/decisions/`. ADRs are immutable — reverse one by
writing a new ADR that supersedes it. The record of having changed your mind is
the point.

---

## Command reference

### Slash commands

| Command | Does |
|---|---|
| `/ctx:init` | Scaffold `.ctx/`, detect profile, propose verify commands |
| `/ctx:status` | Level, active work, briefing budget, wave board, recent journal |
| `/ctx:resume` | Expanded prior state, on demand |
| `/ctx:doctor` | Check layout, budgets, verify commands, gate state |
| **Level 1** | |
| `/ctx:task «name» [objective]` | Track one change at L1 with a done-gate |
| `/ctx:verify` | Run the done-gate by hand; `--sign-off rubric\|human` |
| `/ctx:drop` | Return to L0 trace, keeping the journal |
| **Level 2** | |
| `/ctx:spec «name» [— intent]` | Intent → checkable criteria → blocking questions |
| `/ctx:ask [name]` | Show and ask what's still blocking a spec |
| `/ctx:plan «name»` | Decompose a ready spec into dispatchable units |
| `/ctx:start [--wave N]` | Dispatch brief for the next wave |
| `/ctx:merge «unit»` | Land a unit's worktree branch after its gate passes |
| `/ctx:decide «title»` | Record an ADR |
| **Memory** | |
| `/ctx:save «name»` | Write a portable context bundle |
| `/ctx:load «name»` | Load a bundle: project → global → path |
| `/ctx:list` | Saved bundles, project and global |
| `/ctx:promote «name»` | Copy a bundle to the global store |
| `/ctx:handoff [name]` | Resume packet for another session, person or model |

Arguments are free text, not shell tokens. Claude Code splices what you typed
into the command line unquoted, so `/ctx:task add-search let users search flows`
and `/ctx:decide don't cache refresh tokens` both work without quoting. Every
command run with no arguments reports what it needs instead of failing, because
a non-zero exit aborts the slash command before its prompt can ask you.

**Exit codes.** A command exits non-zero only when a check *failed* or a refusal
is deliberate — a failing gate, an unanswered blocking question, an ownership
collision, a merge that did not land. Having nothing to do yet (no ledger, no
active task, no plan, no name given) exits 0 and says so.

### CLI

Everything above is also a CLI subcommand, which is what makes the same checks
runnable in CI and in scripts. Run via `bin/ctx` in the plugin directory.

```bash
ctx status
ctx doctor --verify        # actually run the verify commands
ctx doctor --clear         # drop a stale hook error log, then check
```

These have **no slash command by design** — none is a conversation, and every
slash command costs always-on context:

| Command | Does | Exit |
|---|---|---|
| `ctx ci [--plan X]` | Every headless check in one run | 0 / 1 |
| `ctx verify --plan X` | Run every unit's gate in a plan | 0 / 1 |
| `ctx migrate [--check]` | Upgrade ledger files; `--check` never writes | 0 / 1 |
| `ctx budget [--plan X]` | Predicted **and measured** context cost | 0 |
| `ctx telemetry` | Hook durations and injected briefing sizes | 0 |
| `ctx spec-ready [name]` | Gate 1 as an exit code | 0 / 1 |
| `ctx question «spec» «text»...` | Add questions; `--non-blocking` | 0 |
| `ctx resolve --question X --answer Y` | Record an answer | 0 / 1 |
| `ctx plan-unit «name»` | Scaffold one unit file | 0 |
| `ctx plan-check [name]` | Compute waves, check collisions | 0 / 1 |
| `ctx unit «name» [--status S]` | Focus a unit, or record its outcome. `--status done` runs the unit's gate first and refuses if it does not pass; `--force` overrides | 0 / 1 |
| `ctx worktree list\|remove` | Inspect or discard worktrees | 0 / 1 |
| `ctx level «0\|1\|2»` | Set the level directly | 0 |
| `ctx briefing` | Print exactly what SessionStart would inject | 0 |
| `ctx digest` | Regenerate `journal/DIGEST.md` | 0 |
| `ctx journal «kind» «target»` | Append one journal entry | 0 |

Global flags: `--cwd PATH` resolves the ledger from elsewhere; `--version`.

### Environment variables

| Variable | Effect |
|---|---|
| `CTX_GATE=off` | Disable the done-gate entirely. The escape hatch. |
| `CTX_UNIT` / `CTX_PLAN` | Claim a unit for **this process**. Overrides the shared pointer in `state.json`, so two sessions in one tree stop clobbering each other's focus. Worktree-tier units already get their own `.ctx/runtime/`, so they need neither. |
| `CTX_GLOBAL_ROOT` | Move the global bundle store (default `~/.claude/ctx`) |
| `CLAUDE_PROJECT_DIR` | Where ledger discovery starts |

---

## Configuration reference

`.ctx/ctx.yaml`, generated by `init` and safe to hand-edit. Comments survive
migrations.

```yaml
schema: 1                     # managed by `ctx migrate` — don't edit
profile: code                 # code | docs | research | infra | data
level: 0                      # starting level for new sessions

briefing_chars:               # hard cap on injected context, in characters
  l0: 220                     # ~3.6 chars per token
  l1: 900
  l2: 2600

journal:
  enabled: true               # false disables journalling entirely
  digest_lines: 12            # entries kept in DIGEST.md
  max_line_chars: 200         # per-entry truncation

gate:
  enabled: true               # false disables the done-gate
  max_attempts: 3             # blocks before it escalates to you
  output_head: 40             # failure output: leading lines kept
  output_tail: 20             # trailing lines kept
  timeout_seconds: 240        # budget for the whole gate, not per command

plan:
  wave_budget_tokens: 250000  # a wave over this refuses to dispatch

auto_load: []                 # bundles injected into every session
redact: []                    # extra regexes scrubbed before any write

verify_candidates: []         # extra commands `init` should consider, for a
                              # toolchain no marker table anticipates:
                              #   - bazel test //...
                              #   - ./scripts/check.sh

verify:                       # default checks inherited by new tasks/units
  - kind: cmd
    run: npm run typecheck
```

**On raising `briefing_chars`:** when `ctx doctor` reports a briefing was
truncated, the fix is to shorten the objective and criteria on disk. Raising the
cap recreates the problem the ledger exists to solve.

---

## Verification reference

For "any type of task" to hold, verification can't assume code. Six kinds,
ordered by how much they're trusted:

| Kind | Passes when | Trust | Typical use |
|---|---|---|---|
| `cmd` | Shell command exits 0 | objective | tests, typecheck, lint, build, `terraform validate` |
| `exists` | Path exists, optionally matching `matches:` regex | objective | generated docs, migrations, exports |
| `diff` | Changed files are a subset of `owns` | objective | scope enforcement on plan units |
| `symbol` | Every name in `contains:` still appears in `path` | objective | **interface freeze** — see below |
| `rubric` | The `verifier` subagent judges criteria against the diff | advisory | prose, research, design, API ergonomics |
| `human` | You sign off explicitly | authoritative | irreversible or outward-facing steps |

```yaml
verify:
  - kind: cmd
    run: pytest -q tests/auth
  - kind: exists
    path: docs/api.md
    matches: "## Authentication"
  - kind: symbol
    path: src/auth/refresh.ts
    contains: ["export function refresh(", "AuthExpiredError"]
  - kind: rubric
    about: the migration guide covers every breaking change
```

**`cwd` and `env` — monorepos.** `cmd`, `exists` and `symbol` each take an
optional `cwd:` relative to the repository root, and `cmd` also takes an `env:`
map layered over the session's environment. Without them every command ran at the
ledger's parent, so a repository whose ledger sits at the root could not express
"run this in `apps/web`":

```yaml
verify:
  - kind: cmd
    run: pnpm test
    cwd: apps/web
    env:
      CI: "1"
  - kind: cmd
    run: go test ./...
    cwd: services/api
```

A `cwd` that does not exist is a configuration error, so it warns and passes
rather than blocking — the same rule as a missing binary.

### Interface freeze

`symbol` is what turns "don't change a published interface" from advice into a
check. List the signatures a sibling unit is coding against; if one is renamed or
removed, the gate fails with:

```
symbol failed — src/auth/refresh.ts
no longer provides: export function refresh( — a sibling unit is coding
against this, so changing it is a planning decision. Report it instead of
adjusting the check.
```

It's a substring match, deliberately: crude enough to need no parser per
language, precise enough to catch the two dangerous cases (renamed, deleted) for
one file read.

**Checks run cheapest-first** — `diff` → `exists` → `symbol` → `cmd` → `rubric` — and
short-circuit on the first failure. A scope violation costs zero model tokens to
catch, because the model-based check never runs.

**Judged checks are recorded, not re-judged.** `rubric` and `human` need a model
or a person, so `/ctx:verify` evaluates them and records the sign-off in the work
file. **Any subsequent edit clears it** — a sign-off can't outlive the code it
signed off on. So verify last.

### Profiles

`init` detects a profile and proposes checks for it. `code` and `infra` get real
commands detected from your toolchain; the others fall back to judged checks,
because no command can decide whether prose is right.

Markers are **scored, not first-matched**, and weighted by how much they really
tell you. A build manifest at the root says what a project *is*; a directory
named `docs/` or `notebooks/` says only that the project has some, which projects
of every kind do. Highest score wins, and ties break toward the profile that has
commands to propose.

| Profile | Strong markers (10) | Weak markers (2–4) | Fallback |
|---|---|---|---|
| `code` | `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle[.kts]`, `Gemfile`, `composer.json`, `mix.exs`, `Package.swift`, `*.sln`, `*.csproj` | `setup.py` (8), `Makefile` (4) | none — commands are detected |
| `infra` | `main.tf`, `Chart.yaml` | `terraform/` (3) | `human` — review before applying |
| `docs` | `mkdocs.yml`, `docusaurus.config.js` | `docs/` (2) | `rubric` |
| `data` | `dbt_project.yml` | `notebooks/` (2) | `human` |
| `research` | *(explicit `--profile`)* | | `rubric` |

So a Python service that documents itself is `code`, not `docs`. Under the old
first-match order `docs/` won outright, which quietly left most repositories with
no runnable gate at all.

Override any of it with `--profile`. `init` also tells you when the only checks
it configured are judged ones — otherwise a project looks gated when everything
needs a model or a person.

### Failure policy

- The gate **fails closed** on a failing criterion and feeds back the exact check
  plus truncated output.
- **Scope enforcement watches the shell too.** `PreToolUse` matches `Bash`
  alongside the edit tools, so a `sed -i` or a redirect into a file outside `owns`
  raises the same nudge — it used to walk straight past. Reading a shell command
  is necessarily a heuristic, so the nudge is advisory; the `diff` kind reads git
  and stays the authoritative answer on what actually changed.
- **Infrastructure failure is not work failure.** A missing binary, exit 127, a
  timeout, or output matching an absent-tool signature (`No module named …`,
  `command not found`) warns and passes. Blocking on those would brick every
  session in a project whose toolchain isn't installed.
- Bounded at `gate.max_attempts`. Then it marks the work `verify_failed`, stops
  blocking, and escalates to you.
- **`gate.timeout_seconds` is the budget for the whole gate**, not for each
  command. A check that starts after the budget is spent is reported as a
  configuration error rather than run. Per command the limit was unenforceable:
  three commands at 240s each outlive the 300s `Stop` hook, and a killed hook
  returns no decision — so an over-long suite silently stopped gating anything.
- **The gate runs on `Stop`, not `SubagentStop`.** It belongs to the session that
  owns the work. Firing on every finishing subagent meant an unrelated search
  agent ran the whole test suite and could be blocked against criteria it had
  never touched.
- Failure output is scrubbed by `redact` before it reaches the model. The full
  log under `.ctx/runtime/verify/` is left raw — it is gitignored and local, and
  redacting it would hide the line you are debugging.
- `CTX_GATE=off` disables it outright.

---

## What lives on disk

```
.ctx/
  ctx.yaml                    profile, budgets, gate policy, redaction
  tasks/<slug>.md             L1 — one file per tracked change
  specs/<slug>/
      spec.md                 intent + acceptance criteria
      questions.md            questions → answers → dates (audit trail)
  plans/<slug>/
      README.md               human-facing plan, regenerated by plan-check
      plan.json               derived graph; prior revisions archived
      units/NN-name.md        one self-contained prompt file per unit
  contexts/
      index.md                catalogue
      <name>.ctx.md           portable bundles
  journal/
      YYYY-MM-DD.md           append-only, date-partitioned (merge-safe)
      DIGEST.md               mechanical tail, O(1) to read
  decisions/NNNN-slug.md      ADRs; immutable, superseded not edited
  runtime/                    GITIGNORED — machine-local only
      state.json              active level / spec / plan / unit
      telemetry.jsonl         hook durations, size-capped
      verify/*.log            full verify output
      worktrees/              session-tier checkouts
```

**Only `runtime/` is gitignored.** Everything else is authored to be reviewed in
a pull request.

The layout is shaped by one hard requirement: **concurrent agents must never
write the same file.** There's no central mutable state blob — unit status lives
in per-unit frontmatter, and the journal is partitioned by date.

---

## Continuous integration

```yaml
# .github/workflows/ledger.yml
name: ledger
on: [push, pull_request]
jobs:
  ledger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Get Context Ledger
        run: git clone --depth 1 https://github.com/ansh-n-chovatiya/context-ledger /tmp/ctx
      - name: Ledger schema is current
        run: /tmp/ctx/bin/ctx migrate --check
      - name: Ledger checks
        run: /tmp/ctx/bin/ctx ci
```

`ctx ci` fails on: a stale schema, a missing verify binary, an unanswered blocking
question, a plan with ownership collisions, or a briefing that had to truncate.

```
## ledger
  ok   layout complete
  ok   schema current
## budgets
  ok   L0 briefing fits without truncation
## spec
  FAIL billing has no open blocking questions — 1 unanswered

1 check(s) failed: billing has no open blocking questions
```

Add `ctx verify --plan <slug>` to run every unit's mechanical checks. Judged
checks are reported as awaiting sign-off rather than pretending an unattended run
can decide them.

---

## Operations

```bash
ctx doctor                 # layout, budgets, verify availability, gate state
ctx doctor --verify        # …and actually run the verify commands
ctx doctor --clear         # drop a stale hook error log first
ctx budget                 # predicted vs measured context cost
ctx telemetry              # hook durations, injected briefing sizes
ctx migrate --check        # is the ledger schema current?
ctx migrate                # upgrade it
```

**Migration** is idempotent, `--check` writes nothing, a ledger stamped *newer*
than the plugin is refused rather than downgraded, and `ctx.yaml` is edited
line-wise so your comments survive.

**Measurement.** `doctor` predicts what a briefing would cost; the hooks record
what sessions actually paid. `budget` shows both:

```
## briefing budget (predicted)
  ok   L0 94/220 chars (~26 tok)
## briefing actually injected (measured)
  12 session(s) recorded · median 93 chars (~26 tok)
## declared unit budgets — plan billing
  ok   wave 1: 60,000 of 250,000 tokens
```

---

## What it costs

Two separate costs, and they're often confused:

| | Cost | When |
|---|---|---|
| **Plugin always-on** | ~557 tok | every session, every project, at user scope |
| **Hook briefing** | ~30 tok (L0) · ~96 (L1) · ≤722 (L2) | every session in a ledger project |
| **Hooks themselves** | 0 | harness-side; no model context at all |
| **Per turn** | 0 | `UserPromptSubmit` is silent unless drift is detected |

So a real L0 session costs roughly **587 tokens**. Measure it yourself rather
than trusting this figure as it ages:

```bash
claude plugin details ctx
```

Four design decisions keep it there:

1. **`UserPromptSubmit` is silent by default.** Re-injecting criteria every turn
   would cost ~12k tokens across a long session to repeat what the model already
   has. Instead `PreToolUse` sets a one-shot flag when an edit strays out of
   scope, and the nudge is delivered once.
2. **The digest is a tail, not a summary.** Summarising costs tokens on a
   schedule. Journal lines are structured; the digest is the last N plus a count —
   O(1) regardless of project age, zero inference.
3. **Briefings are deterministic.** No clock time, no drifting counters. Identical
   state produces byte-identical text, so the prompt cache hits across sessions.
4. **Scripts, not agents.** Status boards, digests, collision checks and budget
   measurement are all Python. They cost nothing.

To cut it further: install with `--scope project`, or remove slash commands you
don't use. A command whose whole body is one shell call belongs in the CLI.

---

## Troubleshooting

**The gate keeps blocking and I can't finish.**
`CTX_GATE=off` disables it immediately. It's also bounded — three attempts, then
it escalates. If a check can't run at all, that's reported as a warning and never
blocks; run `ctx doctor --verify` to see which command is failing.

**`ctx: command not found`.**
Use the launcher: `~/tools/context-ledger/bin/ctx`. Add it to your PATH, or use
the slash commands, which resolve the path themselves.

**Nothing happens in my project.**
The plugin is silent without `.ctx/`. Run `/ctx:init`. Confirm with
`claude plugin list` that it's enabled.

**`/plugin isn't available in this environment`.**
`/plugin` is a built-in of the interactive terminal only — the VSCode extension,
headless runs and CI don't have it. Use the `claude plugin ...` CLI instead,
which works everywhere. See [Installation](#installation).

**`claude plugin update ctx` says "not found".**
Expected for a local-directory marketplace — the plugin loads live from the
directory, so there's nothing to update. `git pull` the repo instead.

**`ctx merge` refuses: "integration tree has uncommitted changes".**
Commit or stash first. Changes under `.ctx/` are excluded automatically, since
the ledger writes there itself.

**`ctx merge` reports a conflict.**
That means an ownership contract was violated — a unit wrote outside its `owns`.
It stops rather than resolving. Inspect the branch, fix the unit's scope, and
re-run.

**A briefing is truncated.**
Shorten the objective and criteria on disk. Raising `briefing_chars` recreates the
context problem this tool exists to solve. If it's `auto_load` being cut, that's
by design — standing context yields to active work.

**`plan-check` reports collisions I don't agree with.**
It never auto-repairs; each message names the exact `depends_on` line that fixes
it. If two units genuinely must write the same path, they're one unit.

**Hooks seem slow.**
`ctx telemetry` shows per-hook median and worst-case durations. `SessionStart` and
`UserPromptSubmit` sit in front of every turn. Check
`.ctx/runtime/hook-errors.log`.

**A hook is broken.**
Every hook except the done-gate **fails open** — errors are logged to
`.ctx/runtime/hook-errors.log` and the hook exits 0. A bug here can't brick your
session, including a bug in the gate itself.

---

## How it works

Everything durable is on disk. Hooks are the only traffic across the boundary,
and they run on harness events rather than on the model remembering to call them.

| Event | Responsibility | On error |
|---|---|---|
| `SessionStart` | Inject the budgeted, deterministic briefing | open |
| `UserPromptSubmit` | **Silent** unless a drift nudge is queued | open |
| `PreToolUse` | Queue a nudge on out-of-scope edits (L2), including shell writes | open |
| `PostToolUse` | Append to the journal; clear stale sign-offs | open |
| `PreCompact` | Flush state, write a mechanical autosave | open |
| `SessionEnd` | Finalise journal and digest | open |
| `Stop` | **The done-gate** | **closed** |

Note what isn't here: nothing depends on the model *choosing* to record state.
Persistence is a property of the harness, which is why it survives compaction,
crashes and your own `Ctrl-C`.

### Design principles

- **Waves are computed, never authored.** `depends_on` is the single source of
  truth for ordering.
- **`plan.json` is derived**, and prior revisions are archived rather than
  overwritten, so an in-flight wave can't be pulled out from under itself.
- **No check that can never fail.** A default that always passes makes an
  unguarded project look guarded — worse than no default.
- **Report, don't repair.** Collisions and merge conflicts name their fix and
  stop. Silently rewriting someone's plan isn't a favour.
- **Fail open everywhere but the gate.**

---

## Uninstalling

```
/plugin uninstall ctx
/plugin marketplace remove context-ledger
```

```bash
claude plugin uninstall ctx
claude plugin marketplace remove context-ledger
```

Your `.ctx/` directory is untouched — it's plain markdown and JSON, readable and
useful without the plugin. Delete it if you want it gone:

```bash
rm -rf .ctx
git worktree prune          # if you used the session tier
```

---

## Development

```bash
python3 -m unittest discover -s tests      # 248 tests, no dependencies
claude plugin validate . --strict
```

```
ctx/            the package — every decision that doesn't need a model
hooks/          three-line shims over ctx.hooks, so the contract has one seam
commands/       slash commands: a few lines each, logic lives in Python
agents/         unit-runner, verifier
skills/ledger/  when to escalate, and what belongs on disk
tests/          stdlib unittest
bin/ctx         launcher for CLI and CI use
```

The tests worth keeping green are the risk guards: briefing caps hold and
briefings are byte-stable; journal cost doesn't grow with history; hooks stay
silent in untracked projects and fail open on error; a missing tool never blocks
but a real failure always does; a judged sign-off is cleared by any edit;
collisions are caught within a wave and not across waves; `migrate --check` writes
nothing; and no profile ships an always-passing default.

---

## License

MIT © 2026 Ansh Chovatiya. See [LICENSE](LICENSE).
