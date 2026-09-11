# Context Ledger

**Durable project state for Claude Code.** Specs, plans, decisions and memory live
on disk instead of in the context window — so sessions become disposable,
compaction stops losing your work, and "done" becomes something a gate can refuse
to sign off on.

Python 3 standard library only. Nothing is added to your project's dependency
tree, and the plugin is completely silent in any project that hasn't opted in.

## Contents

[Why this exists](#why-this-exists) · [Requirements](#requirements) ·
[Installation](#installation) · [Quick start](#quick-start) ·
**[The three levels](#the-three-levels)** — the one concept to understand ·
[Walkthrough: a small change](#walkthrough-a-small-change) (L1) ·
[Walkthrough: a large change](#walkthrough-a-large-change) (L2) ·
[Review without commits](#review-without-commits) ·
[Memory that survives sessions](#memory-that-survives-sessions) ·
[Command reference](#command-reference) ·
[Configuration reference](#configuration-reference) —
including [Policy](#policy-system-user-repository) ·
[Verification reference](#verification-reference) —
including [Command trust](#command-trust) ·
[What lives on disk](#what-lives-on-disk) ·
[Continuous integration](#continuous-integration) · [Operations](#operations) ·
[What it costs](#what-it-costs) · [Troubleshooting](#troubleshooting) ·
[Security](#security) · [How it works](#how-it-works) ·
[Uninstalling](#uninstalling) · [Development](#development)

## Why this exists

Four common complaints about working with an AI coding agent have one shared
cause — **project state lives in a conversation instead of in a repository**:

| What you experience | What's actually wrong |
|---|---|
| It assumes things and misunderstands scope | No spec contract. Gaps get filled silently instead of surfaced. |
| It reports done, but criteria are unmet | No machine-checkable definition of done, so nothing can fail. |
| Long sessions burn context; work happens one item at a time | No retrieval discipline or delegation policy. |
| `/compact` loses everything | State lives in the context window, which compaction destroys. |

> **The context window is a scratchpad, not a database.** Intent, plans, decisions
> and progress live on disk as reviewable files. A session is disposable; the
> ledger is not.

Three consequences follow, and they are the whole system:

1. **Ambiguity becomes an artifact.** Unanswered questions go to a file that
   blocks planning. The agent can't assume past a file it must clear.
2. **Done becomes executable.** Acceptance criteria carry a verification command;
   a hook runs it and refuses to let the session end on failure.
3. **Work becomes shippable in units.** A task described completely enough to hand
   to a stranger can go to a subagent, a fresh session, a teammate or CI —
   identically.

## Requirements

| | |
|---|---|
| **Claude Code** | any recent version with plugin support |
| **Python 3** | 3.8+, and `pip` enforces it at install time from `Requires-Python: >=3.8` rather than failing at the first f-string. Pre-installed on macOS and every Linux. |
| **Dependencies** | None. The empty `dependencies` list in `pyproject.toml` is the machine-readable form of that: an installed copy reports no `Requires-Dist`. |
| **Git** | required only for the worktree tier and the `diff` verify kind. Levels 0 and 1 work fine without it. |
| **OS** | macOS, Linux, Windows. `bin/ctx` (POSIX) and `bin/ctx.cmd` (Windows) both wrap `bin/ctx.py`; `python3 -m ctx` works anywhere. CI runs the suite on all three. |

Nothing is installed into your project: no `node_modules`, no
`requirements.txt` entry, no `package.json` edit.

## Installation

In an interactive Claude Code session:

```
/plugin marketplace add ansh-n-chovatiya/context-ledger
/plugin install ctx@context-ledger
```

Or from any shell — VSCode, SSH, CI — where `/plugin` doesn't exist, and then
verify what you got:

```bash
claude plugin marketplace add ansh-n-chovatiya/context-ledger
claude plugin install ctx@context-ledger
claude plugin list                 # ctx@context-ledger  0.8.0  ✔ enabled
claude plugin details ctx          # component inventory + token cost
```

### As a Python package

The CLI is also a distribution, for putting `ctx` on `PATH` without a symlink:

```bash
pip install ./context_ledger-0.8.0-py3-none-any.whl   # or: pip install . in a clone
ctx --version                                          # ctx 0.8.0
```

The distribution is **`context-ledger`**; the import package and the console
script are both `ctx`, because `ctx` is not ours to take on an index. **It is not
published to PyPI** — `pip install context-ledger` from a public index does not
resolve, and the release workflow deliberately does not publish (see
[Releasing](#releasing)). Wheels come from a clone or from a GitHub Release.

A wheel gives you the CLI and nothing else: the slash commands, hooks, agents and
skills are plugin assets, so Claude Code still wants the plugin install above.

## Updating

`/plugin` → Manage plugins → ctx → Update, or from a shell — and note this is
**two commands**, not one:

```bash
claude plugin marketplace update context-ledger   # fetch the new commits
claude plugin update ctx@context-ledger           # install them
```

`claude plugin update` fetches nothing: it reads the marketplace clone already on
disk, so without the first command it reports you are current while sitting on a
months-old build. It compares **declared version numbers, not commits**, so a
stale clone downgrades you. Restart Claude Code afterwards, then check what you
are actually running rather than trusting the output:

```bash
claude plugin list                                # declared version
ls ~/.claude/plugins/cache/context-ledger/ctx/    # one directory per installed version
```

### Choosing an install scope

```bash
/plugin install ctx@context-ledger --scope project         # this project only
claude plugin install ctx@context-ledger --scope project   # same, from a shell
```

| Scope | Available in | Trade-off |
|---|---|---|
| `user` (default) | every project | Convenient; its always-on context cost applies everywhere, including projects with no `.ctx/`. |
| `project` | one project | Zero cost elsewhere. Install again per project. |
| `local` | one project, not committed | As `project`, but kept out of shared settings. |

The **hooks** are free everywhere either way — harness-side, no model context.
It's the command descriptions that cost tokens: [What it costs](#what-it-costs).

### Installing from a local clone

For hacking on the plugin itself:

```bash
git clone https://github.com/ansh-n-chovatiya/context-ledger.git ~/tools/context-ledger
claude plugin marketplace add ~/tools/context-ledger    # or /plugin marketplace add
claude plugin install ctx@context-ledger                # or /plugin install
```

A local-directory marketplace loads **live from that directory**: edits take
effect next session, and `claude plugin update ctx` reports `not found` because
there is no snapshot to update. Expected, not a fault — use `git pull`.

## Quick start

In any project you want to track:

```
/ctx:init
```

```
initialised .ctx  profile=code  level=L0
  verify  npm run typecheck  (available; not yet run)
  verify  npm test  (available; not yet run)
L0 is active: work is journalled to disk, and the hook briefing costs ~26 tokens
per session (cap ~61). `ctx budget` reports that as it changes; the plugin's own
always-on footprint is separate and larger: `claude plugin details ctx`.
```

That's the whole setup. `init` detects your project type, proposes verification
commands it can actually find on your PATH, and creates `.ctx/`. **You are now at
level 0, and it asks nothing of you:** work normally, edits are recorded to disk,
nothing is injected per turn, and no gate can block you. Then, when you want it:

```
/ctx:resume      # what was I doing? — expands prior state on demand
/ctx:status      # level, active work, budget, recent activity
```

Commit `.ctx/` — it is designed to be reviewed in pull requests.

## The three levels

**The one concept worth understanding.** Ceremony is opt-*up*. The default costs
almost nothing, because a system that demands a spec for a two-line fix gets
abandoned — the only failure mode that actually matters here.

| Level | You write | Gates active | Briefing | Reach for it when |
|---|---|---|---|---|
| **L0 · trace** *(default)* | nothing | none | ≤61 tok | Anything you'd finish in one sitting without a checklist. |
| **L1 · tracked** | one task file | done-gate | ≤250 tok | Criteria worth writing down; still one agent's work. |
| **L2 · planned** | spec + plan + units | ambiguity + done | ≤722 tok | Several pieces that could genuinely run independently. |

```
/ctx:task fix-token-refresh     # L0 → L1
/ctx:spec billing-migration     # L0/L1 → L2
/ctx:drop                       # back to L0 — deletes nothing
```

### When to escalate

Stay at **L0** unless the user stated acceptance criteria you'd otherwise have to
remember, the work spans more than one session (or you expect compaction
mid-task), or verification is worth automating because you'll run it repeatedly.

Go to **L2** only when the pieces have **disjoint write scopes**: sequential steps
in one file are L1 with a numbered criteria list, not a plan. De-escalate with
`/ctx:drop` the moment ceremony stops paying for itself — nothing on disk is
deleted, you just stop being gated.

## Walkthrough: a small change

You want a bug fixed, with criteria you care about.

**1 · Open a task.**

```
/ctx:task fix-token-refresh
```

Creates `.ctx/tasks/fix-token-refresh.md` and switches to L1. Claude fills in the
objective and criteria, then confirms the `verify` block proves them:

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

Write criteria that are **checkable**: "handles errors properly" is not one, item
3 above is.

**2 · Work normally.** Every session start re-states the objective and criteria,
so the agent can't drift off them across a compaction.

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
```

Output is truncated to 40 head + 20 tail lines with the full log on disk, so a
failing suite can't flood the context.

**4 · It's bounded.** After three blocked attempts the gate stops, marks the task
`verify_failed`, and tells the agent to explain rather than keep guessing.

**5 · Check by hand:** `/ctx:verify`. **6 · Done?** `/ctx:drop` returns you to L0.

## Walkthrough: a large change

Work that splits into pieces which can run in parallel.

### Step 1 — Specify, and answer questions before building

```
/ctx:spec billing-migration
```

Claude reads the repo, writes acceptance criteria and an **Out of scope**
section, then records every question whose answer changes what gets built:

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

Claude asks these interactively and records each answer with its date — an audit
trail of what was asked before work began. **The gate is load-bearing:**

```
$ ctx plan billing-migration
refusing to plan: spec billing-migration has 2 unanswered
blocking question(s). Answer them first — /ctx:ask
```

### Step 2 — Decompose into units

```
/ctx:plan billing-migration
```

Claude writes one file per unit. **The test each must pass: could an agent that
has never seen this conversation execute it?** That property is what makes a unit
dispatchable; its absence is what produces half-finished work.

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

**Cut along file ownership, not phases of thought.** Three units owning distinct
files run in parallel; three that all edit one file are one unit with a numbered
criteria list.

### Step 3 — Check for collisions

```bash
ctx plan-check
```

Waves are **computed, never authored**: `depends_on` is the only place ordering
lives. Two checks run, both scoped to one wave:

| Check | Catches |
|---|---|
| Disjoint ownership | two concurrent units writing the same path |
| No read/write races | a unit reading a path a **concurrent** unit rewrites |

The second matters because `owns` sets can be disjoint and the plan still wrong.
Nothing is written while problems remain, and each names its fix:

```
plan billing-migration: 2 problem(s) — nothing was written
  - wave 1: 01-key-store and 03-rotate both own src/keys.ts
            — add `depends_on: [01-key-store]` to 03-rotate or split the paths
  - wave 1: 04-refresh reads src/clock.ts while 02-clock rewrites it
```

Collisions are **never auto-repaired** — rewriting your dependency graph silently
isn't a favour — and the same overlap across *different* waves is ordinary
sequential work, deliberately not flagged. Once clean:

```
plan billing-migration: 4 unit(s) in 2 wave(s) · graph r1
  wave 1: 01-key-store, 02-clock       wave 2: 03-rotate, 04-refresh
```

### Step 4 — Dispatch a wave

```
/ctx:start
```

This prints a brief and **spawns nothing itself** — what to hand a subagent is the
harness's decision. Claude sends the whole wave in one message with multiple Task
calls, so the units genuinely run in parallel.

Dispatching also does two things on disk: every unit moves to `status: running`,
and each gets a content snapshot plus a sealed copy of its contract. Both are
evidence that has to predate the work, so **a second `/ctx:start` leaves an
already-dispatched unit's baseline and seal exactly alone** and says which units
it skipped — re-running after a crash is the documented recovery path, and it used
to re-snapshot finished work over its own completed state, handing the reviewer an
empty diff and an automatic `approved`. To retake one on purpose:

```
ctx start --rebaseline 03-rotate
```

That re-captures the *review baseline* over the tree as it stands now. It does
**not** re-seal the contract: if the unit's promise moved since dispatch,
`--rebaseline` refuses and names the fields that moved, because a runner holding
`Write` over its own unit file is exactly who would like a baseline retake to
bless a new promise. Accepting a new contract is a planning decision, so it has
its own door:

```
ctx start --reseal 03-rotate
```

`--reseal` re-records the dispatch point and the contract as the unit file now
reads, and retakes the baseline too. Both are journalled, both are repeatable, one
per unit, and recorded findings are kept.

The brief repeats the rule that makes large plans affordable: **the orchestrator
reads unit files and unit reports, never source.** That keeps its context flat
across a twenty-unit plan.

| Tier | Runs as | Use for |
|---|---|---|
| `inline` | this session | trivial work, or a result needed immediately |
| `subagent` | own context window | analysis, review, research, most writing |
| `session` | its own terminal | writes you want to drive yourself |

`session` units run **in this tree** by default: `ctx start` creates no worktree,
branch or anything else in git unless you ask.

```
export CTX_PLAN=auth-rotation CTX_UNIT=03-rotate
ctx unit 03-rotate          # arms the done-gate for this unit
```

`ctx start --worktree` opts into physical isolation instead: each `session` unit
gets a temporary checkout and branch under `.ctx/runtime/worktrees/`.

```
cd .ctx/runtime/worktrees/03-rotate
ctx unit 03-rotate
claude
```

That buys isolation and costs you this tree: a worktree holds its branch
exclusively, so while it exists `git checkout ctx/auth-rotation/03-rotate` here is
refused and you test inside the worktree. `ctx merge` gives the branch back.
Worth asking for, not worth taking by default — hence opt-in.

### Step 5 — Record each outcome

```
ctx unit 03-rotate --status done
```

This closes a unit, however it ran. It re-runs the unit's own verify checks first
and refuses if they do not pass — a report claiming success is not evidence of it
— and refuses too if *no* check could run, if the contract changed since dispatch,
or if the unit has no dispatch seal: see
[Ungated is not done](#ungated-is-not-done). Until it lands the unit stays
`running`.

**Only if you dispatched with `--worktree`** is there a branch to land:

```
/ctx:merge 03-rotate
```

That runs the done-gate **inside the unit's own worktree**, refuses anything that
touched a path outside `owns`, refuses to merge into a branch the worktree did not
fork from (and outright on a detached HEAD, where the merge would be unreachable),
names the branch it merged into, and removes the worktree and branch on success. A
conflict means the integration branch moved on or a contract was violated; it
stops and reports rather than resolving. Without `--worktree` there is no branch
and no merge — the work is already in your tree, uncommitted.

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
   → 03-rotate                subagent  running (in flight)
     04-refresh               subagent  running (in flight)
   next: wave 2 is in flight — 2 dispatched, none left to start; review what comes back
```

A unit is `pending` until `/ctx:start` dispatches it, `running (in flight)` until
`ctx unit … --status done` closes it, and `done` after; one dispatched around `ctx
start` shows as `unsealed`. A wave whose units are all `running` has been sent
out, so the board and `/ctx:next` point at the review instead of advising
`/ctx:start` again — which would change nothing, and used to overwrite the
baselines the review needs.

## Review without commits

A unit's own gate answers "do the checks pass", not "is this any good", and the
session that wrote the code is the worst seat from which to ask. So a completed
unit can be handed to a reviewer that never saw the implementer's transcript, is
not the model that wrote the code, and has no Bash and no Edit — read-only by tool
grant, not by instruction. Review sits between a unit reporting and `ctx unit
«unit» --status done`.

### Why a snapshot, not a commit range

A review has to answer two questions: what changed, and did anything change that
the unit never declared it owned. Both are normally answered with a commit range,
and that answer costs a commit — the implementer has to commit before the work can
be reviewed, so the protocol dictates the project's git history and concurrent
units interleave into one range nobody can read.

`ctx` answers both from content. A snapshot fingerprints *every* file in the
project — so a write to a path nobody declared is still visible — and stores the
bytes of the declared scope, which is what a diff is reconstructed from. One is
taken before a unit is dispatched and one after it reports; the difference is the
change. Nothing is committed and nothing is branched, so **a project with no git
repository at all reviews exactly the same way**, your uncommitted edits stay
uncommitted, and committing remains yours to do when you choose.

### The loop

```bash
/ctx:review 03-rotate
ctx review «unit» [--plan SLUG] [--round N]      # diff, package, stat summary
ctx snapshot «unit» [--plan SLUG] [--phase before|after]
ctx findings «unit» [--plan SLUG]                # id, severity, status, round
```

`ctx review` captures the "after" snapshot, diffs it against the "before" one
recorded at dispatch, writes a **review package** file and prints its path with a
one-line stat summary. With no "before" snapshot it refuses and names the command
that would have made one: reviewing against a snapshot taken after the fact yields
an empty diff. `ctx start` takes the "before" phase for every unit it dispatches,
so `ctx snapshot` is for work not dispatched through it.

The package is one file the reviewer reads in a **single call**: the unit's
objective and acceptance criteria quoted, its declared `owns`/`reads`/`forbid`, a
stat summary, a machine-computed scope-violation section, and a unified diff at 10
lines of context. Content is scrubbed through the redaction rules when the package
is *rendered*, not when the snapshot is captured — redacting on the way in makes
every credential-shaped line read as a change on the way out.

The **scope-violation section is decided before any model sees it.** Changed paths
are compared against the declared `owns` mechanically — covered or not — as a
Critical finding the reviewer does not re-adjudicate. `.ctx/` is excluded, or
every review would open with a violation against ctx's own journal writes. If a
snapshot hit its file cap, deletions are not reported and the package says so: an
accusation the snapshot cannot support is not made.

`/ctx:review` dispatches the `reviewer` subagent at the package path on the model
in `models.reviewer`, records what comes back with `ctx findings --add`, and acts
on the verdict. **The orchestrator reads the path, not the package.** Later rounds
dispatch `re-reviewer`, which verdicts the open findings against a package
covering only the fix and looks for damage the fix caused — a re-review that
wanders finds new work every round and never terminates.

### Findings are a file, not a conversation

The reviewer and the implementer share no context and neither survives
compaction. A finding that lives only in a transcript is gone by the third round,
and the gate has nothing to refuse on. So findings live in a file, one per unit,
and every state change is a write.

```bash
ctx findings «unit» --add important --summary "refresh swallows the 401" \
    --where src/auth/refresh.ts:88 --evidence "except: pass, no re-raise"
ctx findings «unit» --set 3 --status addressed
ctx findings «unit» --set 4 --status disputed --evidence "tests/auth_test.py:41"
ctx findings «unit» --set 5 --status parked --ruling "deferred to the rate-limit spec"
```

| Severity | Meaning |
|---|---|
| `critical` | broken behaviour, data loss, security |
| `important` | the unit cannot be trusted until it is fixed — a missed criterion, fragile behaviour, a swallowed error, a test that asserts nothing |
| `minor` | polish. Recorded, never entered into a fix loop |

A finding is `open`, or it left `open` by exactly one of three routes: `addressed`
(fixed), `disputed` (refuted, **evidence required**), `parked` (decided against,
**ruling required**).

**There is deliberately no `acknowledged` status.** Performative agreement —
"good catch, noted" — reads as progress, closes nothing, and leaves the defect in
the tree. It is not a state this store can represent, and that unavailability is
the mechanism: ask for one and the command names the three routes that exist.

A dispute is a technical claim, so it takes a `file:line` or command output, and
the reviewer's original evidence is appended to rather than overwritten. Parking
spends the user's judgement on their behalf, so it takes a ruling that stays in
the file for them to overturn.

**Three rounds is the ceiling** — the done-gate's own bound for attempts. A fourth
round of the same argument is a decision to escalate, not a retry; what is still
open at the cap gets parked with a ruling, and that belongs in an ADR via
`/ctx:decide`, not only in a findings file.

### The `review` verify kind

```yaml
verify:
  - kind: review
```

It passes when no `critical` or `important` finding is open and fails while any
is; `minor` never blocks, because a review that blocks on nits is one the
implementer learns to route around. Its cost sits between `symbol` and `cmd`, so
an open blocking finding is caught by one file read, before any test suite runs.

### Where the artifacts live

```
.ctx/plans/<slug>/findings/<unit>.md        the findings ledger — committed
.ctx/runtime/snapshots/<key>/               manifest + content — gitignored
.ctx/runtime/reviews/<slug>-<unit>-rN.md    the rendered package — gitignored
```

Findings are authored state and belong in the pull request beside the plan;
snapshots and packages are scratch — reconstructible and regenerated next round.

### What this does not do yet

There is **no final whole-plan review pass**: review is per unit, and nothing
reviews the assembled result of a wave or of a finished plan. There is no
debugging workflow — `ctx` has nothing to say about narrowing a failure to its
cause. (`test_first` covers red-before-green; a TDD workflow it is not.) Gaps,
named here rather than papered over.

## Memory that survives sessions

### Cross-session continuity is automatic

You don't have to do anything. Hooks write state to disk as you work;
`PreCompact` leaves a resumable snapshot **without making a model call**, so
compaction stops being destructive. A new session reads a short briefing back, and
`/ctx:resume` expands it on demand.

### Context bundles — the portable convention

A bundle is **plain markdown with a fixed section schema**. Nothing about it
depends on this plugin: pasting one into a different tool, or a different model,
is a supported path rather than a fallback.

```
/ctx:save billing-migration      # snapshot current understanding
/ctx:load billing-migration      # in any later session
/ctx:context                     # what exists
ctx promote billing-migration    # make it loadable from other projects
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

Bundles live in the repo, so they land in pull requests and get reviewed. `ctx
promote` copies one to `~/.claude/ctx/` for cross-project recall — **manual on
purpose**, because automatic cross-project memory is how you get a confident
assertion sourced from an unrelated codebase. `/ctx:load` resolves project store →
global store → file path.

### Standing context

Bundles named in `auto_load` have their content injected into every session:

```yaml
auto_load: [house-conventions]
```

Only **Constraints** and **Established facts** are carried — `Situation` and
`Resume here` describe one piece of work, so they're noise as standing context.

It's emitted last, so a tight cap truncates *this* rather than dropping your
active task. At L0's 220-character cap almost nothing fits; raise
`briefing_chars.l0` for house rules there, then check `ctx doctor` for truncation.
A name resolving to no bundle is reported, not silently skipped.

### Decisions

```
/ctx:decide "Idempotency keys over a dedupe table"
```

Writes a numbered ADR to `.ctx/decisions/`. ADRs are immutable — reverse one by
writing a new ADR that supersedes it; the record of having changed your mind is
the point.

## Command reference

### Slash commands

| Command | Does |
|---|---|
| `/ctx:init` | Scaffold `.ctx/`, detect profile, propose verify commands |
| `/ctx:status` | Level, active work, briefing budget, wave board, recent journal |
| `/ctx:resume` | Expanded prior state, on demand |
| `/ctx:next` | **Start here.** Names the single most useful next action, from ledger state |
| `/ctx:escalate` | Promote the active task to a spec, carrying its criteria across |
| `/ctx:doctor` | Check layout, budgets, verify commands, gate and policy state |
| `/ctx:task «name» [objective]` | **L1** — track one change with a done-gate |
| `/ctx:verify` | **L1** — run the done-gate by hand; `--sign-off rubric\|human` |
| `/ctx:drop` | **L1** — return to L0 trace, keeping the journal |
| `/ctx:spec «name» [— intent]` | **L2** — intent → checkable criteria → blocking questions |
| `/ctx:ask [name]` | **L2** — show and ask what's still blocking a spec |
| `/ctx:plan «name»` | **L2** — decompose a ready spec into dispatchable units |
| `/ctx:start [--wave N] [--worktree] [--rebaseline UNIT] [--reseal UNIT]` | **L2** — dispatch brief for the next wave |
| `/ctx:review «unit» [--round N]` | **L2** — adversarial review of a completed unit, from a snapshot diff |
| `/ctx:findings «unit»` | **L2** — findings raised against a unit; `--add`/`--set` to record or resolve one |
| `/ctx:merge «unit»` | **L2** — land a unit's worktree branch after its gate passes |
| `/ctx:decide «title»` | Record an ADR |
| `/ctx:save «name»` · `/ctx:load «name»` | Write a portable context bundle; load one (project → global → path) |
| `/ctx:context` | Saved bundles: what exists, and how to load, save or promote one |
| `/ctx:handoff [name]` | Resume packet for another session, person or model |

Arguments are free text, not shell tokens: the command files quote `$ARGUMENTS`,
so `/ctx:task add-search let users search flows` and `/ctx:decide don't cache
refresh tokens` both arrive as one argument and work without quoting. A command
run with no arguments reports what it needs instead of failing, because a non-zero
exit aborts the slash command before its prompt can ask you — which is also why
each command file ends its `!` line with `|| true`.

**Exit codes**, for every command, not a privileged seven. `0` is success or an
advisory notice; `1` is a check that failed — a gate, or an advisory condition
escalated by `--strict`; `2` is a refusal or an unexpected error. A refusal is
deliberate — an unanswered blocking question, an ownership collision, a merge that
did not land, no `.ctx/` in this directory at all — and its reason goes to
**stderr**, so stdout stays clean for whatever is reading it. An unexpected error
prints one line, `ctx «command» failed: «type»: «message»`; `CTX_DEBUG=1` restores
the traceback.

Having nothing to do *yet* is different and still exits 0: no active task or plan,
no name given, and a snapshot truncated by `review.max_files`. Those three are the
whole advisory set — the notice each prints *is* the answer — and `--strict` or
`CTX_STRICT=1` turns exactly those three into exit 1, nothing else. Having no
ledger at all is *not* advisory: `ctx status` with no `.ctx/` exits 2 and points
at `/ctx:init`. Don't export `CTX_STRICT=1` in your shell profile: under it a bare
`/ctx:task` exits 1 and Claude Code abandons the command before it can ask you for
the name.

### CLI

Everything above is also a CLI subcommand, which is what makes the same checks
runnable in CI and in scripts. Run via `bin/ctx` in the plugin directory, or the
`ctx` the [package install](#as-a-python-package) puts on `PATH`.

These have **no slash command by design**: none is a conversation, and every
slash command costs always-on context.

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
| `ctx trust [--yes\|--lock\|--verify-lock]` | Review and accept the shell commands the gate will run on this machine. `--lock` writes the committed `.ctx/trust.lock`; `--verify-lock` checks against it and accepts nothing — see [Command trust](#command-trust) | 0 / 1 |
| `ctx prune [--before D]` | Fold journal days older than `D` (or `journal.keep_days`) into monthly archives | 0 / 1 |
| `ctx unit «name» [--status S]` | Focus a unit, or record its outcome. `--status done` runs the unit's gate first and refuses if it does not pass, if no check could run at all, or if the unit's contract changed since dispatch. `--force` overrides and journals what it overrode — see [Ungated is not done](#ungated-is-not-done) | 0 / 1 |
| `ctx snapshot «unit» [--phase P]` | Capture a content snapshot by hand. `ctx start` takes the `before` phase itself | 0 / 1 |
| `ctx worktree list\|remove` | Inspect or discard worktrees | 0 / 1 |
| `ctx level «0\|1\|2»` | Set the level directly | 0 |
| `ctx briefing` | Print exactly what SessionStart would inject | 0 |
| `ctx digest` | Regenerate `journal/DIGEST.md` | 0 |
| `ctx journal «kind» «target»` | Append one journal entry | 0 |

Two flags on a command that *does* have a slash command earn their own lines,
because a plain re-run now deliberately does less than it used to:

| Command | Does | Exit |
|---|---|---|
| `ctx start --rebaseline «unit»` | Re-capture this unit's **review baseline** over the tree as it stands now, replacing what dispatch recorded. It does **not** re-seal the contract, and refuses if the contract moved since dispatch | 0 |
| `ctx start --reseal «unit»` | The deliberate route the refusal above names: re-records the dispatch commit *and* the contract as the unit file now reads, and retakes the baseline too. Both are journalled; a plain `ctx start` re-run **no longer re-snapshots** a unit it has already dispatched | 0 |

Global flags: `--cwd PATH` resolves the ledger from elsewhere; `--version`.

### Environment variables

| Variable | Effect |
|---|---|
| `CTX_GATE=off` | Disable the done-gate (`off`, `0`, `false` and `disabled` are one decision). Not free: every use is journalled, and a policy can refuse it — see [Policy](#policy-system-user-repository). |
| `CTX_UNIT` / `CTX_PLAN` | Claim a unit for **this process**. Overrides the shared pointer in `state.json`, so two sessions in one tree stop clobbering each other's focus. Worktree-tier units already get their own `.ctx/runtime/`, so they need neither. |
| `CTX_GLOBAL_ROOT` | Move the global bundle store (default `~/.claude/ctx`) |
| `CLAUDE_PROJECT_DIR` | Where ledger discovery starts |

## Configuration reference

`.ctx/ctx.yaml`, generated by `init`, safe to hand-edit; comments survive
migrations. What a policy layer above it can override is
[below](#policy-system-user-repository).

```yaml
schema: 1                     # managed by `ctx migrate` — don't edit
profile: code                 # code | docs | research | infra | data
level: 0                      # starting level for new sessions

# hard cap on injected context, in characters, at ~3.6 chars per token
briefing_chars:
  l0: 220
  l1: 900
  l2: 2600

journal:
  enabled: true               # false disables journalling entirely
  digest_lines: 12            # entries kept in DIGEST.md
  max_line_chars: 200         # per-entry truncation
  keep_days: 0                # `ctx prune` folds older days into a monthly
                              # archive. 0 keeps everything.

telemetry:
  enabled: true               # hook timings and briefing sizes, to gitignored
                              # .ctx/runtime/. Local-only; nothing is transmitted.

gate:
  enabled: true               # false disables the done-gate
  max_attempts: 3             # blocks before it escalates to you
  output_head: 40             # failure output: leading lines kept
  output_tail: 20             # trailing lines kept
  timeout_seconds: 240        # budget for the whole gate, not per command
  # allow_override: true      # not written by `init`; when a policy sets it
                              # false and locks it, CTX_GATE=off is refused

plan:
  wave_budget_tokens: 250000  # a wave over this refuses to dispatch

# content snapshots — see Review without commits
review:
  ignore: []                  # replaces the default exclusions (.git,
                              # node_modules, build output, .ctx/runtime …)
  max_file_bytes: 2000000     # larger files are fingerprinted, never stored
  max_files: 20000            # a bound so a snapshot can't become the slow part
                              # of a dispatch; hitting it is reported

auto_load: []                 # bundles injected into every session
redact: []                    # extra regexes scrubbed before any write
verify_candidates: []         # extra commands `init` should consider, for a
                              # toolchain no marker table anticipates

# default checks inherited by new tasks and units
verify:
  - kind: cmd
    run: npm run typecheck
  - kind: cmd
    run: npx playwright test
    optional: true            # non-blocking when its *tool* looks absent — read
                              # the warning under Verification reference first
```

**On raising `briefing_chars`:** when `ctx doctor` reports a briefing was
truncated, the fix is to shorten the objective and criteria on disk. Raising the
cap recreates the problem the ledger exists to solve.

### Policy: system, user, repository

`ctx.yaml` is committed and editable by anyone who can open a pull request, so it
cannot also decide what a pull request may not change. Two files above it can:
`/etc/ctx/policy.yaml` (`%PROGRAMDATA%\ctx\policy.yaml` on Windows) is the system
layer, `~/.claude/ctx/policy.yaml` — or `$CTX_GLOBAL_ROOT/policy.yaml` — the user
one. Precedence is **system → user → repository**, later winning, *except* where
an earlier layer locked the key. A policy file is `ctx.yaml` syntax plus `locked:`,
in either of two forms:

```yaml
# list form — the value lives in the body, `locked:` names the key
gate:
  allow_override: false
locked: [gate.allow_override]

# mapping form — sets and locks in one line. Use one form or the other.
locked:
  gate.allow_override: false
```

A lock covers its prefixes — `locked: [gate]` locks `gate.enabled` too — and
**`locked:` inside `.ctx/ctx.yaml` is ignored**, because a repository cannot lock
its own settings. `ctx` says so rather than dropping it silently.

**An unlocked policy value loses to any initialised repository.** `ctx init`
renders *every* default key into `ctx.yaml`, so `gate: {max_attempts: 9}` in a
user policy is overwritten by the `max_attempts: 3` that `init` wrote. That is
not a bug to work around: `locked:` is the lever, and a policy that states a value
without locking it is a default, not a control.

`ctx doctor`'s `## policy` section answers *why is this setting what it is* in one
command — each layer and whether it is present, every live value from above the
repository with the layer that set it, and every key a lower layer tried to change
and was refused:

```
## policy
  none system     /etc/ctx/policy.yaml  (absent)
  ok   user       ~/.claude/ctx/policy.yaml
       gate.max_attempts = 9 (from user, locked)
  ok   repo       .ctx/ctx.yaml
  BAD  repo sets gate.max_attempts=3; user policy locks it to 9 — the locked value holds
```

A policy file that does not parse, or that is not a mapping, **stops the command**
(exit 2) and names the file: a control plane that fails soft is a control silently
not applied. One gap to know about — a policy file that cannot be *opened* (wrong
permissions) is currently skipped as if absent, so a root-owned `0600` policy does
not apply to anyone else and does not say so.

## Verification reference

For "any type of task" to hold, verification can't assume code. Eight kinds,
ordered by how much they are trusted:

| Kind | Passes when | Trust | Typical use |
|---|---|---|---|
| `cmd` | Shell command exits 0 | objective | tests, typecheck, lint, build, `terraform validate` |
| `exists` | Path exists, optionally matching `matches:` regex | objective | generated docs, migrations, exports |
| `diff` | Changed files are a subset of `owns` | objective | scope enforcement on plan units |
| `symbol` | Every name in `contains:` still appears in `path` | objective | **interface freeze** — see below |
| `review` | No `critical` or `important` review finding is open | objective | adversarial review of a unit — see [Review without commits](#review-without-commits) |
| `test_first` | A recorded **failing** run of the paths in `tests:` predates the implementation snapshot | objective | red-before-green on a fix; a unit with no recorded run fails rather than passing |
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
  - kind: review
  - kind: rubric
    about: the migration guide covers every breaking change
```

**`cwd` and `env` — monorepos.** `cmd`, `exists` and `symbol` each take a `cwd:`
relative to the repository root, and `cmd` also takes an `env:` map layered over
the session's environment. Without them every command ran at the ledger's parent,
so a ledger at the root could not express "run this in `apps/web`":

```yaml
verify:
  - kind: cmd
    run: pnpm test
    cwd: apps/web
    env:
      CI: "1"
```

A `cwd` that does not exist is a configuration error, so it warns rather than
failing — the same rule as a missing binary. It stops being harmless when it is
the *only* thing that happened: see [Ungated is not done](#ungated-is-not-done).

**`exists` and `symbol` read inside the project only.** Both take a path relative
to the repository root; an absolute path, a `~`, or a `cwd:`/`..` that climbs out
is refused as a configuration error before the file is touched. Neither runs
anything, so neither goes through `ctx trust` — but both report a verdict about a
file's *contents*, and a committed `ctx.yaml` naming `/home/you/.ssh/id_rsa` would
turn the gate into a one-bit oracle over any file you can read. `matches:` is
bounded by the gate's remaining `gate.timeout_seconds`: a regex out of a committed
file is input you did not write, handed to a backtracking engine.

**`optional: true` — a check whose tool may legitimately be absent.** On a `cmd`
check, `optional` re-enables the old absent-tool sniffing: a non-zero exit whose
output looks like a missing toolchain (`No module named …`, `… command not found`)
is reported as a configuration error instead of a failure.

```yaml
  - kind: cmd
    run: npx playwright test
    optional: true                   # browsers may not be installed here
```

Use it for the one check whose tool some machines genuinely will not have — e2e
browsers, a proprietary linter, a GPU suite — and nowhere else. **An `optional`
check stops blocking the moment its output resembles an absent tool, and the
work's own failures can resemble one:** a unit that deletes a module makes pytest
print `No module named 'app.foo'`, which reads as a missing toolchain and walks
the regression through the gate. That laundering is why sniffing is no longer the
default (see [Failure policy](#failure-policy)); `optional` asks for it back, per
check, by name.

### Interface freeze

`symbol` is what turns "don't change a published interface" from advice into a
check. List the signatures a sibling unit is coding against; if one is renamed or
removed, the gate fails with:

```
symbol failed — src/auth/refresh.ts
no longer provides: export function refresh( — a sibling unit is coding
against this, so changing it is a planning decision. Report it instead.
```

It's a substring match, deliberately: crude enough to need no parser per language,
precise enough to catch renamed and deleted for one file read.

**Checks run cheapest-first** — `diff` → `exists` → `symbol` → `review` /
`test_first` → `cmd` → `rubric` — and short-circuit on the first failure, so a
scope violation costs zero model tokens to catch and an open blocking finding is
caught by one file read rather than a test suite.

**Judged checks are recorded, not re-judged.** `rubric` and `human` need a model
or a person, so `ctx verify` reports them as *pending*. `/ctx:verify` delegates: a
`rubric` to the `verifier` subagent, a `human` to you, and the sign-off is recorded
with `ctx verify --sign-off rubric` (or `human`). **Any subsequent edit clears
it** — a sign-off can't outlive the code it signed. So verify last.

### Profiles

`init` detects a profile and proposes checks for it: `code` and `infra` get real
commands detected from your toolchain, the others fall back to judged checks
because no command can decide whether prose is right.

Markers are **scored, not first-matched**, and weighted by how much they really
tell you — a build manifest at the root says what a project *is*, while `docs/` or
`notebooks/` says only that it has some, which projects of every kind do. Highest
score wins; ties break toward the profile with commands to propose.

| Profile | Strong markers (10) | Weak markers (2–4) | Fallback |
|---|---|---|---|
| `code` | `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle[.kts]`, `Gemfile`, `composer.json`, `mix.exs`, `Package.swift`, `*.sln`, `*.csproj` | `setup.py` (8), `Makefile` (4) | none — commands are detected |
| `infra` | `main.tf`, `Chart.yaml` | `terraform/` (3) | `human` — review before applying |
| `docs` | `mkdocs.yml`, `docusaurus.config.js` | `docs/` (2) | `rubric` |
| `data` | `dbt_project.yml` | `notebooks/` (2) | `human` |
| `research` | *(explicit `--profile`)* | | `rubric` |

So a Python service that documents itself is `code`, not `docs`; under the old
first-match order `docs/` won outright, leaving most repositories with no runnable
gate. Override with `--profile`. `init` also says when the only checks it
configured are judged ones — otherwise a project looks gated when everything needs
a model or a person.

### Command trust

`ctx.yaml` is committed and `verify.cmd.run` is executed with `shell=True` **by a
hook**, which never goes through the tool permission prompt — so cloning a
repository and escalating to L1 would otherwise run whatever that file says.
Acceptance is therefore recorded per command, machine-locally, in gitignored
`.ctx/runtime/verify.trust`:

- `ctx init` accepts what it configured — you watched it propose and print them.
- Anything else is reported by the gate as a configuration error and **not run**,
  so an unaccepted ledger is *ungated*, never *broken* — the missing-binary rule.
- `ctx trust` lists what is pending and the file each command came from;
  `--yes` accepts them, and `ctx doctor` and `ctx ci` report the count.

Acceptance is per command, not per file: a task or unit carries its own snapshot
of the `verify` block, so trusting `ctx.yaml` would say nothing about what a unit
file runs. Changing a command's text, its `cwd` or its `env` makes it a new
command and revokes acceptance.

**The lockfile is the other half, and it is what CI checks.** Machine acceptance
says nothing on an ephemeral runner, where the only way to satisfy it is `ctx
trust --yes` — which accepts whatever the branch under test declares. So the
review moves to where reviews already happen:

```bash
ctx trust --lock            # writes .ctx/trust.lock — accepts nothing
git add .ctx/trust.lock     # commit it: the diff is the review
ctx trust --verify-lock     # what CI runs; exit 1 if anything is unlocked
```

`.ctx/trust.lock` is committed and deterministic — sorted by command id, no
timestamp, no hostname, no absolute path, `\n` endings — and carries each
command's `run`, `cwd` and `env` beside its id so a human can read the diff rather
than recompute a hash. Adding a command to `ctx.yaml`, or editing one character of
an existing `run`, makes a new id that is absent from the lockfile, and CI fails
until someone reviews the new line in. Where a lockfile exists, `ctx ci` gates on
it and reports local acceptance as a note instead.

### Failure policy

- The gate **fails closed** on a failing criterion and feeds back the exact check
  plus truncated output.
- **Scope enforcement watches the shell too.** `PreToolUse` matches `Bash`
  alongside the edit tools, so a `sed -i` or a redirect outside `owns` raises the
  same nudge. Reading a shell command is a heuristic, so the nudge is advisory;
  the `diff` kind reads git and stays authoritative on what actually changed.
- **Infrastructure failure is not work failure.** A missing binary, exit 127
  (9009 on `cmd.exe`), an interpreter that cannot import the module it was told
  to run, a timeout, or a `cwd` that does not exist all warn instead of failing.
  Blocking on those would brick every session in a project whose toolchain isn't
  installed.
- **Which of the two a check is, is decided by how the launcher failed — never by
  reading the command's output.** Whether a tool exists is a question about this
  machine, asked of it before the command runs; the exit code says the rest.
  Sniffing output for `No module named …` was a laundering machine: a unit that
  deleted a module produced that string inside pytest's own report, and the
  regression the gate exists to catch became a warning. A project that wants that
  leniency asks for it by name with [`optional: true`](#verification-reference).
- Bounded at `gate.max_attempts`. Then it marks the work `verify_failed`, stops
  blocking, and escalates to you.
- **`gate.timeout_seconds` is the budget for the whole gate**, not per command; a
  check starting after the budget is spent is a configuration error rather than
  run. Per command it was unenforceable: three commands at 240s each outlive the
  300s `Stop` hook, and a killed hook returns no decision, so an over-long suite
  silently stopped gating anything.
- **The gate runs on `Stop`, not `SubagentStop`** — it belongs to the session that
  owns the work. Firing on every finishing subagent meant an unrelated search agent
  ran the whole suite and could be blocked against criteria it never touched.
- Failure output is scrubbed by `redact` before it reaches the model. The full log
  under `.ctx/runtime/verify/` is left raw: gitignored, local, and redacting it
  would hide the line you are debugging.
- **`CTX_GATE=off` is journalled, not silent.** `off`, `0`, `false` and
  `disabled` are one decision, and each use appends a `gate | CTX_GATE` entry
  naming the site that honoured it. A locked `gate.allow_override: false` refuses
  it outright — the gate runs, and the refusal is journalled too. If the journal
  will not take the line the bypass still happens and says so on stderr and in
  `.ctx/runtime/hook-errors.log`: unrecorded, never unnoticed. See
  [Policy](#policy-system-user-repository). Caveat: the `Stop` hook still reads
  the variable through its own inline check, so a bypass *there* is not yet
  journalled or refusable. `ctx doctor` is.

### Ungated is not done

`ctx unit «name» --status done` is the transition that claims work is finished. A
failing check has always refused it; three more refusals join that one, and all
are refusals rather than warnings. `--force` is the only way past any of them, and
it records what it stepped over.

**A gate in which no check reached `pass` refuses the transition.** Not one check
failing — *not one check running*. An unrunnable check used to warn and mark the
unit done, and on the default `code` profile, which carries only `cmd` checks, a
machine that has never run `ctx trust` errors **every** one. Clone a repo, `ctx
start`, `ctx unit 01-api --status done`, and the board went green with zero checks
executed. Now:

```
$ ctx unit 01-key-store --status done
refusing to mark 01-key-store done — not one check could run, so nothing about
this unit was verified:
  warn    cmd: echo ok
            this command has not been accepted on this machine — review it and
            run `ctx trust` to allow it
...
Fix the configuration and re-run, or pass --force to override.
```

An ERROR alongside at least one `pass` is still only a warning: the refusal is for
*nothing ran*, never *something errored*. `ctx merge` refuses on the same condition
inside the unit's worktree, so a merge is no way around it; `--skip-gate` is that
unit's `--force` and names the checks it skipped in the merge journal entry.

**The `Stop` hook is deliberately not changed.** Ending a turn is not a claim that
the work is finished, so a check that could not run still lets the session end and
is journalled as *incomplete, not blocking* — blocking there would brick every
session in a project whose toolchain is not installed. The refusal belongs to the
transition that makes the claim.

**A unit's contract is compared against what was sealed at dispatch.** `ctx start`
records a digest of the fields that constitute the promise — `verify`, `owns`,
`reads`, `forbid`, `depends_on`, `verified`, and the acceptance criteria —
alongside the review baseline, and the done-gate refuses if it moved. `status:` is
deliberately not in the digest: flipping it is the edit the transition makes.

This closes a hole opened by a rule that has to stay: everything under `.ctx/` is
exempt from the `diff` scope check (concurrent units write to the ledger
constantly, and counting that as a violation deadlocks the wave) and the runner
holds `Write`. So a unit that could not make the tests pass could edit the test
instead — delete the failing `verify:` entry, trim a criterion:

```
$ ctx unit 01-key-store --status done
refusing to mark 01-key-store done — its contract changed after it was dispatched:
  changed: verify
...
Restore what changed from the plan, or — if the change is a real
planning decision — say so out loud and pass --force.
```

Blocking findings are sealed the same way and the seal never weakens: recording or
resolving one through `ctx findings --add`/`--set` is authoritative and re-seals,
while a `critical` or `important` finding deleted by hand, downgraded, or moved out
of `open` behind ctx's back is drift and reads as a changed contract —
`changed: finding [1] (important) was deleted from the findings file`.

**A unit with no dispatch seal at all is refused**, and `ctx status` shows it as
`unsealed (running — no ctx start, nothing recorded to judge it)` instead of by
its own `status:`. A seal is written by `ctx start` and by nothing else, so no
seal means no contract, no review baseline and no dispatch commit — three of this
gate's four guarantees absent at once. The refusal is scoped to plans that seal at
all: where *no* unit has a seal, the plan predates the seal or was never dispatched
through `ctx start`, and refusing there would brick every in-flight plan on
upgrade. Where the siblings are sealed and this one is not, it was dispatched
around the ledger — the case worth refusing. A seal that exists but carries no
stored baseline or no dispatch commit still falls through, with a note saying so.

**The `diff` check compares against the commit recorded at dispatch**, not the
working tree alone, so a unit that *commits* a file outside `owns` is caught just
as one that leaves it dirty is. If that commit is gone from the history — amended,
rebased or reset away while the unit ran — the check **fails** rather than
erroring: the gate will not read "cannot tell" as "nothing changed". `ctx start
--reseal «unit»` re-records the dispatch point over the current HEAD.

**Both overrides leave a trail.** `--force` still runs the gate, it just does not
obey it: it prints what it refused and journals `done (--force overrode the gate:
«reason»)`, or `done (--force; the gate passed anyway)`. `ctx merge --skip-gate`
journals `ok (--skip-gate overrode the gate: …)` with the checks it did not run.
One `grep` for "overrode the gate" finds every override, whichever door it used.

## What lives on disk

```
.ctx/
  ctx.yaml                    profile, budgets, gate policy, redaction
  trust.lock                  reviewed verify commands — committed
  tasks/<slug>.md             L1 — one file per tracked change
  specs/<slug>/
      spec.md                 intent + acceptance criteria
      questions.md            questions → answers → dates (audit trail)
  plans/<slug>/
      README.md               human-facing plan, regenerated by plan-check
      plan.json               derived graph; prior revisions archived
      units/NN-name.md        one self-contained prompt file per unit
      findings/<unit>.md      review findings; severity, status, evidence, ruling
  contexts/index.md           catalogue; <name>.ctx.md are portable bundles
  journal/
      YYYY-MM-DD.md           append-only, date-partitioned (merge-safe)
      archive-YYYY-MM.md      older days folded up by `ctx prune`
      DIGEST.md               mechanical tail, O(1) to read
  decisions/NNNN-slug.md      ADRs; immutable, superseded not edited
  runtime/                    GITIGNORED — machine-local only
      state.json / .lock      active level / spec / plan / unit
      verify.trust            commands accepted on this machine
      telemetry.jsonl         hook durations, size-capped (switchable)
      verify/*.log            full verify output
      snapshots/<key>/        before/after content snapshots for review
      reviews/*.md            rendered review packages, one per round
      worktrees/              session-tier checkouts
```

**Only `runtime/` is gitignored.** Everything else — including `trust.lock` — is
authored to be reviewed in a pull request. The layout is shaped by one hard
requirement: **concurrent agents must never write the same file.** There is no
central mutable state blob; unit status lives in per-unit frontmatter and the
journal is partitioned by date.

## Continuous integration

Commit `.ctx/trust.lock` first (`ctx trust --lock`), then:

```yaml
# .github/workflows/ledger.yml
name: ledger
on: [push, pull_request]
permissions:
  contents: read
jobs:
  ledger:
    runs-on: ubuntu-latest
    env:
      CTX_REF: v0.8.0          # a tag or a full commit SHA — never a branch
    steps:
      - uses: actions/checkout@v4
      - name: Get Context Ledger, pinned
        run: |
          git clone --filter=blob:none --no-checkout \
            https://github.com/ansh-n-chovatiya/context-ledger /tmp/ctx
          git -C /tmp/ctx checkout --detach "$CTX_REF"
      - name: Ledger schema is current
        run: /tmp/ctx/bin/ctx migrate --check
      - name: Every verify command is one somebody reviewed
        run: /tmp/ctx/bin/ctx trust --verify-lock
      - name: Ledger checks
        run: /tmp/ctx/bin/ctx ci
```

Two things there are load-bearing, and simplifying either is how a pull request
gets a shell in your CI. **The clone is pinned** — `--depth 1` of a default branch
runs whatever was pushed to a personal account overnight. A `v*` tag is what the
release workflow produces; pin a commit SHA for anything it has not tagged.
**`ctx trust --verify-lock` replaces the `ctx trust --yes` this file used to
recommend**: `--yes` on a throwaway runner accepts whatever the branch under test
declares, and `verify.cmd.run` is executed with a shell, so a pull request adding
one line to `ctx.yaml` would run it with your job's token. `--verify-lock` accepts
nothing — it can only pass on commands already reviewed into `.ctx/trust.lock`.

`ctx ci` fails on: a stale schema, a missing verify binary, a declared command
absent from `.ctx/trust.lock` (or, with no lockfile, one this machine has not
accepted), a `trust.lock` that will not parse, a `trust.lock` declaring a schema
newer than this plugin understands, an unanswered blocking question, a plan with
ownership collisions, or a briefing that had to truncate.

**The plugin's own CI** is `.github/workflows/ci.yml`: the suite on macOS, Linux
and Windows across Python 3.8–3.13, plus a job that scaffolds a throwaway ledger
and runs `init`, `doctor`, `ci` and `migrate --check` against it.

```
## ledger
  ok   layout complete
  ok   schema current
## command trust
  ok   every verify command is in trust.lock
## spec
  FAIL billing has no open blocking questions — 1 unanswered

1 check(s) failed: billing has no open blocking questions
```

Add `ctx verify --plan <slug>` to run every unit's mechanical checks; judged ones
are reported as awaiting sign-off rather than pretending an unattended run can
decide them.

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
line-wise so your comments survive. **Measurement:** `doctor` predicts what a
briefing would cost, the hooks record what sessions actually paid, `budget` shows
both.

```
## briefing budget (predicted)
  ok   L0 94/220 chars (~26 tok)
## briefing actually injected (measured)
  12 session(s) recorded · median 93 chars (~26 tok)
## declared unit budgets — plan billing
  ok   wave 1: 60,000 of 250,000 tokens
```

## What it costs

Two separate costs, and they're often confused:

| | Cost | When |
|---|---|---|
| **Plugin always-on** | measure it — see below | every session, every project, at user scope |
| **Hook briefing** | capped at 61 tok (L0) · 250 (L1) · 722 (L2) | every session in a ledger project |
| **Hooks themselves, and per turn** | 0 | harness-side; `UserPromptSubmit` is silent unless drift is detected |

The caps above are the configured `briefing_chars` at ~3.6 chars per token, so
they move if you retune them. The plugin's own footprint this file does not quote:
a figure written down in two places eventually disagrees with itself, which is a
poor look for a tool whose pitch is honest context accounting. Measure both:

```bash
ctx budget                  # this project's briefing: predicted and measured
claude plugin details ctx   # the plugin's own always-on footprint
```

`ctx budget` reports the briefing per level *and* the median actually injected
across recorded sessions, so the cap is observable rather than aspirational. Four
design decisions keep it there:

1. **`UserPromptSubmit` is silent by default.** Re-injecting criteria every turn
   would cost ~12k tokens a session to repeat what the model already has. Instead
   `PreToolUse` sets a one-shot flag when an edit strays out of scope.
2. **The digest is a tail, not a summary.** Journal lines are structured; the
   digest is the last N plus a count — O(1) in project age, zero inference.
3. **Briefings are deterministic.** Identical state produces byte-identical text,
   so the prompt cache hits across sessions.
4. **Scripts, not agents.** Status boards, digests, collision checks and budget
   measurement are all Python, and cost nothing.

To cut it further: install with `--scope project`, or remove slash commands you
don't use — one whose whole body is a shell call belongs in the CLI.

## Troubleshooting

**The gate keeps blocking and I can't finish.**
It is bounded already — three attempts, then it stops and escalates. A check that
can't run at all warns and never blocks; `ctx doctor --verify` says which command
is failing. `CTX_GATE=off` still disables it, but it is not a free action: every
use is journalled and a locked `gate.allow_override: false` refuses it. To close
work the gate won't sign, `--force` on the transition records what it stepped over.

**`ctx: command not found`.**
Use the launcher `~/tools/context-ledger/bin/ctx`, add it to your PATH, `pip
install` the package, or use the slash commands, which resolve the path themselves.

**Nothing happens in my project.**
The plugin is silent without `.ctx/`: run `/ctx:init`, and confirm with `claude
plugin list` that it's enabled.

**`/plugin isn't available in this environment`.**
It is a built-in of the interactive terminal only — the VSCode extension, headless
runs and CI don't have it. Use the `claude plugin ...` CLI, which works everywhere.

**`claude plugin update ctx` says "not found".**
Expected for a local-directory marketplace: the plugin loads live from the
directory, so there is nothing to update. `git pull` the repo instead.

**`ctx merge` refuses: "integration tree has uncommitted changes".**
Commit or stash first. Changes under `.ctx/` are excluded automatically, since the
ledger writes there itself.

**`ctx merge` reports a conflict.**
An ownership contract was violated — a unit wrote outside its `owns`. It stops
rather than resolving: inspect the branch, fix the unit's scope, re-run.

**A briefing is truncated.**
Shorten the objective and criteria on disk; raising `briefing_chars` recreates the
problem this tool exists to solve. If it's `auto_load` being cut, that is by
design — standing context yields to active work.

**`plan-check` reports collisions I don't agree with.**
It never auto-repairs; each message names the exact `depends_on` line that fixes
it. If two units genuinely must write the same path, they're one unit.

**Hooks seem slow.** `ctx telemetry` shows per-hook median and worst-case
durations; `SessionStart` and `UserPromptSubmit` sit in front of every turn. Check
`.ctx/runtime/hook-errors.log`.

**A hook is broken.** Every hook except the done-gate **fails open** — errors go
to `.ctx/runtime/hook-errors.log` and the hook exits 0, so a bug here can't brick
your session, including a bug in the gate itself.

## How it works

Everything durable is on disk. Hooks are the only traffic across the boundary, and
they run on harness events rather than on the model remembering to call them.

| Event | Responsibility | On error |
|---|---|---|
| `SessionStart` | Inject the budgeted, deterministic briefing | open |
| `UserPromptSubmit` | **Silent** unless a drift nudge is queued | open |
| `PreToolUse` | Queue a nudge on out-of-scope edits (L2), including shell writes | open |
| `PostToolUse` | Append to the journal; clear stale sign-offs | open |
| `PreCompact` / `SessionEnd` | Flush state and autosave; finalise journal and digest | open |
| `Stop` | **The done-gate** | **closed** |

Note what isn't here: nothing depends on the model *choosing* to record state.
Persistence is a property of the harness, which is why it survives compaction,
crashes and `Ctrl-C`.

### Design principles

- **Waves are computed, never authored** — `depends_on` is the single source of
  truth for ordering.
- **`plan.json` is derived**, and prior revisions are archived rather than
  overwritten, so an in-flight wave can't be pulled out from under itself.
- **No check that can never fail.** A default that always passes makes an
  unguarded project look guarded — worse than no default.
- **Report, don't repair.** Collisions and merge conflicts name their fix and stop.
- **Fail open everywhere but the gate**, and make every bypass leave a record.

## Security

Report a vulnerability through **GitHub private vulnerability reporting**:
<https://github.com/ansh-n-chovatiya/context-ledger/security/advisories/new>

That form, and not email: this project publishes no security mailbox, and an
address in a commit trailer or a package manifest is authorship metadata, not a
monitored intake. Not a public issue either — the right place for a bug, the wrong
place for a working exploit. Windows and scope: [SECURITY.md](SECURITY.md).

> **Private vulnerability reporting is off by default on GitHub, and it is off on
> this repository.** The link above will not accept a report until a maintainer
> switches it on in *Settings → Advanced Security*. That is a repository setting,
> not something a commit can fix, and until it is on there is no working private
> channel — said here rather than left for a reporter to discover.

**Supported versions:** the latest released minor only. Fixes ship as a new patch
from `main`; there are no LTS branches and no backports.

**Every `uses:` in `.github/workflows/` is pinned to a full commit SHA**, because
a tag is a mutable pointer and an action resolved at run time is whatever it was
repointed at overnight. The trailing `# v4.4.0` comment is the human-readable half
of the same pin — it is what makes the SHA reviewable, and being a comment, the
SHA is what actually runs. Changing the version means changing both.

The threat model — `ctx` runs commands that arrive as *data*, from a committed
`ctx.yaml`, a plan file, a fetched spec — is why [command trust](#command-trust)
and `.ctx/trust.lock` exist.

## Uninstalling

```bash
claude plugin uninstall ctx                     # or /plugin uninstall ctx
claude plugin marketplace remove context-ledger
```

Your `.ctx/` directory is untouched — plain markdown and JSON, readable and useful
without the plugin. `pip uninstall context-ledger` removes the packaged CLI. To
delete the ledger: `rm -rf .ctx`, then `git worktree prune` if you used the
session tier.

## Development

```bash
python3 -m unittest discover -s tests      # stdlib only, no dependencies
claude plugin validate . --strict
```

```
ctx/            the package — every decision that doesn't need a model
hooks/          three-line shims over ctx.hooks, so the contract has one seam
commands/       slash commands: a few lines each, logic lives in Python
agents/         unit-runner, verifier, reviewer, re-reviewer
skills/ledger/  when to escalate, and what belongs on disk
tests/          stdlib unittest
bin/ctx         launcher for CLI and CI use
```

### Releasing

One version, in two files, and a tag that agrees with both. Bump `__version__` in
`ctx/__init__.py` **and** `"version"` in `.claude-plugin/plugin.json` to the same
string, commit, then:

```bash
git tag v0.8.1 && git push origin v0.8.1
```

`.github/workflows/release.yml` fires on `v*` and nothing else. Its first step
compares the tag, `ctx.__version__` and `plugin.json` and exits non-zero if the
three do not describe one release — before anything is built, so a mistyped tag
costs ten seconds rather than a wheel whose filename lies about the code inside
it. `pyproject.toml` does not restate the version; hatchling reads it from
`ctx/__init__.py`.

It **does not publish to any package index**: no `twine`, no Trusted Publishing,
no `id-token: write`. It builds an sdist and a wheel, installs the wheel into a
clean venv and runs `ctx --version` from it, emits a CycloneDX SBOM and asserts its
component list is exactly `context-ledger`, writes `SHA256SUMS`, and attaches all
four artefacts to the GitHub Release (`sha256sum -c SHA256SUMS` verifies a copy).

`[tool.hatch.build.targets.sdist].include` is an **allowlist**, not an ignore
list. It is what keeps `.ctx/runtime/` and `tests/fixtures/` out of the sdist —
and what will silently drop a new top-level directory that nobody added to it.

The tests worth keeping green are the risk guards: briefing caps hold and
briefings are byte-stable; journal cost doesn't grow with history; hooks stay
silent in untracked projects and fail open everywhere but the gate; a missing tool
never blocks but a real failure always does; collisions are caught within a wave
and not across; `migrate --check` writes nothing; no profile ships an
always-passing default.

## License

MIT © 2026 Ansh Chovatiya. See [LICENSE](LICENSE).
