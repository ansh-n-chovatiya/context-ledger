# Context Ledger — Enterprise Readiness Audit

**Target:** `context-ledger` v0.8.0 (`ctx`) — durable specs, plans, decisions and memory on disk for Claude Code
**Date:** 2026-09-10
**Commit:** `ab02287` (branch `main`, 31 commits, **0 tags**)
**Method:** 8 parallel audit agents, one per dimension, read-only, findings independently spot-verified by the lead
**Scope:** 9,585 lines of Python in `ctx/`, 10,598 lines of tests, 22 slash commands, 4 agent contracts, 7 hooks, CI

---

## 1. Verdict

**Not enterprise-ready today. Two of the product's three core promises are mechanically defeatable, and one path is a remote code execution vector.**

This is a well-engineered project by the standards of a solo tool. The architecture is layered with no import cycles, the runtime is 100% stdlib with zero network calls, atomic writes are real where they matter most, and 625 tests pass on 3 operating systems across Python 3.8–3.13. The module docstrings are unusually honest about past bugs. None of that is in question.

What is in question is the gap between what the tool **claims to enforce** and what it **mechanically enforces**. Context Ledger's entire value proposition is that "done" is something a gate can refuse to sign off on. The audit found five independent ways to reach `status: done` with nothing verified — and one of them is the *default* configuration on a freshly cloned repo.

| Promise | Status |
|---|---|
| State survives session death and compaction | **Largely holds.** Atomic writes verified on the hot paths; 8 concurrent processes × 40 increments produced exactly 320. Gaps are in prune, rotate, and migration (P1). |
| "Done" is gated by checks a model cannot fake | **Does not hold.** Five bypasses found, incl. an all-ERROR gate that reports "not blocking" and marks the unit done. |
| Safe to adopt into a company | **Does not hold.** RCE on `ctx doctor` against a cloned repo; no release artifact, no packaging, no policy layer, no SECURITY.md. |

One further result belongs in the verdict, because it cuts the other way: **nothing from the two prior audits has regressed.** All 8 `PRODUCTION-AUDIT.md` P0s and 11 P1s were re-verified against 0.8.0 and hold, six of them under mutation. The remediation this project did in 0.6.x stuck. The blockers below are new surface, not old ground being re-lost.

**Estimated work to close the P0 set: 3–5 focused days.** None of the blockers are architectural — they are missing guards on existing, well-placed seams. That is the good news, and it is the reason this report is worth acting on rather than filing.

---

## 2. Top blockers, ranked

| # | Severity | Finding | Anchor |
|---|---|---|---|
| 1 | **P0** | Cloned `ctx.yaml` achieves arbitrary code execution *before* the trust gate, via the availability probe | `ctx/cli.py:303-318` |
| 2 | **P0** | An all-ERROR gate is reported as "not blocking" and the unit is marked done — zero checks run | `ctx/cli.py:1664-1671`, `ctx/hooks.py:246-256` |
| 3 | **P0** | A dispatched runner can edit its own unit contract and findings ledger; the `diff` check exempts all of `.ctx/` | `ctx/verify.py:187,209-211` |
| 4 | **P0** | Real regressions are laundered into "missing tool" ERRORs by output sniffing | `ctx/verify.py:434-464` |
| 5 | **P0** | Re-running `ctx start` overwrites the review baseline → empty diff → automatic `approved` | `ctx/cli.py:1236-1246`, `ctx/snapshot.py:172` |
| 6 | **P0** | The documented CI recipe clones unpinned HEAD, then `ctx trust --yes` blanket-accepts PR-editable shell commands | `README.md:1135-1141` |
| 7 | **P0** | No release artifact: zero tags, no packaging, no SBOM — "we run 0.8.0" is unverifiable | repo root |
| 8 | **P0** | No policy layer; `CTX_GATE=off` silently disables the gate fleet-wide and is documented as the fix | `ctx/cli.py:893`, `README.md:1252` |
| 9 | **P0** | `main()` has no catch-all — unexpected exceptions print raw tracebacks to the user | `ctx/cli.py:2368-2380` |
| 10 | **P0** | 34 of 41 commands exit 0 on error; the CLI is unscriptable outside `HARD_FAIL` | `ctx/cli.py:2364-2380` |

---

## 3. Findings by dimension

### 3.1 Security and trust boundaries

**P0 — Arbitrary code execution from a cloned repo, in front of the trust gate.**
`ctx/cli.py:303-318`. `_availability()` probes whether `python3 -m pytest` can run by executing `python -c "…importlib.util.find_spec('pytest')…"`. The module name comes from the untrusted `verify.cmd.run` string in a committed `.ctx/ctx.yaml`. Two facts combine: `find_spec` on a *dotted* name imports the parent package, and `python -c` places the current working directory on `sys.path`. A hostile repo shipping `run: python3 -m evilpkg.sub` plus an `evilpkg/__init__.py` executes that `__init__` the moment anyone runs `ctx doctor` — the first thing the docs tell a new user to do. Reached from `ctx init` (`cli.py:366`), `ctx doctor` (`cli.py:838`) and `ctx ci` (`cli.py:2086`).

The bitter detail: the same run prints `MISS 1 of 1 command(s) not accepted on this machine`. The trust store correctly refuses to *run* the command, while the probe in front of it already did. `AUDIT.md:509` records this probe as the remediation for an earlier finding — **the fix introduced the hole.**

*Fix:* gate the probe behind `trust.is_accepted`; run it with `python -I` and a `cwd` outside the repo; or replace it with a pure `importlib.machinery.PathFinder` lookup that never imports a parent package.

**P1 — `exists` and `symbol` verify kinds are not trust-gated.** `ctx/verify.py:126` consults `trust.load` only for `kind == "cmd"`. A committed `ctx.yaml` can declare `{kind: exists, path: /Users/victim/.ssh/id_rsa, matches: "BEGIN OPENSSH"}` — absolute paths are honoured (`verify.py:278,309`) and the PASS/FAIL verdict becomes a content oracle over any readable file, with `symbol`'s `contains` echoing the probe string back into the gate message. Worse, `matches` is an unbounded regex (`verify.py:288`) and the gate's `timeout_seconds` is checked **only** on the `cmd` branch: a catastrophic-backtracking pattern was still running after 15s *inside the Stop hook*.

**P1 — Absolute host paths land in the committed journal.** `ctx/paths.py:96` — `rel()` falls back to the absolute path on `ValueError`, and `.ctx/.gitignore` covers only `runtime/`. Any tool call touching a file outside the repo writes the full host path into `.ctx/journal/<date>.md`, which is committed and pushed — leaking username, home layout, and often the names of unrelated private repositories.

**P1 — `redact` misses everyday credential shapes.** Verified pass-through: bare `Bearer <token>` without an adjacent `authorization`, `passphrase`/`PASSPHRASE` (the keyword list has `passwo?rd|passwd|pwd`), SendGrid `SG.x.y`, and any `KEY value` form using a space instead of `:`/`=`.

**P2 —** Hook `main` fails **closed** on a malformed payload, contradicting its own documented invariant (`ctx/hooks.py:33-37` — `_read_payload` runs *before* the `try:`); telemetry is written even when `telemetry.enabled: false` (`ctx/hooks.py:62`); `ctx plan --unit '12-../../../../escaped'` escapes the units directory (`ctx/cli.py:1127`); `bundle.resolve` treats a model-supplied name as an arbitrary filesystem path, so `ctx promote ~/.aws/credentials` copies it into the global store (`ctx/bundle.py:95`).

**Verified safe:** the `cmd` trust gate itself is sound — `verify.run` refuses unaccepted commands before any `subprocess` call, the store is keyed by resolved project path outside the repo, and `command_id` covers `run` + `cwd` + `env`. `miniyaml` is not exploitable (depth capped at 64, linear parsing, hand-rolled quoting to avoid backtracking). Slug-based traversal is blocked everywhere `slugify` is used. All git invocations use argv lists, never a shell.

### 3.2 The done-gate — the product's central claim

This dimension produced the most consequential findings, because the gate is the product.

**P0 — An unrunnable check scores as a passing check.** `ctx/cli.py:1664-1671` and `ctx/hooks.py:246-256`. `verdict_of` correctly ranks ERROR above PASS (`verify.py:174-180`), but both consumers then treat ERROR as non-blocking: the gate prints a warning and returns `None`, so the unit is marked `done`. Now combine with `PROFILES["code"] = []` (`config.py:150`): the default profile carries only `cmd` checks, and on any machine that has not run `ctx trust`, **every** `cmd` check returns ERROR. Clone the repo on a second laptop or in CI, `ctx start`, `ctx unit 01-api --status done` → *"warning: no check could run… so this is not blocking"* → status `done`, plan advances, board green, **zero checks executed**.

*Fix:* refuse `done` when no check reached PASS. An all-ERROR result is *ungated*, and ungated is not done. Keep ERROR non-blocking for the `Stop` hook — don't brick a session — but make it blocking for the explicit `--status done` transition.

**P0 — A runner can forge its own contract.** `ctx/verify.py:187` exempts everything under `.ctx/` from the `diff` scope check, and the `unit-runner` agent holds `Write` and `Edit`. A runner that cannot make the tests pass can edit its own unit file — delete the failing `verify:` entry, append `verified: [rubric, human]`, trim an acceptance criterion — and/or delete blocking findings from `.ctx/plans/*/findings/*.md`. `_check_judged` passes on the mere *presence* of a kind in `recorded` (`verify.py:479`); `_check_review` passes on an empty ledger (`verify.py:243`). Nothing hashes or compares the contract.

**P0 — Real regressions laundered as infrastructure.** `ctx/verify.py:434-464`. `_missing_tool` regexes scan the combined stdout+stderr of the *test run*, not the launcher's failure. A unit that deletes or renames a module produces `ModuleNotFoundError: No module named 'ctx.foo'` inside pytest output → matches → ERROR → non-blocking (above). The precise regression the gate exists to catch is the one it launders. The second pattern (`^.*?(\w+): (?:command )?not found`) trips on any test asserting a shell error string anywhere in a 10,000-line log.

**P0 — Re-running `ctx start` destroys the review baseline.** `ctx/cli.py:1236-1246` calls `capture_before()` unconditionally for every non-`done` unit, and `snapshot.capture` `rmtree`s the existing snapshot first (`snapshot.py:172`). `ctx start` never sets `running` — status stays `pending` until an explicit `--status done`. So the documented crash-recovery path ("run `/ctx:start` again") re-snapshots finished units over their completed state; `ctx review` then diffs post-work against post-work, hands the reviewer an empty package, and gets `verdict: approved`. `commands/review.md` warns against exactly this in prose, with nothing enforcing it.

**P1 — `--sign-off` is an unconditional write.** `ctx/cli.py:1055-1060` records the sign-off with no verifier verdict required, stored, or checked. `commands/verify.md` says "never sign off on their behalf" for the `human` kind — prose addressed to the same session that types the command. A model running `ctx verify --sign-off human --note "looks right"` has permanently satisfied a check whose entire purpose is that a person looked.

**P1 — `owns` is not mechanically enforced on the default path.** Nothing ever adds a `kind: diff` check (`PROFILES["code"]` is empty, `scaffold_unit` copies `config.verify`), so the unit template's own criterion "no file outside `owns` is modified" is judged by a model reading a report. When a `diff` check *is* present, `changed_files()` runs `git status --porcelain` over the whole tree while subagent-tier units get no worktree — so in the flagship concurrent wave it fails unit A on unit B's uncommitted work. And empty scope returns **PASS** with "no owned scope declared" (`verify.py:204`).

**P1 — `forbid` is advisory.** `on_pre_tool_use` detects an out-of-scope edit, sets a nudge, and returns `""` — **the write proceeds** (`ctx/hooks.py:137-165`). The nudge is a single-slot store, so N concurrent runners overwrite each other's warnings. `_scope()` reads `state.json` and ignores `work.claim()`/`CTX_UNIT` (`hooks.py:393-402`), while the warning text three lines above uses `work.claim()` — so a worktree terminal gets *another* unit's scope enforced under its own unit's name.

**P1 — `escalate_on_failed_round` cannot reach a re-dispatched runner.** `dispatch.instructions()` always passes the default `round=1` (`dispatch.py:202`); only `cmd_review` passes a real round. A unit that fails its gate and is re-dispatched runs on the same tier forever. Combined with the tiering design, on a 4-entry `tiers` config **the most capable model is unreachable by any code path in the dispatch flow.**

**P2 — No cap on wave fan-out.** `prepare()` caps only the *sum of self-declared* `budget_tokens` against a 250k default (`dispatch.py:129-137`). A planner writing `budget_tokens: 1000` on forty units passes the cap and books forty seats. Telemetry records `ms: 0` and no token count, so nothing ever measures real spend against the complexity weights that `config.py:70-78` candidly admits are guesses.

**Assessed sound:** the complexity→model tiering matches ADR 0002 — clamping is correct, `tier_up` returns unchanged rather than raising at the top, boundaries are inclusive-low and deliberate, and `unit.model` correctly wins over both score and escalation. The `reviewer` and `re-reviewer` contracts are genuinely strong: they name the package as the sole input, forbid re-deriving the diff, forbid re-adjudicating the mechanical scope section, and define `approved` as a conjunction. `agents/unit-runner.md` is the weak one, and it sits on the hot path — it is told "run the `verify` checks yourself" without ever being told the command.

### 3.3 Durability and concurrency

**Verified good first, because it is load-bearing:** `state.save` is a proper temp + `fsync` + `os.replace` (`state.py:57-70`), as is `frontmatter.Document.write` (`frontmatter.py:110-154`). The `O_EXCL` state lock holds under contention — 8 processes × 40 increments produced exactly 320, no lost updates. A truncated `state.json` does not wedge the tool. Journal appends are line-atomic (8 forks × 150 records: 1200/1200 present, 0 malformed).

**P1 — A newer-schema ledger breaks every hook, not just `ctx`.** `ctx/hooks.py:58`. `hooks.main` wraps everything in `except BaseException` to fail open — then carves out `except SystemExit: raise` immediately above it, and `config.load` raises `SystemExit` on a schema above the plugin's (`config.py:169`). A teammate on ctx 0.9 commits `schema: 2`; a teammate on 0.8 then gets a non-zero exit and a raw traceback on **every tool call**. The one condition deliberately excluded from fail-open is the most likely real-world one.

**P1 — `journal.prune` rewrites the archive non-atomically, then deletes the source.** `ctx/journal.py:181-187`. `target.write_text(...)` truncates months of accumulated archive and rewrites it *after* the day files that fed prior runs are already unlinked. A crash mid-write leaves a truncated archive whose inputs no longer exist. This is the only operation in the product that destroys history, and it is the one that does not use the atomic writer that already exists three files away.

**P1 — `worktree.path_for` is plan-agnostic.** `ctx/worktree.py:59` builds `.ctx/runtime/worktrees/<unit_name>` with no plan slug, while `branch_for` *does* include it. `remove()`'s own docstring claims the cross-plan collision was fixed — it was fixed for the branch only. So `ctx worktree remove 01-api --force` from plan-b deletes plan-a's live worktree and its uncommitted work. Numbered kebab unit names make the collision the common case.

**P1 — Unit files are read-modify-written across a multi-minute gate with no lock.** `ctx/cli.py:1687→1698`. `find_unit` parses at t0, the full verify suite runs (up to 240s per `cmd`), then `unit.set()` writes the **whole t0 document** back. Any write landing in that window is erased — including `_record_fork_point`'s `base_branch`, which disarms the merge-target guard `worktree.py` was built around. `frontmatter.write`'s docstring says there is "no read-modify-write window to serialise"; this caller is one.

**P1 — Migration writes `ctx.yaml` and `plan.json` with plain `write_text`** (`migrate.py:147,153`), so an interrupted bulk migration leaves a half-written config — and the module's "idempotent, rerun it" promise then fails, because `_CONFIG_LINE` matches against garbage.

**P1 — `findings/` and `phases/` are schema-stamped but invisible to migration.** `migrate.py:55-62` — `discover()` globs task/spec/questions/unit/decision/bundle plus `plan.json`, but not the findings or phases ledgers, which both write `ctx_schema`. So `ctx migrate --check` reports clean while findings ledgers sit at a schema the running plugin will silently misparse. Findings gate merges; a misread ledger is a merge that should have been blocked.

**P2 —** `telemetry._rotate` discards every append made during the rotate — **reproduced: 0 of 20 concurrent records survived** (`telemetry.py:66-69`); findings and phase ledgers are lost-update-prone between reviewer and implementer (`findings.py:305`); the stale-lock reclaim can theoretically hand the lock to two holders and unlinks locks it does not own (`state.py:91-107`, code-path only, not reproduced under 4-way contention); six durable, *committed* files are written truncate-first, including `DIGEST.md`, which is written from `on_compact` and `on_session_end` — i.e. at session death, by N concurrent agents, on a git-tracked file.

**P2 — A corrupt `state.json` silently resurfaces as "no active work"** (`state.py:49-50`), indistinguishable from a fresh repo, so the user re-dispatches work already running in a worktree.

### 3.4 Enterprise distribution and operations

**P0 — There is no release artifact.** Zero git tags. `.claude-plugin/marketplace.json` declares `"source": "./"` — the plugin *is* the default branch. No release workflow. An enterprise cannot pin, roll back, mirror, or reproduce a version: "we run ctx 0.8.0" is unverifiable, because 0.8.0 is whatever `main` contained when each engineer last synced.

**P0 — The documented CI recipe is a supply-chain hole.** `README.md:1135-1141` clones unpinned HEAD from a personal GitHub account, then runs `ctx trust --yes`, which rubber-stamps whatever shell strings that repo's `ctx.yaml` declares — a file any PR can edit. A malicious PR gets shell in CI. This is the single item that fails a security review outright.

**P0 — No packaging metadata at all.** No `pyproject.toml`, `setup.py`, or `package.json`. Nothing for `pip-audit`, Dependabot, Snyk, or Syft to read; no console-script entry point, so `ctx` on `PATH` is a manual symlink each engineer maintains; no `requires-python`, so the 3.8 floor is a README claim rather than an install-time constraint. **The mitigating fact worth stating plainly:** the runtime is 100% stdlib with zero network-capable imports anywhere in the tree, so the supply-chain *risk* is genuinely low — it is the supply-chain *evidence* that is absent, and that is what a platform team will not accept on trust.

**P0 — No policy enforcement, and an unlogged global off-switch.** `config.load` reads only the repo's own PR-editable `.ctx/ctx.yaml`; there is no org/user/system layer. `ctx/cli.py:893` honours `CTX_GATE=off`, and `README.md:1252` markets it: *"The gate keeps blocking and I can't finish. `CTX_GATE=off` disables it immediately."* One env var removes the control the tool exists to provide, with no audit record and no fleet-wide way to detect it.

**P1 — Unpinned GitHub Actions, no `permissions:` block**, and no `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, or Dependabot config. No documented vulnerability-disclosure channel — a required field on most vendor-intake questionnaires, and a conspicuous gap for a tool that executes `shell=True` commands arriving via `git clone`.

**P1 — Two engineers on one repo will collide.** `.ctx/.gitignore` is one line (`runtime/`), so plans, journal, digest and revisions are all committed. The journal is keyed on the **date** (`journal.py:44`), not the author, so two people working the same day conflict on nearly every merge — both `journal/2026-09-10.md` and `DIGEST.md` are dirty in the current tree. `plan.json` is one blob, so concurrent plan edits conflict and a mis-resolved JSON merge silently corrupts the wave graph. Worst of the three: **ADR numbers are allocated local-max+1** (`spec.py:240-253`), so two branches both emit `0003-*.md` under different slugs, merge cleanly, and leave two ADR 0003s — a silent collision with no git conflict. The genuinely conflict-free parts are per-unit frontmatter and the gitignored `state.json`.

**P1 — Command trust degrades to a rubber stamp in CI.** The store is keyed by resolved project path outside the repo (`trust.py:48-60`), so every ephemeral runner starts empty and `ctx ci` cannot pass without `ctx trust --yes`. The control is real for a human at a terminal and vacuous everywhere an enterprise actually runs it.

**P1 — No logging subsystem.** Zero uses of `logging` across `ctx/`. `telemetry.record` and `journal.append` swallow everything by design ("must not break a session"), so a read-only mount, a full disk, and a permissions error all present as "everything is fine." There is no way to raise verbosity for a support case and no structured log to ship anywhere.

**P2 —** Telemetry is opt-*out* and on by default; it is local-only and the code corroborates that claim, but there is no documented field list or retention statement, and the only thing keeping it out of a shared repo is a one-line gitignore that `git add -f` defeats. Versioning is *enforced* (CI asserts `plugin.json.version == ctx.__version__` — a real strength) but not automated: no tag, no release job, no documented checklist.

**Verified as not problems:** the Python 3.8 floor is real (no `match`, no PEP 604/585 syntax, no `removeprefix`, and CI actually tests 3.8). Offline operation is genuine. `ctx doctor`/`ctx ci` return proper 0/1 for CI gating. Windows is covered by a dedicated job asserting `bin\ctx.cmd` propagates non-zero exits — added after that exact bug shipped for six versions.

### 3.5 Architecture and CLI contract

**P0 — No catch-all in `main()`.** `ctx/cli.py:2368-2380` catches only `SystemExit`. Verified live: with `.ctx/tasks` occupied by a regular file, `ctx task` dumps a `FileExistsError` traceback with absolute temp paths. Every `mkdir`/`read_text`/`subprocess` across 41 command functions is an uncaught-exception surface, and the slash commands wrap invocations in `|| true`, so the user sees a Python stack trace where the prompt body expected a sentence.

**P0 — Errors exit 0 for 34 of 41 commands.** Only `{verify, ci, spec-ready, plan-check, doctor, migrate, trust}` fail loudly. `ctx status` with no `.ctx/` prints "no .ctx/ found" and exits **0**; so do "no active plan", "no unit X in plan", and every `_needs()`. The rationale is sound for the *slash-command* channel — but it is applied to the *only* channel, so a pipeline calling `ctx merge` cannot distinguish success from "there was no plan." *Fix:* add `--strict`/`CTX_STRICT=1` rather than changing the interactive default.

**P1 — No machine-readable output anywhere.** `--json` does not exist. Even `cmd_ci`, explicitly written for pipelines, emits `## budgets` / `  ok   name` prose a caller must regex — and `_echo`'s lossy-encoding fallback can silently mangle that text on Windows.

**P1 — `cli.py` is 2,380 lines, a quarter of the codebase, and imports 21 of 26 modules.** The five extractions that most reduce risk, in order: (1) `cli.py:151-341` — 191 lines of ecosystem taxonomy and subprocess probing → `ctx/detect.py` (`cmd_ci` already reaches back into the private `_availability`, which is the signal); (2) `_verify_plan`/`_gate_before_done` (`cli.py:1564-1671`) → `verify.py`, since both rebuild near-identical `verify.run` call shapes; (3) `_next_action` (`cli.py:1777-1850`) — 74 lines encoding the entire product decision tree, currently reachable only through argparse → `ctx/advice.py`; (4) `build_parser` (`cli.py:2125-2358`) → a `COMMANDS` registry, which is also what makes `--json`/`--strict` a one-place change; (5) the five-way duplicated plan-resolution preamble → `_plan_or_report()`. Note the copies differ in which arg they read (`args.plan` vs `args.name`) — a real trap for mechanical deduplication.

**P1 — Extensibility is uneven.** A new agent role is cheap and data-driven (a `models.<role>` key plus a `.md`) — that is the pattern to copy. A new command costs 2–3 coordinated edits in `cli.py` with no registry or entry-point seam. A new verify kind costs **four** coordinated edits in `verify.py` (`KINDS`, `COST`, two if-ladders) with no dispatch table.

**P2 —** 15 subcommands have no slash command, including `trust` — the security gate `_next_action` literally advises users to run. `config.py` declares 5 profiles and `--profile` accepts all 5, but `_PROFILE_MARKERS` knows only 4, so `research` can never be auto-detected. `LEDGER_PREFIX = ".ctx/"` is defined three times, none referencing `paths.CTX_DIRNAME`, which already holds it. The unit file path is hand-built in five modules instead of going through a `Layout` accessor. Two dead functions, one of which (`snapshot.discard_all`) would `rmtree` the entire snapshot root — dead code that deletes data.

**Structurally sound:** no import cycles (verified by AST walk over all 27 modules). Layering is real: `miniyaml → {config, frontmatter} → {spec, plan, findings, phases, state, work, bundle} → {verify, worktree, complexity, dispatch, review, briefing} → {cli, hooks}`. The highest-fan-in modules (`config` at 13 importers, `frontmatter` at 11) are both small and stable — the right shape.

### 3.6 Documentation

The README's *mechanics* are honest and code-verified — install, quickstart, hooks table, atomicity claims, profile weights, and the three-round findings cap all check out, and a scratch `ctx init` reproduced the documented output byte-for-byte. The problem is **currency**: 0.8.0 shipped a substantial feature set that never reached the reference sections.

**P1 — The entire 0.8.0 feature set is undocumented.** A grep across `README.md` for `kind: bug`, `reproduce`, `guard`, `test_first`, `model_for`, `models.tiers`, and `escalate_on_failed_round` returns **zero hits**. `/ctx:phase` has a complete command file and a registered subcommand, and appears in neither the slash-command table nor the CLI table. The `models:` and `complexity:` config blocks ship in every generated `ctx.yaml` and are absent from the configuration reference. An engineer who reads the CHANGELOG and goes looking for model tiering must read `ctx/complexity.py` to use it.

**P1 — Two directly checkable false claims.** `README.md:922` says "Seven kinds" of verify check and lists seven; `verify.py:37` has eight — `test_first` is fully implemented with its own cost weight and dispatch branch. `README.md:807` says the CLI commands below it have "no slash command by design", listing `ctx findings` — but `commands/findings.md` is a complete, cross-referenced slash command.

**P1 — 1,386 lines / 55KB is the wrong artifact for onboarding**, and is how the drift happened: it reads as accumulated release notes rather than a maintained reference. `GUIDE.md` (226 lines) is the actual onboarding doc and is well-scoped. *Fix:* README keeps Why/Requirements/Install/Quickstart/command table under ~300 lines and links to `docs/reference.md` and `docs/walkthroughs.md`.

**P2 —** No `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, or issue/PR templates; `.github/` contains only `workflows/ci.yml`. No upgrade guide beyond CHANGELOG prose, though 0.7.0's breaking changes (trust store moved, `ctx start` no longer auto-worktrees) are real migration material. `README.md:1153` says the suite runs "on macOS, Linux and Windows across Python 3.8–3.13" — only Ubuntu runs the full spread; macOS and Windows run 3.9 and 3.13.

### 3.7 Test suite — does green mean anything?

Measured, not assumed: **625 tests** (not the ~300 in the project's notes), all green in 26s, at **91% line / 89% branch** coverage of `ctx/`. Three targeted mutants were run against the full suite on scratch copies: `trust.is_accepted → True` killed 6 tests, `redact.scrub → identity` killed 8, `state.locked → no-op` killed 1. The trust boundary and the redaction filter are load-bearing, not decorative. There is no `os.chdir` anywhere, no network, every test gets its own `TemporaryDirectory`, one `skipTest` in the whole suite, zero `except: pass`. **This is a genuinely strong suite** — the findings below are about its edges and its meta-layer, not its centre.

**P0 — CI has no floor on what the suite must prove.** `ci.yml:41` runs `python -m unittest discover -s tests` and nothing else — no coverage, no threshold, no lint, no type-check, no dependency scan. And `unittest discover` **exits 0 on "Ran 0 tests"**: rename `tests/`, break a top-level import in `support.py` in a way that yields an empty suite, and 8 matrix jobs go green having proved nothing. The release signal for this project is "CI is green," and green does not currently entail "625 tests ran."

**P0 — The only guard on a 399-line vendored fixture is skipped in every CI run.** `tests/test_findings_rounds.py:171` corroborates `tests/fixtures/findings_pre_escalation_0_7_0.py` against `git show <ref>:ctx/findings.py`, and `skipTest`s when that fails. `ci.yml` uses `actions/checkout@v4` with no `fetch-depth:` — a depth-1 shallow clone — so the ref is never present and the test skips in all 9 jobs, on every push, forever. The byte-identity proof built on that fixture is therefore anchored to a file CI never re-validates: edit the fixture to match a regression and the suite stays green. *Fix:* `fetch-depth: 0` on one job, or assert the fixture's SHA-256 against a checked-in constant.

**P1 — A named security test does not exercise the boundary it names.** `tests/test_git_invariant.py:704`. With `trust.is_accepted` mutated to `return True`, six tests in that file fail — but `test_init_verify_now_does_not_probe_untrusted_candidates` **stays green**, so its assertion passes for some reason other than the trust gate. There is no positive control anywhere proving that `HOSTILE = "git commit --allow-empty -m HOSTILE"` actually lands a commit when it *is* run. A negative assertion with no demonstration that the mechanism can fire is the textbook fail-green shape: if the hostile ledger ever stopped being parsed at all, all four tests in that class would pass vacuously.

**P1 — The concurrency guarantee rests on exactly one test.** Removing the `state.locked` lock breaks precisely one of 625 tests. `state.py` has the suite's worst branch coverage (81%), and the uncovered lines are the entire contention surface: the stale-lock reclaim (`state.py:94-100`) and the `EACCES`/`EROFS` fallthrough (`:104-109`) that **silently disables locking** on a read-only checkout.

**P1 — The destructive tier's failure branches are the least covered.** `worktree.py` at 82% branch, 33 uncovered statements: the `git is not installed` (127) and `TimeoutExpired` (-1) returns (`:44-47`) are never taken, so nothing proves ctx degrades rather than crashes when git is absent or hangs; the `git worktree add` failure path (`:164-168`) is never exercised; and the unwritable-runtime-dir path at `:158`, whose comment insists "one unwritable runtime directory must not abort the dispatch of every other unit in the wave," is asserted only in prose.

**P2 —** `migrate.py` — the code that rewrites a user's existing `.ctx/` in place — has one referencing test file and 86% branch coverage; `ci.yml` smoke-tests `--check` on a *freshly scaffolded* ledger, the one case needing no migration. Several meta-tests are substring greps a comment would satisfy: `test_audit_wave3.py:271` "runs on three platforms" greps `ci.yml` for `"ubuntu-latest"` etc. as plain substrings, and `ci.yml` does contain prose comments naming jobs. `test_gates.py:538` `assertIn("gate", body)` is entailed by the next line. `tests/support.py:29` gives up on `rmtree` after 5 tries with no warning, so a real handle leak is indistinguishable from the benign macOS git race. `GIT_CONFIG_GLOBAL="/dev/null"` (`support.py:104`) works on Windows only by accident. Non-ASCII paths, CRLF sources, and large inputs are untested.

**What green means today:** the behaviours someone deliberately went and proved still hold — the git invariant against real subprocess git, concurrency via `threading.Barrier`, crash-recovery by killing a real child and reading state back in a third process. What it does not yet mean: that the suite ran at all, that the destructive tier degrades gracefully when git is absent or hung, that a real pre-existing ledger migrates, or that a contended or read-only filesystem behaves.

### 3.8 Prior-audit regression check

`AUDIT.md` (28 findings, closed at 0.6.0) and `PRODUCTION-AUDIT.md` (8 P0s, closed at 0.6.2) were re-verified against 0.8.0 rather than taken on faith. **No P0 or P1 from either audit has regressed.** All 8 production P0s and 11 P1s hold, and six of the highest-value guards were mutation-tested on a scratch copy — every one was caught: `trust.is_accepted → True` (+14 failures), `init` accepting `config["verify"]` (+2), dropping the `base_branch != target` refusal (+1), `verify.is_ledger → False` (+1), widening `_HEX32` back to entropy-shaped (+5), `miniyaml` no longer escaping newlines (+11 failures, +8 errors). That is a real result and it says the remediation held.

What did surface is **two guards the prior audit itself flagged as untested, still untested two minor versions later** — the code is present, but nothing would notice if it were deleted.

**P1 — The merge gate's PENDING refusal is unenforced by any test.** `ctx/worktree.py:442`. Mutating `if verdict == verify.PENDING:` → `if False:` survives the **entire 625-test suite**, and `PENDING`/`sign-off` appear nowhere in `test_worktree.py` or `test_merge_safety.py`. FAIL and ERROR at the same call site are both covered. This is PRODUCTION-AUDIT mutation-survivor #2 verbatim: the judged half of the done-gate — the half no command can decide, i.e. the half the product's pitch rests on — is guarded by code nobody is watching. *Fix:* arm a unit with an unsigned `rubric` check, assert `merge()` returns `False`, the message names sign-off, and the branch survives.

**P1 — `config.normalise_level` fails open and has zero tests.** `ctx/config.py:189`. Mutating the fallback from `"0"` to `"2"` survives all 625 tests; `normalise_level` appears nowhere in `tests/`, despite 8 call sites in `hooks.py` and `cli.py` gating on its result. A malformed `level:` in a shared `ctx.yaml` silently arms the Stop hook — and "levels default down" is listed in `AUDIT.md` §7 as the single most load-bearing design choice in the product. *Fix:* one four-line test.

**P2 —** `tests/test_audit_wave3.py:75` is still the doubly-guarded loop that cannot fail: an `init` regression writing an empty `verify:` — the exact failure mode the test exists to catch — makes it pass with zero assertions executed. `tests/test_core.py:116`'s length cap asserts nothing (a 51-char input against a 200-char cap). Three prior P2s remain correctly open but untouched: reserved Windows device names still reach `.ctx/tasks/con.md` (`bundle.py:35`), `_fit` still returns an empty briefing with no marker while `measure()` reports `truncated: False` (`briefing.py:199`, verified live), and `keep_days: 0` still lets the committed journal grow unbounded.

**Both audit documents should be archived rather than updated.** `AUDIT.md`'s header still reads "version 0.1.1 · 193-test suite" — four minor versions and 432 tests stale — and `PRODUCTION-AUDIT.md` already contradicts its central claim. `PRODUCTION-AUDIT.md` is the better document but is pinned at 0.7.0 and predates `complexity.py`, `phases.py`, `models.tiers` and the `test_first` kind, so a reader takes its "snapshot.py and findings.py: zero tests, zero callers" verdict as current when both now have dedicated suites. Carry the two hollow guards forward into an open tracker; retire the rest.


---

## 4. Remediation roadmap

Sequenced so each wave is independently shippable. This is deliberately shaped as a `ctx` plan — the tool should be used to fix itself.

### Wave 1 — Close the security hole and make the gate real (1–2 days, blocks any external use)

1. **Fix the availability probe** (`cli.py:303-318`) — gate behind `trust.is_accepted`, run with `python -I` and a `cwd` outside the repo, or replace with a non-importing `PathFinder` lookup. *This one ships alone, today.*
2. **Refuse `done` when no check reached PASS** (`cli.py:1664`) — an all-ERROR result is ungated, and ungated is not done. Require `--force` with a journalled reason.
3. **Stop laundering regressions** (`verify.py:434-464`) — classify as infrastructure only when the *launcher* failed (exit 127 / OSError), not by sniffing test output. Add an explicit `optional: true` for checks a project genuinely wants skipped.
4. **Un-exempt the work's own contract** (`verify.py:187`) — `.ctx/plans/*/units/*.md` and `.ctx/plans/*/findings/*.md` must not be invisible to the `diff` check, and `_gate_before_done` should compare the unit's frontmatter against the `before` snapshot.
5. **Protect the review baseline** (`cli.py:1236`) — `capture_before` refuses to overwrite without `--force`; `ctx start` sets `status: running` so a re-run reports in-flight units instead of re-snapshotting them.
6. **Trust-gate `exists`/`symbol`**, reject absolute and `..` paths, and put `matches` under the shared gate deadline.

### Wave 2 — Make the CLI safe to automate (1 day)

7. Catch-all in `main()` → `ctx <cmd> failed: <msg>`, exit 2, traceback behind `CTX_DEBUG=1`.
8. `--strict` / `CTX_STRICT=1` so error paths return 1 without changing the interactive default.
9. `--json` on `status`, `next`, `doctor`, `ci`, `verify`, `plan-check`, `findings` — route through one `_emit(data, human_fn)` helper.
10. Test the two hollow guards: the merge gate's `PENDING` refusal (`worktree.py:442`) and `config.normalise_level`'s fail-down fallback — both survive the full suite when mutated today.
11. CI floor: assert `Ran N tests` with `N >= 600`, add `--cov-fail-under=88`, add `ruff`, add `fetch-depth: 0` so the vendored-fixture guard actually runs.

### Wave 3 — Make it installable and governable (1–2 days)

12. `pyproject.toml` with `requires-python = ">=3.8"`, license metadata, `project.scripts.ctx = "ctx.cli:main"`.
13. Tag `v0.8.0`; release workflow producing wheel + sdist + SHA256SUMS + CycloneDX SBOM via PyPI Trusted Publishing; point the marketplace entry at the tag.
14. Rewrite the documented CI recipe: pinned install, and `trust.lock` verification instead of `trust --yes`.
15. Policy layer — system → user → repo precedence with a `locked:` section the repo cannot override; `CTX_GATE=off` journalled, never silent; `ctx doctor` prints the active policy source.
16. SHA-pin actions, add `permissions: contents: read`, `SECURITY.md`, `CODEOWNERS`, Dependabot.

### Wave 4 — Durability and multi-user (1–2 days)

17. Extract `atomic_write_text` and route `journal.prune`, `write_digest`, `plan.json`, snapshot manifests, and `migrate` through it.
18. Per-plan lockfile around the unit read-modify-write window and the findings/phases ledgers; rotate telemetry under the existing lock.
19. `worktree.path_for` takes the plan slug; `remove` refuses a tree checked out on a foreign branch.
20. Add `findings/` and `phases/` globs to `migrate.discover()`.
21. Per-author journal files, `DIGEST.md` derived and gitignored, ULID-based ADR ids (or a `doctor` check for duplicates).
22. `hooks.main` catches `SystemExit` and degrades to a one-line notice instead of failing every tool call.

### Wave 5 — Structure and docs (ongoing)

23. The five extractions from `cli.py` — `detect.py`, gate orchestration into `verify.py`, `_next_action` into `advice.py`, a `COMMANDS` registry, `_plan_or_report()`.
24. Dispatch table for verify kinds so a new kind is one dict entry rather than four coordinated edits.
25. Document the 0.8.0 feature set (`phase`, `kind: bug`, `test_first`, `models:`, `complexity:`); fix the "seven kinds" and "no slash command" claims; split the 55KB README into `README` + `docs/`.
26. `max_wave_units` hard cap; real spend recorded in telemetry so the complexity weights have something to be checked against.

---

## 5. What is genuinely good

Stated plainly, because a report that only lists defects misrepresents the codebase:

- **No import cycles** across 27 modules, with real layering and small, stable chokepoints (`config`, `frontmatter`).
- **Zero third-party runtime dependencies and zero network-capable imports.** The "local-only" claim is true, and the code proves it.
- **Atomic writes are real** on the paths that matter most — `state.save` and `frontmatter.write` both do temp + `fsync` + `os.replace`.
- **The `O_EXCL` state lock holds under contention** — 8 processes × 40 increments, exactly 320, no lost updates.
- **625 tests at 91% line coverage**, using real subprocess git in real throwaway repos rather than mocks, with crash-recovery proved by killing a real child process.
- **CI actually tests what the docs claim**: Python 3.8 on a pinned runner, three operating systems, and a dedicated Windows-wrapper job added after that exact bug shipped for six versions.
- **The version-drift check** (`plugin.json` vs `ctx.__version__`) closes a real class of bug in CI.
- **The reviewer and re-reviewer agent contracts are strong** — package as sole input, no re-deriving the diff, no re-adjudicating the mechanical section, `approved` defined as a conjunction.
- **The module docstrings are honest about past bugs**, which is rarer and more valuable than it sounds; several findings in this report were found *because* a docstring pointed at the seam.

---

## 6. Method

Eight audit agents ran in parallel, one per dimension, all read-only: security and trust boundaries; state durability and concurrency; test-suite honesty; enterprise packaging and operations; CLI architecture and contract; documentation truthfulness; prior-audit regression verification; and agent-orchestration semantics. Each was asked for verified findings with `file:line` anchors and concrete failure scenarios, and explicitly told not to pad.

Findings were cross-checked by the lead session against the source before inclusion — the RCE path, the `shell=True` call sites, the CI recipe, the test count, and the absence of tags and packaging metadata were each confirmed directly. Coverage figures and mutation results come from a real `coverage 7.x` run and three mutants executed against the full suite on scratch copies. Baseline: `python3 -m unittest discover -s tests` → 625 tests, OK, exit 0; `ctx doctor` and `ctx ci` both green.

Where agents disagreed with the project's own records, the code won: the suite is 625 tests, not ~300.
