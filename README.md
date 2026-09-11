# Context Ledger

**Durable project state for Claude Code.** Specs, plans, decisions and memory live
on disk instead of in the context window — so sessions become disposable,
compaction stops losing your work, and "done" becomes something a gate can refuse
to sign off on.

Python 3 standard library only. Nothing is added to your project's dependency
tree, and the plugin is completely silent in any project that hasn't opted in.

[Why this exists](#why-this-exists) · [Requirements](#requirements) ·
[Installation](#installation) · [Quick start](#quick-start) ·
**[The three levels](#the-three-levels)** — the one concept to understand ·
[Command reference](#command-reference) · [Where the rest lives](#where-the-rest-lives)
· new to it? [GUIDE.md](GUIDE.md) explains the same tool without jargon.

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
| **OS** | macOS, Linux, Windows. `bin/ctx` (POSIX) and `bin/ctx.cmd` (Windows) both wrap `bin/ctx.py`; `python3 -m ctx` works anywhere. |

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

**Updating is two commands, not one:**

```bash
claude plugin marketplace update context-ledger   # fetch the new commits
claude plugin update ctx@context-ledger           # install them
```

`claude plugin update` fetches nothing: it reads the marketplace clone already on
disk, so without the first command it reports you are current while sitting on a
months-old build. It compares **declared version numbers, not commits**, so a
stale clone downgrades you. Restart Claude Code afterwards, then check what you
are running rather than trusting the output: `claude plugin list`, and `ls
~/.claude/plugins/cache/context-ledger/ctx/` for one directory per version.

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
[Releasing](docs/operations.md#releasing)). Wheels come from a clone or from a
GitHub Release.

A wheel gives you the CLI and nothing else: the slash commands, hooks, agents and
skills are plugin assets, so Claude Code still wants the plugin install above.

Install scopes (`user`, `project`, `local`) and installing from a local clone:
[Operations](docs/operations.md#install-scopes).

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
/ctx:next        # the single most useful next action, read off the ledger
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

Go to **L2** only when the pieces have **disjoint write scopes**: sequential steps
in one file are L1 with a numbered criteria list, not a plan. Worked examples of
both, end to end: [Walkthroughs](docs/walkthroughs.md).

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
| `/ctx:trust` | Review and accept the shell commands the gate will run on this machine |
| `/ctx:task «name» [objective]` | **L1** — track one change with a done-gate |
| `/ctx:verify` | **L1** — run the done-gate by hand; `--sign-off rubric\|human` |
| `/ctx:drop` | **L1** — return to L0 trace, keeping the journal |
| `/ctx:spec «name» [— intent]` | **L2** — intent → checkable criteria → blocking questions |
| `/ctx:ask [name]` | **L2** — show and ask what's still blocking a spec |
| `/ctx:plan «name»` | **L2** — decompose a ready spec into dispatchable units |
| `/ctx:start [--wave N] [--worktree] [--rebaseline UNIT] [--reseal UNIT]` | **L2** — dispatch brief for the next wave |
| `/ctx:phase «unit» [phase]` | **L2** — inspect or advance a unit's phase gate |
| `/ctx:review «unit» [--round N]` | **L2** — adversarial review of a completed unit, from a snapshot diff |
| `/ctx:findings «unit»` | **L2** — findings raised against a unit; `--add`/`--set` to record or resolve one |
| `/ctx:merge «unit»` | **L2** — land a unit's worktree branch after its gate passes |
| `/ctx:decide «title»` | Record an ADR |
| `/ctx:save «name»` | Write a portable context bundle |
| `/ctx:load «name»` | Load one (project → global → path) |
| `/ctx:context` | Saved bundles: what exists, and how to load, save or promote one |
| `/ctx:handoff [name]` | Resume packet for another session, person or model |

Arguments are free text, not shell tokens: the command files quote `$ARGUMENTS`,
so `/ctx:task add-search let users search flows` and `/ctx:decide don't cache
refresh tokens` both arrive as one argument and work without quoting. A command
run with no arguments reports what it needs instead of failing, because a non-zero
exit aborts the slash command before its prompt can ask you — which is also why
each command file ends its `!` line with `|| true`.

### CLI

Every subcommand below is runnable in CI and in scripts, which is the point:
the same checks a session is gated on are the ones a pipeline runs. Use `bin/ctx`
in the plugin directory, or the `ctx` the [package
install](#as-a-python-package) puts on `PATH`. Exit codes and `--json` are
documented in the [Reference](docs/reference.md#exit-codes).

Most subcommands have **no slash command by design**: none is a conversation, and
every slash command costs always-on context.

| Command | Does | Slash |
|---|---|---|
| `ctx init [--profile P] [--verify-now] [--force]` | Scaffold `.ctx/`, detect the profile, propose and accept verify commands | `/ctx:init` |
| `ctx status` | Level, active work, briefing budget, wave board, journal tail | `/ctx:status` |
| `ctx briefing` | Print exactly what `SessionStart` would inject | |
| `ctx resume` | Expanded prior state, on demand | `/ctx:resume` |
| `ctx digest` | Regenerate `journal/DIGEST.md` | |
| `ctx drop` | Return to L0 trace, keeping the journal | `/ctx:drop` |
| `ctx list` | Saved context bundles, project and global | `/ctx:context` |
| `ctx level «0\|1\|2»` | Set the engagement level directly | |
| `ctx task «name» [objective]` | Escalate to L1 with a single task file | `/ctx:task` |
| `ctx save «name»` | Write a portable context bundle; `--stdin`, `--file`, `--tag` | `/ctx:save` |
| `ctx load «name»` | Print a bundle: project store, then global, then path | `/ctx:load` |
| `ctx promote «name»` | Copy a bundle into the global store for other projects | |
| `ctx journal «kind» «target»` | Append one journal entry | |
| `ctx prune [--before D]` | Fold journal days older than `D` (or `journal.keep_days`) into monthly archives | |
| `ctx doctor [--verify] [--clear]` | Layout, budgets, verify availability, gate and policy state | `/ctx:doctor` |
| `ctx spec «name» [— intent]` | Escalate to L2 and scaffold a spec | `/ctx:spec` |
| `ctx question «spec» «text»...` | Add questions to a spec; `--non-blocking` | |
| `ctx ask [name]` | List the questions still open on a spec | `/ctx:ask` |
| `ctx resolve «spec» --question X --answer Y` | Record an answer, with its date | |
| `ctx spec-ready [name]` | Gate 1 as an exit code (0 = ready to plan) | |
| `ctx decide «title»` | Record an ADR | `/ctx:decide` |
| `ctx verify [--plan X] [--sign-off rubric\|human]` | Run the done-gate for the active work, or every unit's gate in a plan | `/ctx:verify` |
| `ctx plan «name» [--spec S] [--no-spec]` | Scaffold a plan; refuses while the spec is ambiguous | `/ctx:plan` |
| `ctx plan-unit «name»` | Scaffold one unit file | |
| `ctx plan-check [name]` | Compute waves and check for collisions | |
| `ctx start [--wave N] [--worktree]` | Dispatch brief for the next wave; `--rebaseline`/`--reseal` a single unit | `/ctx:start` |
| `ctx snapshot «unit» [--phase P]` | Capture a content snapshot by hand; `ctx start` takes the `before` phase itself | |
| `ctx review «unit» [--round N]` | Build the review package: diff, scope violations, stat summary | `/ctx:review` |
| `ctx findings «unit»` | List or update a unit's review findings; `--add`, `--set` | `/ctx:findings` |
| `ctx phase «unit» [phase]` | Inspect a unit's phase gate, or record one phase against it | `/ctx:phase` |
| `ctx merge «unit» [--skip-gate]` | Land a unit's worktree branch after its gate passes | `/ctx:merge` |
| `ctx worktree list\|remove` | Inspect or discard ctx worktrees | |
| `ctx next` | The single most useful next action, from state alone | `/ctx:next` |
| `ctx escalate [name]` | L1 to L2, carrying the task into a spec | `/ctx:escalate` |
| `ctx trust [--yes\|--lock\|--verify-lock]` | Review and accept the verify commands this machine will run | `/ctx:trust` |
| `ctx migrate [--check]` | Upgrade ledger files; `--check` never writes | |
| `ctx budget [--plan X]` | Predicted **and** measured context cost | |
| `ctx telemetry [--spend T --unit U]` | Hook durations and briefing sizes; record what a unit was reported to cost | |
| `ctx ci [--plan X]` | Every headless check in one exit code | |
| `ctx unit «name» [--status S]` | Focus a unit, or record its outcome | |
| `ctx handoff [name]` | Write a resume packet for a session, person or model | `/ctx:handoff` |

Global flags: `--cwd PATH` resolves the ledger from elsewhere; `--version`;
`--strict` escalates the three advisory conditions to exit 1; `--json` on the
seven commands that answer in a document.

**Driving a `session`-tier unit yourself.** Those run in this tree by default.
`ctx start --worktree` opts into a checkout and branch per unit instead, under
`.ctx/runtime/worktrees/<plan>/`, which `ctx merge` lands and removes:

```
cd .ctx/runtime/worktrees/auth-rotation/03-rotate
ctx unit 03-rotate          # arms the done-gate for this unit
```

### Environment variables

| Variable | Effect |
|---|---|
| `CTX_GATE=off` | Disable the done-gate (`off`, `0`, `false` and `disabled` are one decision). Not free: every use is journalled, and a policy can refuse it — see [Policy](docs/reference.md#policy-system-user-repository). |
| `CTX_STRICT=1` | As `--strict`. Don't export it in your shell profile: a bare `/ctx:task` then exits 1 and Claude Code abandons the command before it can ask you for the name. |
| `CTX_UNIT` / `CTX_PLAN` | Claim a unit for **this process**. Overrides the shared pointer in `state.json`, so two sessions in one tree stop clobbering each other's focus. Worktree-tier units already get their own `.ctx/runtime/`, so they need neither. |
| `CTX_GLOBAL_ROOT` | Move the global bundle store (default `~/.claude/ctx`) |
| `CTX_DEBUG=1` | Restore the traceback behind an unexpected error |
| `CLAUDE_PROJECT_DIR` | Where ledger discovery starts |

## Where the rest lives

| | |
|---|---|
| [docs/walkthroughs.md](docs/walkthroughs.md) | A small change (L1) and a large one (L2), end to end: spec, plan, dispatch, review, findings, merge, handoff. Context bundles and standing memory. |
| [docs/reference.md](docs/reference.md) | Every `ctx.yaml` key, policy layering, the eight verify kinds, phase gates, profiles, command trust, failure policy, exit codes and `--json`. |
| [docs/operations.md](docs/operations.md) | What lives on disk, CI, measurement, cost, troubleshooting, how it works, security, uninstalling, development and releasing. |
| [GUIDE.md](GUIDE.md) | The plain-language guide. |
| [report.md](report.md) | The current audit. Earlier ones are archived under [docs/history/](docs/history/). |

## Security

Report a vulnerability through **GitHub private vulnerability reporting**:
<https://github.com/ansh-n-chovatiya/context-ledger/security/advisories/new> —
that form, and not email or a public issue. **It is off on this repository until
a maintainer switches it on**, so read [Security](docs/operations.md#security)
before you rely on it. Scope and Windows specifics: [SECURITY.md](SECURITY.md).

## License

MIT © 2026 Ansh Chovatiya. See [LICENSE](LICENSE).
