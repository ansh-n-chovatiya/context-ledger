# Context Ledger — engineering audit

**Scope:** full source read (4,845 lines), 193-test suite executed, six defects reproduced under controlled probes.
**Against:** `a93a714` · version 0.1.1
**Date:** 2026-09-03

| | Found | Fixed |
|---|---|---|
| Blocking | 9 | 9 |
| Enterprise readiness | 11 | 11 |
| Flow | 5 | 4 |
| Record | 3 | 3 |

**Status:** 27 of 28 fixed. Suite is 298 tests (was 193); the 105 added pin the
behaviour that was wrong rather than the shape of the fix.

**F22 (command consolidation) is deliberately not done** — it is the one finding
that is a product decision rather than a defect, and collapsing verbs people have
muscle memory for is a breaking change worth asking about rather than imposing.
See the finding for the options.

One correction found while fixing F02: worktree-tier units were **already**
isolated, because each worktree is a checkout carrying its own `.ctx/` and
therefore its own gitignored `runtime/`. The pointer only ever collided between
two sessions sharing one tree, which is narrower than this audit first claimed.

---

## 1. Verdict

Context Ledger solves a genuinely hard problem well. The three-level engagement model,
the character-capped briefing, the distinction between *work failure* and *infrastructure
failure*, the derived wave graph — these are load-bearing ideas, implemented with care and
documented honestly. Stdlib-only was the right call.

The gap is not architecture. It is that **the system was designed against a well-behaved
project and a single-threaded session**, and both assumptions break in the environments
you are aiming at.

Three defects are severe enough to make the tool actively misbehave rather than merely
fall short:

- **Every subagent that stops runs the project's full test suite** and can be blocked
  against acceptance criteria it never touched. Under a parallel wave that is N concurrent
  test runs against one shared tree.
- **A Python repo with a `docs/` folder is classified as a docs project** and gets no
  runnable gate at all.
- **On a machine where only `python3` exists** — every current macOS install — `ctx init`
  proposes `python -m pytest`, finds it absent, and writes an empty `verify:` list. The
  flagship feature silently produces an unguarded ledger.

All three were reproduced, not inferred. Wave 1 below is roughly two days of work and
takes the tool from "works on the author's machine and shape of project" to something you
can point at a monorepo.

---

## 2. Blocking

### F01 — Every subagent stop runs the whole done-gate  ·  **fixed**

`ctx/hooks.py:255` · `hooks/hooks.json` → `SubagentStop` (no matcher)

`SubagentStop` is wired to the same handler as `Stop`, with no matcher. So an `Explore`
search, the `verifier` agent, any unrelated Task — each one ends by running the project's
entire `verify` suite (up to 240s per `cmd`) and can be *blocked up to `max_attempts`*
against a task it never touched. Dispatch a wave of six unit-runners and you get six
concurrent `npm test` runs against one shared working tree.

```
reproduced → hooks.main("SubagentStop") on an L1 ledger
{"decision": "block", "reason": "The done-gate blocked completion of
 `demo-a-thing` (attempt 1 of 3)…"}
```

**Fix:** gate on `Stop` only. If subagent verification is wanted, key it to an explicit
unit handoff — a `ctx unit` claim the runner makes — not to every agent that finishes.

### F02 — Parallel waves share one `unit` pointer  ·  **fixed**

`ctx/state.py` · `ctx/work.py:63` · `ctx/dispatch.py:98`

`state.json` holds a single `unit` field, but a wave dispatches N subagent-tier units at
once — and the dispatch brief only instructs `inline` and `session` tiers to run
`ctx unit`. The gate therefore judges whichever unit the pointer happens to hold, which
for the default tier is stale or null. Combined with F01, subagents are blocked against
the wrong contract.

**Fix:** make the active unit per-agent, not per-machine — key it by session ID, or pass
it through the unit-runner's environment. A single global pointer cannot describe a
parallel wave.

### F03 — `init` proposes `python`, which modern installs do not have  ·  **fixed**

`ctx/cli.py:146`

`_verify_candidates` emits `python -m pytest -q`; `_runnable` then checks
`shutil.which("python")`. Homebrew and python.org installs ship `python3` only. The
candidate is silently rejected and the ledger is written with `verify: []` — no gate, no
warning beyond one grey line.

```
reproduced → git repo containing only pyproject.toml
initialised .ctx  profile=code  level=L0
  skipped python -m pytest -q — binary not on PATH
  no verify commands configured
verify: []
```

**Fix:** use `sys.executable` for the proposed command, falling back to `python3`. Then
probe importability (`-m pytest --version`) rather than only PATH presence.

### F04 — A `docs/` folder outranks every code marker  ·  **fixed**

`ctx/cli.py:127-138`

Profile detection tests `infra → data → docs → code` in order, and the `docs` markers
include a bare `docs` directory. Any Python, Node or Go repo that documents itself — most
of them — is classified as a docs project, which has no command candidates at all and
falls back to a `rubric` check. The same trap fires on `notebooks/` → `data`. This is the
single largest obstacle to "adaptable to any project".

```
reproduced → repo with pyproject.toml + docs/index.md
initialised .ctx  profile=docs  level=L0
  no runnable command detected; falling back to rubric
```

**Fix:** score, don't first-match. A build manifest at the root is far stronger evidence
than a directory name; `docs` should only win when nothing else matched. Support mixed
profiles for repos that are genuinely both.

### F05 — The YAML writer escapes quotes the reader never unescapes  ·  **fixed**

`ctx/miniyaml.py:177` (`_emit`) vs `:106` (`_scalar`)

`_emit` wraps a value needing quotes and writes `\"`; `_scalar` strips the wrapper but
leaves the backslashes. Unit and task frontmatter is rewritten on every status change,
sign-off and `apply_waves` call, so the corruption compounds with each pass. Any `run:` or
`about:` containing a quote and needing quoting is destroyed.

```
reproduced → round-trip through dumps/loads
in  '-flag "x"'   dumped  k: "-flag \"x\""
out '-flag \"x\"'                *** LOSSY ***
```

**Fix:** unescape `\"` and `\\` in `_scalar` when the value was quoted, and add a property
test asserting `loads(dumps(x)) == x` over a corpus with quotes, leading dashes and
commas. Inline lists also split on commas inside quotes.

### F06 — Removing one worktree deletes same-named branches in every plan  ·  **fixed**

`ctx/worktree.py:151-154`

`remove()` loops over *every* entry in `.ctx/plans/` and runs
`git branch -D ctx/<plan>/<unit>`. Two plans that both contain a `01-api` unit — a near
certainty with numbered kebab names — means discarding one destroys the other's branch. If
that branch was the only ref to committed work, the work is gone.

**Fix:** take the plan slug as a parameter and delete exactly one branch. The loop also
iterates files, not just directories.

### F07 — The gate timeout is per check; the hook timeout is per gate  ·  **fixed**

`ctx/verify.py:87` · `hooks/hooks.json` → `Stop` timeout 300

`gate.timeout_seconds: 240` applies to each `cmd` individually, but the harness kills the
whole `Stop` hook at 300s. Three commands — typecheck, test, lint, a normal configuration
— can run for 720s. The hook is killed mid-run, produces no decision, and the gate
silently does not apply. The config comment says "must stay under the Stop hook timeout",
which is only true for a single check.

**Fix:** budget the gate as a whole — track elapsed time across checks and stop with an
`ERROR` verdict when the remaining budget is exhausted, so an over-long suite warns
instead of vanishing.

### F08 — Scope isolation does not see Bash edits  ·  **fixed**

`hooks/hooks.json` → PreToolUse/PostToolUse matchers · `ctx/hooks.py:263`

Both hooks match `Edit|Write|MultiEdit|NotebookEdit`. A `sed -i`, a heredoc, a
`git checkout`, an `mv`, or any MCP file-writing tool passes straight through: no
`owns`/`forbid` nudge and no journal entry. Since `owns` isolation is the stated reason
parallel waves are safe rather than hopeful, an enforcement path with a one-command bypass
is not an enforcement path. It also means `recent:` and the digest under-report real work.

**Fix:** add `Bash` to the matchers and parse the command for write-shaped verbs against
declared scope. Treat the `diff` verify kind — which reads git, not tool calls — as the
authoritative check, and say plainly in the docs that the PreToolUse nudge is advisory.

### F09 — One transient hook error makes `doctor` fail forever  ·  **fixed**

`ctx/cli.py` → `cmd_doctor` · `ctx/hooks.py:81`

`hook-errors.log` is append-only with no rotation, and `doctor` counts its mere existence
as a problem. A single failure — a full disk, a killed process — leaves the command
exiting 1 permanently, with no CLI to clear it. In a pipeline that is a red build nobody
can turn green without knowing to delete a file by hand.

```
reproduced → write 2 lines to hook-errors.log, then:
$ ctx doctor
## hook errors (2 lines in .ctx/runtime/hook-errors.log)
1 problem(s)                            exit 1
```

**Fix:** rotate the log, report only errors newer than the last clean run, and add
`ctx doctor --clear`. Report it as a warning unless errors are recent.

---

## 3. Enterprise readiness

### F10 — No verify check can name its own working directory  ·  **fixed**

`ctx/verify.py:196` · `ctx/hooks.py:195`

Every `cmd` runs at `layout.root.parent`. A monorepo with the ledger at the root and
packages under `apps/web`, `services/api` cannot express "run `npm test` here". This is
the primary reason the tool cannot be pointed at a large repository as-is.

**Fix:** add optional `cwd:` and `env:` to a check. Both are a few lines and they unlock
the whole monorepo class.

### F11 — Toolchain detection covers five ecosystems  ·  **fixed**

`ctx/cli.py:141-161`

Python, npm, Go, Cargo and Terraform. Notably absent: Maven and Gradle (`pom.xml` is
detected as `code` but yields no command at all), .NET, Ruby, PHP, Elixir, Swift, plain
`make`, Bazel, and the workspace tools that define modern monorepos — pnpm workspaces, Nx,
Turborepo. That set is most of what an enterprise actually runs.

**Fix:** move candidates into a declarative table of *(marker file → probe → command)* so
adding an ecosystem is data, not code — and so users can extend it in `ctx.yaml`.

### F12 — Windows has no path through  ·  **fixed**

`bin/ctx` · every file in `commands/`

`bin/ctx` is a bash script, and all 19 command files invoke it. The README's "Windows via
WSL or Git Bash" is a workaround, not support. For an enterprise rollout where the fleet
is mixed, that excludes a large share of seats.

**Fix:** ship `bin/ctx.cmd` alongside, or have the command files call `python3 -m ctx`
with `PYTHONPATH` set inline — removing the shell dependency entirely.

### F13 — `state.json` has atomic writes but no atomic updates  ·  **fixed**

`ctx/state.py:58-82`

`os.replace` makes each write atomic, but `update`, `bump_attempts` and `clear_attempts`
are read-modify-write with no lock. Concurrent `ctx` invocations — the normal case during a
wave — lose attempt counts and can clobber each other's `level` and `unit`. Attempt keys
are bare unit names, so they also collide across plans.

**Fix:** an `O_EXCL` lockfile around the read-modify-write, and namespace attempt keys as
`plan/unit`.

### F14 — A committed `ctx.yaml` is executable shell  ·  **fixed**

`ctx/verify.py:196` (`shell=True`) · `.ctx/ctx.yaml` is tracked

`verify.cmd.run` is committed to the repository and executed by a hook with `shell=True` —
hooks do not go through the tool permission prompt. Cloning an untrusted repo and
escalating to L1 runs whatever that file says. The real mitigation today is accidental:
`state.json` lives under gitignored `runtime/`, so a fresh clone starts at L0. That is a
defence nobody documented and nobody can rely on.

**Fix:** document the trust boundary explicitly. Better: fingerprint the `verify` block and
require a one-time confirmation when it changes — the same shape as a lockfile check.

### F15 — Gate output reaches the transcript unredacted  ·  **fixed**

`ctx/verify.py:218` · `ctx/hooks.py:227`

`redact.scrub` guards the journal and context bundles — the write path — but not the block
reason, which inlines up to 60 lines of raw command output straight into the model's
context. A test that prints a token, a connection string in a stack trace, a failing
integration test dumping headers: all land in the transcript and any downstream log.

**Fix:** run `scrub` over the truncated excerpt in `_check_cmd`. One line, and it closes
the last unguarded path.

### F16 — The journal has no retention policy  ·  **fixed**

`ctx/journal.py:44`

One committed markdown file per active day, forever. A team repo produces hundreds a year,
all tracked, all in every clone and every diff. There is no `prune`, no archive, no
compaction beyond the 12-line digest that reads them.

**Fix:** `ctx journal --prune --before`, plus monthly rollup into a single archive file.
The digest is already a tail, so it is unaffected.

### F17 — `--status done` is an assertion, not a verification  ·  **fixed**

`ctx/cli.py` → `cmd_unit`

Only the worktree `merge` path enforces the gate before marking a unit complete. For
`subagent` — the default tier, and the one the dispatch brief pushes hardest — "done" is
whatever the orchestrator types after reading a report the unit wrote about itself.

**Fix:** run the unit's checks inside `ctx unit --status done` and refuse on failure, with
`--force` as the deliberate override. Right now the strongest guarantee in the system does
not cover its most common path.

### F18 — The plugin's own tests do not run anywhere  ·  **fixed**

no `.github/` in the repository

The README documents a consumer-facing workflow inline, but the repo has no CI of its own.
193 tests exist and nothing runs them on push — and there is no reusable action or
pre-commit hook for the teams meant to adopt `ctx ci`.

**Fix:** a matrix workflow across Python 3.8–3.13 and macOS/Linux/Windows, running the
suite plus `ctx ci` against a fixture project. Publish a composite action so consumers get
one line, not a copy-paste.

### F19 — Telemetry has no configured off switch  ·  **fixed**

`ctx/telemetry.py` · `ctx/config.py` DEFAULTS

Records are local and gitignored, which is the right design — but there is no
`telemetry.enabled` key and no mention in the configuration reference. Procurement review
asks this question every time, and "read the source, it never leaves the machine" is not
the answer that clears it.

**Fix:** a config key, documented, defaulting to on.

### F20 — Checks are copied into tasks at creation and never re-sync  ·  **fixed**

`ctx/cli.py` → `cmd_task`, `cmd_plan`

`config.verify` is snapshotted into each task and unit's frontmatter when the file is
written. Fixing a broken command in `ctx.yaml` leaves every existing task still carrying
the old one, with nothing reporting the divergence. The snapshot is defensible; the
silence is not.

**Fix:** have `doctor` report tasks whose `verify` block differs from the project default,
and add `ctx task --resync`.

### F21 — Two file handles leak on every symbol and exists check  ·  **fixed**

`ctx/verify.py:150, 176`

`open(...).read()` with no context manager. Harmless under CPython refcounting, but it
emits `ResourceWarning` on every test run — noise in exactly the output a team would be
watching.

**Fix:** use `Path.read_text`. Then turn warnings into errors in CI so the next one is
caught.

---

## 4. Flow

### F22 — Too many slash commands for a tool that argues against ceremony  ·  **open, needs a decision**

`commands/` · 20 files (18 at audit time; `next` and `escalate` added since)

`save`, `load`, `list` and `promote` are four commands over one noun. `status`, `resume`
and `doctor` overlap heavily in what they print. The skill file warns that "a system that
demands a spec for a two-line fix gets abandoned" — a nineteen-item command palette is the
same failure wearing a different coat.

**Fix:** collapse the bundle verbs into one `/ctx:context` with subcommands. Target ten
surfaced commands; keep the rest reachable through the CLI.

**Status:** open. This is the one finding that is a product decision rather than a defect —
`/ctx:save` and `/ctx:load` are the memory verbs people learn first, and removing them is a
breaking change for existing users in exchange for a tidier palette. `/ctx:next` (F23) took
most of the pressure off by removing the need to *know* the palette at all. Raised rather
than decided.

### F23 — There is no "what now"  ·  **fixed**

`commands/`

To use the tool you must first know which level you are at, then which command that level
implies. The state machine already knows: blocking questions open means `/ctx:ask`; a
checked plan with a pending wave means `/ctx:start`; a failed gate means `/ctx:verify`.
Nothing exposes that inference to the user.

**Fix:** a single `/ctx:next` that reads state and names the one action, with everything
else reachable but unprompted. This is the highest-leverage addition on the list — it turns
three levels of machinery into one entry point.

### F24 — Escalation is a cliff, not a ramp  ·  **fixed**

`ctx/cli.py` → `cmd_task`, `cmd_spec`

`/ctx:drop` exists to come down, but going L1 → L2 means abandoning the task file and
starting a spec from scratch. The skill tells the model to escalate when work turns out
larger than expected — precisely the moment when discarding the objective and criteria
already written is most expensive.

**Fix:** `ctx escalate` that seeds the spec's Intent and Acceptance criteria from the
active task and links back to it.

### F25 — Name-versus-objective splitting is guesswork  ·  **fixed**

`ctx/cli.py:67-94`

`_split_name` infers the boundary from punctuation and from whether the first word contains
a hyphen. `/ctx:task Fix the login-page bug` becomes a task slugged
`fix-the-login-page-bug` with no objective. The heuristic is well-commented and still a
heuristic, sitting on the most-used command.

**Fix:** have the command file pass the name and objective as distinct flags, and let the
prompt do the splitting — a model is better at this than a regex, and it can ask.

### F26 — The orchestrator is asked to obey a rule nothing checks  ·  **fixed**

`ctx/dispatch.py:79` · `commands/start.md`

"Do not read source files" is repeated in the brief and the command, and it is the
mechanism that keeps orchestrator context flat across a twenty-unit plan. It is also
entirely honour-system. Since the wave is already fully described on disk, the check is
available.

**Fix:** have `ctx status` report orchestrator reads observed in the journal during an
active wave. Measuring it costs nothing and makes the discipline visible rather than
aspirational — the same move the briefing budget already makes.

---

## 5. Record

### F27 — The two cost figures disagree, and the model reads the wrong one  ·  **fixed**

`README.md:899-904` vs `skills/ledger/SKILL.md:50-53`

README: ~557 always-on tokens, ~587 for an L0 session. SKILL.md: ~425 and ~455. SKILL.md is
loaded into context, so the number the model reasons with is the one that was never
updated. For a tool whose entire pitch is measured, honest context accounting, this is the
worst possible place to be inconsistent.

**Fix:** measure once, write it in one place, and have the other reference it. Better: have
`ctx budget` print the live figure and stop hardcoding it in prose.

### F28 — Stale test count, and one documented invocation that fails  ·  **fixed**

`README.md:1042`

"175 tests" — the suite runs 193. And `tests/` has no `__init__.py`, so the natural
`python3 -m unittest discover -s tests -t .` raises
`ImportError: Start directory is not importable`. Only the exact documented form works, and
only because `support.py` patches `sys.path`.

**Fix:** add `tests/__init__.py`, and let CI assert the count rather than the README.

---

## 6. Remediation, in waves

Ordered by dependency, not severity. Wave 1 is what makes the gate trustworthy; nothing
after it is worth doing until it holds.

### Wave 1 — done · F01 F03 F04 F05 F06 F07 F09 F15 F17 F21

- **Made the gate honest.** `SubagentStop` unregistered and its shim deleted; the
  timeout is now one budget for the whole run, with a check that starts after it is spent
  reported as a configuration error; `ctx unit --status done` runs the unit's checks and
  refuses on failure, with `--force` as the explicit override; failure output is scrubbed
  before it reaches the model, while the gitignored log stays raw.
- **Made detection work.** Profile markers are scored and weighted rather than
  first-matched, so a manifest beats an incidental `docs/`; the proposed Python command
  uses an interpreter that exists; `_availability` probes module importability and now
  reports the real reason instead of always claiming "binary not on PATH".
- **Stopped the data loss.** `_scalar` unescapes what `_emit` escaped and inline lists no
  longer split inside quotes; worktree removal resolves one branch from git and deletes
  only that.
- **Unstuck `doctor`.** Error entries are timestamped, the log rotates at 64 KB, only
  failures inside 24h block, and `ctx doctor --clear` drops a stale one.

### Wave 2 — done · F02 F08 F10 F11 F13

- **Reached a real repo.** `cmd`, `exists` and `symbol` take a `cwd`, and `cmd` takes an
  `env`; a missing `cwd` warns rather than blocks. Candidates come from a declarative
  marker table covering Maven, Gradle, .NET, Ruby, PHP, Elixir, Swift, Make and the pnpm
  / yarn / bun workspaces, extensible from `ctx.yaml` via `verify_candidates`, and no
  longer gated on the profile label.
- **Survived concurrency.** `CTX_UNIT`/`CTX_PLAN` claim a unit for one process and beat
  the shared pointer; state updates hold a lockfile that reclaims a stale one and fails
  open; attempt keys are namespaced `plan/unit`.
- **Closed the bypass.** `Bash` is in both hook matchers, with shell write detection
  feeding the same `owns`/`forbid` nudge and the journal. The heuristic is documented as
  advisory, with `diff` named as authoritative.

### Wave 3 — done · F12 F14 F16 F18 F19 F20

- **Shipped the operational surface.** `.github/workflows/ci.yml` runs the suite on macOS,
  Linux and Windows across Python 3.8–3.13, plus a job that scaffolds a throwaway ledger
  and runs `init`/`doctor`/`ci`/`migrate --check` against it. `ctx prune` folds old journal
  days into monthly archives, driven by `journal.keep_days`. `telemetry.enabled` is a
  documented switch. `ctx doctor` reports work files whose `verify` block has drifted from
  ctx.yaml.
- **Made the trust boundary explicit.** Commands are accepted per command and
  machine-locally; `ctx init` accepts what it configured, anything else is reported and not
  run until `ctx trust`. Landing it broke 23 existing tests, which is the proof the control
  is real — the fixture now accepts hand-written checks the way a developer authoring them
  locally would.
- **Covered Windows.** `bin/ctx.py` is the platform-neutral entry point; `bin/ctx` and
  `bin/ctx.cmd` wrap it, and CI asserts all three report the same version.

Two things found while doing it, both fixed here: `journal.append(when=…)` set only the
time inside the line, not which day file it landed in, so the parameter meant something
other than it said. And the README's "Python 3.8+" claim was untested — the suite now
parses every module under the 3.8 grammar and CI pins a 3.8 job.

A composite action for consumers was **not** shipped: the workflow in the README is a
seven-line copy-paste, and an action to maintain is not obviously better than that. Say if
you want one.

### Wave 4 — done except F22 · F23 F24 F25 F26 F27 F28

- **Streamlined the flow.** `/ctx:next` reads state and names the one action — blocked spec
  to `/ctx:ask`, ready spec to `/ctx:plan`, dispatchable wave to `/ctx:start`, unaccepted
  command to `ctx trust`. `ctx escalate` carries a task's objective and criteria into a spec
  and keeps the task file as the record of why the work grew. `ctx task` now prints the
  name/objective split it made and how to correct it, so a wrong guess is visible.
- **Measured what the orchestrator rule can measure.** `ctx status` reports source files
  edited during an active wave that no unit owns. Reads — the discipline that actually
  matters — stay unobserved: catching them needs a `PostToolUse` hook on every `Read`, a
  process spawn per file read, which is too much to charge everyone for a diagnostic. Said
  plainly rather than half-built.
- **Reconciled the record.** Neither README nor SKILL.md hardcodes the plugin's always-on
  cost now; both point at `ctx budget` and `claude plugin details ctx`, and a test asserts
  the stale figures never come back. `tests/__init__.py` makes the discovery form people
  reach for first actually work. The README no longer claims a test count.

Found while doing it: SKILL.md referenced `/ctx:budget`, which is CLI-only — a dead end the
model would walk into. Fixed, plus a test that every `/ctx:` reference in the docs resolves
to a shipped command file.

---

## 7. What should not change

An audit that only lists defects invites the wrong repair. These are the load-bearing
decisions, and they are correct.

- **Levels default down.** L0 always on, escalation opt-in and reversible. The one design
  choice that decides whether a tool like this gets used at all.
- **Budget in characters.** No tokenizer dependency, measurable in CI, enforced by
  truncation rather than by hope. `ctx doctor` reports it, so it is observable rather than
  aspirational.
- **Work failure ≠ infra failure.** A missing binary warns and passes; a failing criterion
  blocks. Getting this backwards would brick every session in a project whose toolchain is
  not installed.
- **Sign-offs expire on edit.** A judged verdict cannot outlive the code it judged. Small
  mechanism, and it removes the most common way a gate becomes theatre.
- **Waves derived, not authored.** `depends_on` is the single source of truth; ownership
  collisions and read/write races are reported with the exact line that fixes them, and
  never auto-repaired.
- **Stdlib only.** Nothing enters the host project's dependency tree. It is why the plugin
  can be installed globally and stay silent everywhere it was not invited.
