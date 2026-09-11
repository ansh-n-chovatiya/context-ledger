# Walkthroughs

Worked examples, end to end. [Back to the README](../README.md) ·
[Reference](reference.md) · [Operations](operations.md)

[A small change (L1)](#a-small-change-l1) ·
[A large change (L2)](#a-large-change-l2) ·
[Fixing a bug under phase gates](#fixing-a-bug-under-phase-gates) ·
[Review without commits](#review-without-commits) ·
[Memory that survives sessions](#memory-that-survives-sessions)

## When to escalate

Stay at **L0** unless the user stated acceptance criteria you'd otherwise have to
remember, the work spans more than one session (or you expect compaction
mid-task), or verification is worth automating because you'll run it repeatedly.

Go to **L2** only when the pieces have **disjoint write scopes**: sequential steps
in one file are L1 with a numbered criteria list, not a plan. De-escalate with
`/ctx:drop` the moment ceremony stops paying for itself — nothing on disk is
deleted, you just stop being gated.

## A small change (L1)

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

## A large change (L2)

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

Other frontmatter a unit may carry — `kind: bug` and its `reproduction:`,
`phases:`, `model:` — is in the [unit frontmatter
reference](reference.md#unit-frontmatter).

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

A wave is refused if it is too expensive (`plan.wave_budget_tokens`) **or too
wide** (`plan.max_wave_units`, default 8): twelve 5k units clear the token budget
with room to spare and still ask one session to hold twelve concurrent Task
calls, twelve reports and twelve review packages at once. Each dispatch line
names the model the unit will run on and the [complexity
score](reference.md#complexity-and-model-choice) that chose it.

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
gets a temporary checkout and branch under `.ctx/runtime/worktrees/<plan>/`.

```
cd .ctx/runtime/worktrees/auth-rotation/03-rotate
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
or if the unit has no dispatch seal: see [Ungated is not
done](reference.md#ungated-is-not-done). Until it lands the unit stays `running`.

If the runner reported what the unit cost, record it — partial data, labelled as
such wherever it is shown:

```
ctx telemetry --spend 38000 --unit 03-rotate
```

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

## Fixing a bug under phase gates

A unit that declares `kind: bug` cannot fix before it reproduces. The
[four phases](reference.md#phase-gates-and-kind-bug) are enforced by the ledger,
not by convention, and `/ctx:phase` is the only door.

```yaml
---
unit: 02-expiry-off-by-one
plan: token-bugs
kind: bug
reproduction: pytest tests/test_auth.py::test_expiring_token
owns: [src/auth.py]
verify:
  - kind: cmd
    run: pytest tests/test_auth.py
---
```

Run it with no phase named first — that prints what the gate currently wants
rather than making you guess:

```bash
ctx phase 02-expiry-off-by-one
```

```
phases for 02-expiry-off-by-one: reproduce -> locate -> fix -> guard
  reproduce: 0 entries recorded — open
  locate: 0 entries recorded — locked — 'locate' is locked: reproduce has not been recorded yet
  fix: 0 entries recorded — locked — 'fix' is locked until reproduce (…) and locate (…) are recorded
  guard: 0 entries recorded — locked — 'guard' is locked: fix has not been recorded yet
    judged on: the fix addresses the cause `locate` pointed at, and the …
```

Then, in order:

```bash
# 1. a run that actually FAILED. A zero exit reproduces nothing.
ctx phase 02-expiry-off-by-one reproduce \
    --command "pytest tests/test_auth.py::test_expiring_token" --exit-code 1 \
    --evidence "AssertionError: token accepted 1s after expiry"

# 2. where, as file:line. A bare assertion does not satisfy this.
ctx phase 02-expiry-off-by-one locate \
    --evidence "src/auth.py:118 — expiry compared with <= instead of <"

# 3. after the change: the SAME reproduction command, exiting zero.
ctx phase 02-expiry-off-by-one fix \
    --command "pytest tests/test_auth.py::test_expiring_token" --exit-code 0

# 4. guard is judged, not mechanical — dispatch the verifier at the text the
#    listing printed under "judged on", then record its verdict.
ctx phase 02-expiry-off-by-one guard --exit-code 0 --evidence "<verdict>"
```

Recording out of order is refused in the gate's own words, which always name the
specific prerequisite that is missing. Read the refusal; do not retype the
command with different values.

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
`/ctx:decide`, not only in a findings file. With
`models.escalate_on_failed_round` on, each round past the first also dispatches
one tier dearer.

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
cause. (`kind: bug` gates the order; it does not help you find the cause.) And
`test_first` has **no CLI surface**: its evidence is written by
`ctx.snapshot.record_test_run` from Python, so a unit declaring that kind today
needs a caller of its own. Gaps, named here rather than papered over.

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
