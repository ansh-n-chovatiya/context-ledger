> **Historical.** This is the enterprise-readiness audit of **v1.0.0** at `0d7a45d`, archived. Every finding in it — the four P0s, the P1s bundled into its own Wave 1 and Wave 2, and the rest of its remediation roadmap in §4 (Waves 3 through 5, plus the findings its own §3 dimension write-ups named but never put on a wave) — has since been closed across three follow-up remediation passes. It is kept for the decisions it records, not as a description of the tool today; nothing in it has been re-checked against a later release.

# Context Ledger — Enterprise Readiness Audit

**Target:** `context-ledger` v1.0.0 (`ctx`) at `0d7a45d` — durable specs, plans, decisions and memory on disk for Claude Code
**Date:** 2026-09-13
**Method:** eight parallel read-only audit agents, one per dimension; 38 mutations on scratch copies; every finding below re-verified by the lead against the source.

---

## 1. Verdict

**Not enterprise-ready today. The product's central claim — that "done" is something a gate can refuse to sign off on — is defeatable three independent ways, and a fourth blocker executes repository-supplied code before the trust prompt that exists to stop it.**

The good news comes first, because it is structural and it is earned: **nothing from any of the three prior audits has regressed.** All ten `report.md` P0s, all eight `PRODUCTION-AUDIT.md` P0s and its eleven P1s, and `AUDIT.md`'s twenty-eight findings were re-verified against 1.0.0 — thirty distinct guards were mutation-tested and **thirty-five of thirty-eight mutants were killed by the suite**. The two guards the last audit caught as *present but untested two minor versions later* are now both armed: breaking the merge preflight's PENDING refusal kills two tests, and inverting `normalise_level`'s fail-down kills twenty-five. The remediation this project did in 0.6.x through 0.9.x stuck, and the suite genuinely watches it.

The blockers below are new surface, not old ground being re-lost. But three of the four sit on the gate, and the fourth sits on the security fix that closed the last audit's headline P0 — in each case because a guard was added at one layer and the layer beside it was left unvalidated.

| Promise | Status |
|---|---|
| State survives session death and compaction | **Holds.** Atomic writes verified on every hot path (11 tests die when temp+rename is removed); the lock is real (6 die when `O_EXCL` is dropped); 8 processes × 40 increments lose nothing. Gaps are a seal read-modify-write and the absence of a directory fsync — both P1/P2. |
| "Done" is gated by checks a model cannot fake | **Does not hold.** A one-character typo in `kind:` deletes a check silently; `kind: diff` passes on zero changes and re-arms the all-ERROR bypass v0.8.0 closed; `ctx merge` skips the gate's entire preamble. |
| Safe to adopt into a company | **Does not hold.** `ctx doctor`, `ctx ci` and `ctx init` execute a repo-supplied interpreter before the trust gate. The documented CI recipe pins a tag that has never existed. A tag push publishes the release having run zero tests. |
| The ledger is honest about its own state | **Largely holds**, with one sharp exception: every findings file states that critical findings block the done-gate. They do not, unless the unit opted into `kind: review` — which 6 of this repo's own 63 units do. |

**Estimated work to close the P0 set: 2–4 focused days** — less than the 3–5 the last audit estimated and hit, because none of these are architectural. Each is a missing validation at a seam that already exists: check `kind` against `KIND_TABLE` the way `tier` is already checked against `TIERS`; make `diff` distinguish *nothing stray* from *nothing at all*; route `merge` through `gate_check` instead of `verify.run`; validate `parts[0]` in the probe the way `parts[2]` already is.

---

## 2. Top blockers, ranked

| # | Severity | Finding | Anchor |
|---|---|---|---|
| 1 | **P0** | `ctx doctor`/`ci`/`init` execute a repository-supplied interpreter before the trust gate — RCE on clone | `ctx/detect.py:240-252` |
| 2 | **P0** | A one-character typo in a check's `kind:` deletes it from the gate; every surface reports green | `ctx/verify.py:132-136` |
| 3 | **P0** | `kind: diff` returns PASS on zero changes, re-arming the all-ERROR bypass v0.8.0 closed | `ctx/verify.py:348`, `:1665` |
| 4 | **P0** | `ctx merge` never calls `gate_check` — no seal check, no contract comparison, no usable-check refusal | `ctx/worktree.py:513`, `:689` |
| 5 | **P1** | `wave:` is not a sealed contract field; editing it relabels a scope violation as a sibling's work | `ctx/contract.py:61`, `ctx/review.py:150` |
| 6 | **P1** | Open critical findings do not block `done` unless the unit declares `kind: review` — 6 of 63 do | `ctx/verify.py:1556`, `ctx/findings.py:92` |
| 7 | **P1** | `ctx verify --plan` ("this is what CI runs") exits 0 when not one check could run | `ctx/verify.py:1490` |
| 8 | **P1** | The documented CI recipe pins `CTX_REF: v0.8.0` — a tag that has never existed | `docs/operations.md:101` |
| 9 | **P1** | A tag push publishes wheel, sdist and SBOM having run zero tests | `.github/workflows/release.yml:6-9` |
| 10 | **P1** | `ctx trust` never shows the reviewer the `env:` block it is accepting | `ctx/commands.py:2985-2986` |

---

## 3. Findings by dimension

### 3.1 Security and trust boundaries

**P0 — The availability probe executes a repository-supplied interpreter, before any trust check.** `ctx/detect.py:240-252`, reached from `ctx/commands.py:380` (`init`), `:1144` (`doctor`), `:3242` (`ci`). `availability()` splits the `run` string from the committed `.ctx/ctx.yaml` and, if `os.path.basename(parts[0]).startswith("python")`, runs `subprocess.run([parts[0], "-c", _PROBE, top])`. The v0.8.0 audit's headline P0 was an RCE in this same probe; the fix hardened the *module name* (`_MODULE_NAME`, `cwd=tempdir`, argv not interpolation). `parts[0]` — the binary actually executed — is still whatever the repository wrote.

*Reproduced by the lead.* A scratch repo containing an executable `python3-shim` and a `ctx.yaml` naming it by absolute path:

```
$ python3 -m ctx doctor
## verify commands
  ok   …/probe/python3-shim -m json (available; pass --verify to run it)
## command trust
$ ls …/probe/PWNED
…/probe/PWNED
```

The marker exists. The command is reported **available**, and the trust section prints *after* the code has already run. The absolute path makes a developer machine a guess but CI deterministic — GitHub Actions checks out at a known path — so an unreviewed PR gets execution in the pipeline.

*Why the suite is green:* `tests/test_audit_probe_isolation.py:111` is titled `TestTheProbeRunsNoRepositoryCode` and every hostile case builds its command as `f"{cli._python_exe()} -m evilpkg.sub"` — the real interpreter. No test plants a binary in the repo. The class name asserts more than the tests check.

*Fix:* require `parts[0]` to resolve outside the project root, or to be `sys.executable`. The module-name validation three lines below is the model.

**P1 — `ctx trust` never shows the `env:` block it is accepting.** `ctx/commands.py:2985-2986` prints `run` and `cwd` only; same omission in `doctor`'s pending list (`:1181`) and in `verify._label_cmd` (`ctx/verify.py:1107`), the label that reaches the journal and the model. `trust.command_id` *does* digest `env`, so acceptance is correctly per-env — the defect is that the one human review the module exists to force is blind to the field most worth reviewing. `trust.py:69-71` claims the id is "a digest of exactly what would be executed: the command, where, and with what environment"; the prompt shows two of the three. A `PATH` pointing at an in-repo `evilbin/` is accepted as `pytest -q`.

**P1 — A repo `ctx.yaml` voids a locked policy key by assigning its parent.** `ctx/config.py:318-329`. `_lock_holder` matches a lock on `dotted` or any prefix of it, so `locked: [gate]` covers `gate.enabled`. The reverse is unguarded: a lock on `gate.allow_override` does not cover an assignment to `gate`, and `_assign` replaces the whole mapping. A repo adding one line — `gate: []` — collapses the falsy list to `{}` in every reader's `(config.get("gate") or {})`, reverting `allow_override` to its `True` default. No refusal is recorded, so `ctx doctor`'s policy section stays silent.

**P2 — `redact:` patterns from `ctx.yaml` can hang a hook forever.** `ctx/redact.py:137-141` runs `re.sub` unbounded; `except re.error` catches compile failures only. `verify._match_within` already solved this shape — pattern in a killable child, bounded by the gate clock. `redact.scrub` runs on the journal write path inside `PostToolUse`/`Stop` and never got it. `scrub('a'*40 + 'X', [r'(a+)+$'])` does not return.

**Assessed sound:** path confinement refuses `../`, absolute paths and in-repo symlinks before the stat, re-checked after `realpath` on both sides. Only two `shell=True` sites exist, both behind trust checks; every other `subprocess.run` passes an argv list. The trust store is content-addressed on `sha256(run+cwd+env)` and lives outside the repo, so there is no accept-then-swap window. Slug→path normalisation is shared rather than copied across five modules and collapses `..`, `/`, NUL and long names to one component. `ctx ci` gates on `trust.lock` when present, closing the ephemeral-runner hole. The HTML preview escapes `&<>` to `\uXXXX` so a value cannot close the `<script>`.

### 3.2 The done-gate — the product's central claim

**P0 — A one-character typo in `kind:` deletes the check, and every surface calls it green.** `ctx/verify.py:132-136`:

```python
def ordered(checks):
    """Checks sorted cheapest-first, dropping anything malformed."""
    valid = [c for c in (checks or [])
             if isinstance(c, dict) and c.get("kind") in KIND_TABLE]
```

*Demonstrated by the lead:*

```
KIND_TABLE keys: ['cmd','diff','exists','human','review','rubric','symbol','test_first']
ordered([{'kind':'rubric',…}]) -> 1 check(s) survive
ordered([{'kind':'rubrik',…}]) -> 0 check(s) survive
verdict_of([])                 -> pass
```

A `rubric` check that blocks until a human signs off becomes a silent PASS. `test-first` — the natural hyphen spelling of a documented kind — disappears the same way, as does `contains`, a name two of this audit's own agents reached for unprompted. `ctx doctor` prints `ok`, `ctx ci` prints `all checks passed`, `ctx plan-check` exits 0, and `ctx verify --plan` reports `1 passed`. An entry with no `kind:` at all prints `ok None (no command to probe)` and `ctx trust` offers the literal string `None` for acceptance.

`plan.check` validates `tier` against `TIERS` explicitly at `ctx/plan.py:275-279`; the identical check for `kind` against `KIND_TABLE` was never written. It refuses only when *every* check is unusable (`plan.py:285`).

The behaviour is pinned by `tests/test_verify_kinds_table.py:228`, `test_an_unregistered_kind_is_still_dropped_loudly_enough`, whose body asserts `results == []` and `verdict == PASS`. The name says loud; the assertions prove silent.

*Fix:* refuse an unregistered `kind` in `plan.check` and in `ctx doctor`'s config validation, naming the nearest registered kind. Dropping malformed entries at runtime is right — never telling anyone is not.

**P0 — `kind: diff` returns PASS on zero changes, re-arming the bypass v0.8.0 closed.** `ctx/verify.py:348` returns `Result("diff", …, PASS)` whenever nothing *stray* was written — which includes writing nothing at all. `ctx/verify.py:1665` refuses `done` only when `not any(r.status == PASS for r in results)`, with a comment explaining that this branch exists because "a freshly cloned repo that has never run `ctx trust` errors *every* check". A unit carrying both `diff` and a `cmd` check on an untrusted or missing toolchain now produces:

```
warning: not every check could run for 01-zeta — configuration, not a work
failure, so this is not blocking:
  ok      diff: changed files within owned scope
  warn    cmd: … tool not available
01-zeta: done
```

The unit wrote nothing. `diff` costs 0 so it always runs first, and its vacuous PASS satisfies the "some check did pass, so the gate is not blind" comment at `verify.py:1684`. The gate is precisely blind. **13 of this repo's own 63 units carry `kind: diff`** (the reporting agent said all 63; the code says 13 — the mechanism is unchanged, the blast radius is smaller).

*Fix:* `diff` should return a distinct verdict when the changed set is empty. An idle unit and a correct unit must not be indistinguishable to the one check that costs nothing.

**P0 — `ctx merge` is a complete route around the gate's preamble.** `ctx/worktree.py:513` calls `verify.run` directly at `:583` and never `verify.gate_check`. `gate_check` has exactly two callers, both in `cmd_unit` (`ctx/commands.py:2745,2753`), and it is the only place steps 0–2 live: dispatch-seal present, contract intact, checks usable. A unit that `ctx unit --status done` refuses —

```
refusing to mark 01-gamma done — its contract changed after it was dispatched:
  changed: verify
```

— is merged and marked done by `ctx merge` on the next line. The same command refuses a stray file with advice to "widen `owns` and re-plan"; taking that advice *after dispatch* produces a contract `contract.compare` reports as moved, which `merge` then does not consult. A unit with **no dispatch seal at all** merges cleanly. `worktree.py:689` then writes `status="done"` through a stale document read at `:521`, across a full gate run and a `git merge` — the exact stale-write hazard `_set_unit_status` (`ctx/commands.py:1957`) was written to prevent, and `worktree.py` takes no lock anywhere.

**P1 — The phase gate is never consulted by the done-gate.** `ctx/phases.py:83-91`. No module outside `phases.py` and `cmd_phase` reads it. A `kind: bug` unit reaches `done` with zero phases recorded — no reproduce, no locate, no fix, no guard. `phases.py:8` claims "`can_enter` is the only door, and it is closed by default"; `Ledger.add` (`:330`) is public and calls `_add` with no gate check, so `add("fix")` is accepted where `can_enter("fix")` refuses, after which `can_enter("guard")` returns `(True, '')`.

**P2 — A gated repo can switch its own Stop gate off in silence.** `ctx/hooks.py:292` returns `""` when `gate.enabled` is false, with no journal entry. `.ctx/ctx.yaml` is a path the runner is explicitly permitted to write (`verify.is_ledger`). `ctx doctor` prints `enabled=False` but adds nothing to its exit code; `cmd_ci` never examines the key. The env route (`CTX_GATE=off`) journals every bypass and can be refused by policy; the in-repo route does neither.

**P2 — `ctx ci` never runs the unit gate**, contradicting `verify_plan`'s docstring at `ctx/verify.py:1425` ("This is what CI runs"). `verify_plan`'s only callers are in `cmd_verify`'s `--plan` branch. The verified-baseline `ctx ci` → exit 0 gated no unit.

**Assessed sound:** the 0.9.2 `symbol` fix is real and its test is now honest — the fixture is built from `string.printable` specifically so the character-wise bug cannot pass by luck, and deleting the fix kills three tests. The same shape in `test_first` and `diff` fails *closed*. An empty `verify` list is refused on all four paths. Every individual kind returns ERROR rather than PASS when it cannot run. The gate sees uncommitted work: `changed_files` uses `git status --porcelain --untracked-files=all`, and a `since` no longer an ancestor of HEAD is a FAIL. `--force` and `--skip-gate` both still run the gate and journal what they overrode.

### 3.3 Durability and concurrency

**P1 — `contract.seal_findings` is an unlocked read-modify-write, and a lost entry disarms the tamper check.** `ctx/contract.py:396-410`; all six callers sit outside any lock. The findings *ledger* is correctly serialised under `plan-<slug>`, so concurrent adds both land. The *seal* is not: both processes read, both mutate a copy, last writer wins. `findings_drift` then compares the ledger against a seal that never knew about finding #2, so #2 can afterwards be deleted by hand and `gate_check` reports the contract intact. Forced-interleaving repro: ledger `[1,2]`, seal `['1']`, drift `[]`.

**P1 — `ctx merge` blames the unit for ctx's own uncommitted ledger writes.** `ctx/worktree.py:145` and `:453` filter `.ctx/` out of both preflights by design; `git merge` does not. The runner commits its journal on the branch, the orchestrator's journal is dirty in the integration tree, and the merge aborts with *"Your local changes would be overwritten"*. Because the merge never started, `_conflicted` returns nothing and ctx falls to the else branch at `:674`, printing *"one unit wrote outside its scope"*. The unit did nothing wrong and the real remedy is never mentioned.

**P2 — No atomic write anywhere fsyncs the parent directory.** `ctx/atomic.py:62-64`, `ctx/frontmatter.py:168-171`, `ctx/state.py:71-74` all stop after `fsync(file)` + `os.replace`. The docstrings scope the promise to process death, which is honest; nothing notes that the guarantee stops there.

**P2 — The lock's fail-open is completely unobservable.** `ctx/lock.py:238-265` fails open on an unwritable locks directory, `EROFS`, any unclassified `OSError` (`:212`, a bare `return None, None`), and on timeout. All five call sites bind `as taken` and discard it; `lock.py` imports no logger and `ctx doctor` has no lock check. A wave that loses an increment leaves no trace.

**P2 — The "every ledger write is atomic" guard is blind to `path.open("w")`.** `tests/test_durable_writes.py:104` walks the AST for `.write_text(...)` only. `ctx/review.py:261` truncates with `path.open("w")` and appears in neither the enumerated list nor the deliberate-exclusion list, so the test's claim that every module is accounted for is satisfied vacuously.

**P2 — A concurrency test that cannot fail.** `tests/test_review_telemetry.py:122` spawns two threads with no barrier and asserts `alpha_stats["bytes"] != beta_stats["bytes"]` — on a fixture that deliberately gives the two units different-sized changes, reading per-unit files, in a class whose docstring says it carries no module-level state. The assertion holds identically if the threads are replaced by two serial calls. It is the only test claiming to cover concurrent review-package reads.

**Assessed sound:** `lock._reclaim`'s rename-then-verify-token design closes the stat-then-unlink window and its test forces the interleaving with a positive control. `telemetry.record` holds one acquisition across rotate and append; 0/20 without the lock, 20/20 with it. The journal's eight-process fork test was instrumented and genuinely overlaps on this machine. `state.py` always had this right. Timestamp ordering sorts on `(HH:MM, author, file position)` and depends on mtime nowhere except the lock's 60–120s staleness margin. `tests/test_ledger_locks.py`, `test_lock_reclaim.py`, `test_plan_lock.py` and `test_journal_authors.py` all force their interleavings and carry positive controls — they are the model the rest should follow.

### 3.4 Enterprise distribution and operations

**P1 — The documented CI recipe pins a tag that has never existed.** `docs/operations.md:101` sets `CTX_REF: v0.8.0` inside the copy-pasteable workflow, with prose calling the pin load-bearing. `git rev-parse v0.8.0` → *unknown revision*. `git tag -l` returns exactly `v1.0.0`. A team following the docs is red on push one, and the obvious fix is the one the surrounding prose spends two paragraphs warning against.

**P1 — A tag push publishes the release having run zero tests.** `.github/workflows/release.yml:6-9` triggers on `tags:`; `.github/workflows/ci.yml:3-6` triggers on `branches: [main]`, which no `refs/tags/*` ref matches. `release.yml` declares no `needs:`, runs no `unittest`, no `ruff`, no coverage gate, and creates the GitHub Release with wheel, sdist, SBOM and SHA256SUMS in about two minutes. `tests/test_packaging.py:390` asserts the trigger, the artefacts, SHA pinning and permissions — nothing about a test gate. `SHA256SUMS` ships unsigned with no build provenance, so the checksums and the artefacts they cover travel from the same place with the same trust.

**P2 — HEAD ships as 1.0.0 but is not the tagged 1.0.0, and the marketplace serves HEAD.** `.claude-plugin/marketplace.json:10` is `"source": "./"`; `plugin.json:4` is `1.0.0`; the tag is at `fbbe889` = `HEAD~1`. The documented primary install clones the default branch. Two engineers installing a week apart both report `ctx 1.0.0` from different trees, neither matching the released SHA256SUMS — and an engineer already at `fbbe889` will never receive `0d7a45d`, because `plugin update` compares declared versions, not commits. This is the project's own recorded failure mode, live at HEAD.

**P2 — The README documents 0.8.0 at 1.0.0.** `README.md:71,94,95`: `pip install ./context_ledger-0.8.0-py3-none-any.whl` is a file that no release produces, and the two verification comments tell a reader to expect `0.8.0` where the tool prints `ctx 1.0.0`. (`README.md:289` refers to the archived 0.8.0 audit and is correct.)

**P2 — CI verifies an sdist built by a different backend than the release builds.** `ci.yml:279` pins `hatchling==1.27.0`; `release.yml:74` pins `1.32.0`. `SdistContainsWhatTheReadmeLinksTo` — the only check that builds the archive and reads it rather than grepping the include list — runs solely in the coverage job under the older pin.

**P2 — The packaging guard module cannot be imported by name.** `python3 -m unittest tests.test_packaging` → `ModuleNotFoundError: No module named 'test_ci_floor'`, `Ran 1 test`, `FAILED (errors=1)` *(reproduced by the lead)*. Line 45 inserts the repo root but not `tests/`; `tests/test_supply_chain.py:40` does the insert correctly for the identical import. Every structural assertion about what ships runs only under `discover`.

**Assessed sound:** Python 3.8 support is real — no `match`, no runtime `X | Y`, no `tomllib`/`graphlib`/`zoneinfo`/`functools.cache`/`removeprefix` anywhere in `ctx/`, and `worktree.py:228` names the 3.8 floor as the reason it hand-rolls a prefix strip. Version consistency across `__init__.py`, `plugin.json`, `CHANGELOG` and the tag is enforced by CI before anything builds. The no-network claim is true and verified independently by the lead: no `socket`/`urllib`/`http`/`requests`/`ssl` import in `ctx/`, `hooks/` or `bin/`, and all 38 modules import stdlib only. The SBOM is CycloneDX, reproducible, generated from the installed venv and checksummed. Neither workflow contains `continue-on-error`, `if: always()`, `|| true` or `set +e`, and every `uses:` is SHA-pinned with explicit per-job permissions. The 0.8.0→1.0.0 upgrade trap was deliberately disarmed: `contract.any_seal` falls through when no unit of a plan has a seal, so a pre-0.9.0 in-flight plan is not bricked.

### 3.5 Architecture and the CLI contract

**P1 — `ctx verify --plan` exits 0 when not one check could run.** `ctx/verify.py:1490` is `return 1 if failed else 0`; `warned` and `pending` are counted, printed, then dropped. `cmd_verify` for the same gate is explicit about the distinction (`ctx/commands.py:1528`: "0 pass, 1 a criterion failed, 2 nothing could be run"), and `verdict_of`'s own docstring names this fold as the historic defect — *"Folding it into a PASS is how the gate came to sign off on work it had never checked."* `docs/operations.md:145` tells users to put this command in their pipeline.

**P1 — `ctx doctor` and `ctx ci` validate `verify:` twice and disagree.** `ctx/commands.py:1132-1137` counts a non-mapping entry as a problem; `ctx/commands.py:3237` filters non-mappings away before looking, then reports `none configured`. Given `verify:\n  - "pytest -q"`, doctor exits 1 and ci exits 0 with `all checks passed`. `ci` is the one documented for pipelines and the one the PR template gates on.

**P1 — `--json` stdout can be corrupted by a child process.** `ctx/cli.py:488` uses `contextlib.redirect_stdout`, which rebinds `sys.stdout` without touching fd 1. Every `subprocess.run` in the package uses `capture_output=True` — except `webbrowser.open` (`ctx/commands.py:1857`), whose `GenericBrowser` path inherits stdout. `BROWSER=… ctx preview <plan> --open --json` puts the browser's chatter ahead of the document and `json.load` raises. Contradicts `docs/reference.md:806` ("stdout carries one document and never prose beside one").

**P2 — The exit-code contract comment in `cli.py` is false.** `ctx/cli.py:461-472` says there are only three codes and that 1 is "an advisory condition, escalated, and only under `--strict`". 1 is the ordinary check-failed code with no flag anywhere, in `doctor`, `ci`, `spec-ready`, `verify`, `migrate --check`, `merge` and `findings`. `docs/reference.md:779-786` states the real rule correctly; the comment a maintainer reads first does not.

**Assessed sound:** the `--json` envelope is genuinely uniform — all eight commands emit the same eight top-level keys, verified both in a populated ledger and against a directory with no `.ctx/`, and `JSON_COMMANDS` is derived from the registry so the flag and the list cannot drift. A refusal under `--json` yields valid JSON on stdout, exit 2, and the reason on stderr — never a traceback. `cli.py:506-516` catches everything into one stderr line and exit 2, so an internal error is never confusable with a check failure; `KeyboardInterrupt` is deliberately re-raised. Argparse edge cases all exit 2 with usage and no traceback. `cli.py` is 551 lines with zero storage access, and `tests/test_no_import_cycles.py` is a real property test. A systematic AST scan across `ctx/`, `tests/`, `hooks/`, `bin/`, `commands/`, `docs/`, `skills/` and `agents/` found exactly one unreachable symbol: `DISPATCHABLE` (`ctx/dispatch.py:16`). All 24 broad excepts were reviewed and each one around a pass/fail decision fails closed.

### 3.6 Documentation truthfulness

Every shell command in `README.md`, `GUIDE.md` and `docs/` was executed in throwaway projects rather than read.

**P1 — "exactly three advisory conditions" — there are four.** `docs/reference.md:788-797` states it three times, including "turns exactly those three into exit 1 **and nothing else**". *Verified by the lead:* `commands.ADVISORY` is `{'snapshot-truncated','missing-argument','no-active-work','plan-slow'}` — four. A valid, collision-free serial plan fails `ctx plan-check --strict` with exit 1 for a condition no table names.

**P1 — The profile table says `research` needs an explicit flag; five markers auto-detect it.** `docs/reference.md:440` renders the marker column as *(explicit `--profile`)*. `ctx/detect.py:57-59` carries `*.bib` and `*.bibtex` at strength **10** — equal to `package.json` — plus three more at 2. A directory containing `refs.bib` and a `Makefile` initialises as `profile=research` with a rubric-only gate: no mechanical check at all. The code comment at `detect.py:49-50` explains that this table did not know about `research`; the table still does not. This collides with the project's own stated principle at `docs/operations.md:428` — "a default that always passes makes an unguarded project look guarded".

**P2 — `operations.md:192` names a regression test that does not exist.** It claims `tests/test_packaging.py` asserts there is no network-capable import in the tree. The fact is true; that file's 26 tests cover dependencies only. A reviewer takes the named test as evidence and skips the check.

**P2 — `ctx worktree remove X --force` reports success for a worktree that never existed**, so a typo'd unit name leaves the broken worktree on disk behind a confident `removed worktree and branch for zzz-nonexistent-unit` and exit 0.

**P2 — The unit-runner contract invokes a bare `ctx` the default install does not provide.** `agents/unit-runner.md:42,45` instructs the runner to call `ctx phase`; all 24 slash commands call `"${CLAUDE_PLUGIN_ROOT}/bin/ctx"` and no agent contract mentions it. Prose docs are disclaimed for this; an agent contract has no reader who can go look it up.

**Module docstrings.** These are unusually candid and mostly accurate, which is why the exceptions matter — a maintainer writes code on them. `ctx/journal.py:8` points at `ctx digest --semantic`, a flag that does not exist. `ctx/snapshot.py:10` says "every file is fingerprinted"; a committed `ctx.yaml` can *replace* the ignore set, and `review: {ignore: ["ctx/"]}` blinds the scope check to the whole source tree. `ctx/miniyaml.py:22` promises that anything outside the subset raises rather than guessing; `loads('a: [1,2')` returns `{'a': '[1,2'}` — a truncated list silently becomes a string, which is the interface-freeze incident the same docstring claims the rewrite ended *(verified by the lead)*. `ctx/redact.py:19` says every pattern is anchored on something a human can point at; `_HEX32` is bare length-and-charset, and `scrub('md5 d41d8cd98f00b204e9800998ecf8427e')` returns `'md5 <<redacted>>'` — write-path and irreversible. `ctx/preview_page.py:15` claims `check()` refuses any external reference "no image URL"; `<img src>`, `<use href>`, `<object data>`, `<embed>`, `srcset` and `<base href>` all pass, including the paragraph's own lead example.

**Assessed sound:** the full L2 walkthrough — `spec → ask → plan → plan-check → preview → start → review → findings → unit --status done` — matches the documentation verbatim, including collision and refusal texts. The phase-gate listing is byte-identical to `walkthroughs.md:431-437`. `ctx init`'s output is byte-accurate. Redaction-on-render is exactly as claimed: a planted AWS-shaped key is absent from the review package and present raw in the snapshot. All counted claims that `tests/test_docs_currency.py` covers are correct — 8 verify kinds, 8 `--json` commands, 24 slash commands, 4 agent contracts, 7 hooks, the three briefing caps. Every CHANGELOG entry maps to a real commit and describes what it did. All four `docs/history/` banners are accurate. `tests/test_docs_currency.py` is real and works; every finding above sits in a gap it does not cover — version strings in examples, git refs in recipes, the `ADVISORY` count, the profile marker table, and module docstrings.

### 3.7 Prior-audit regression check and mutation testing

**No P0 or P1 from any of the three archived audits has regressed.** Re-verified by execution rather than reading: `miniyaml` round-trips all eight hostile shapes from C5/C6/H11; `plan.collisions` is indexed rather than pairwise; `journal.prune` reads day files whole and writes through `atomic.write_text`; `bin/ctx.cmd` uses `goto` so `exit /b %ERRORLEVEL%` expands at run time; all 23 slash commands end their `!` line with `|| true`.

**The hollow-guard question, which this dimension exists to ask: both prior survivors are now armed.** The two guards the v0.8.0 audit flagged as present-but-untested two minor versions later are pinned at 1.0.0 — mutating the merge preflight's PENDING refusal (`worktree.py:607`) kills two tests including a positive control; inverting `normalise_level`'s fail-down (`config.py:703`) kills twenty-five, because `tests/test_config_levels.py:135` asserts the exact value rather than membership precisely so an upward fallback cannot pass.

**Mutation results: 38 mutations across 30 distinct guards, 35 killed, 3 survived.** Every run was the full 1864-test suite on a scratch copy. Representative kills: done-gate FAIL/PENDING refusal → 25; `verdict_of` ERROR ranking → 38; dispatch seal written by `ctx start` → 39; briefing budget truncation → 102; reserved Windows device names → 296; trust-store acceptance → 29; contract forgery check → 17; ledger exemption in `diff` scope → 22; redaction → 19; atomic write → 11; the lock's `O_EXCL` → 6.

The three survivors are all on the **security** fix, not the gate, and all three are equivalent mutants today — verified non-exploitable, not merely assumed:

| Guard | Anchor | Why it survived |
|---|---|---|
| `_confined`'s absolute-path fast path | `ctx/verify.py:564` | `_inside()` re-checks after `realpath`, so `/etc/hosts` is still refused. The content-oracle P1 is genuinely closed; the fast-path half of a two-layer guard is untested. |
| Probe drops `sys.path[0]` | `ctx/detect.py:211` | With the line deleted, a hostile `evilpkg/__init__.py` still yields `(False, 'cannot import evilpkg')` and writes no marker — the tempdir `cwd` catches it. The sibling layer *is* watched. |
| Probe module-name validation | `ctx/detect.py:244` | The name reaches the child only as `argv[1]`; `find_spec` does not execute. |

`detect.py:194-205` enumerates four layers of defence on the probe. **Two of the four have no test.** That is the same shape the last two audits caught twice — now sitting on the security fix rather than on the gate, and directly adjacent to blocker #1, which is the layer nobody thought to enumerate at all.

**Still open from prior audits, never closed:** `forbid`/`owns` enforcement in `on_pre_tool_use` remains advisory — every branch sets a nudge and returns `""`, so the write proceeds — and still names the wrong unit, because `hooks.py:225` resolves the name through `work.claim()` (which honours `CTX_UNIT`) while `_scope` at `:474` reads only `state.json`. `ctx verify --sign-off human` remains an unconditional write; for L2 units this is now largely neutralised because `verified` is a sealed field, but L1 task work has no seal, so the model can still satisfy the one check whose purpose is that a person looked. `PRODUCTION-AUDIT.md` H8 (Windows `PATHEXT`: all seven hooks registered as bare `*.py` fail to launch silently on a venv-only install) was archived while explicitly open, and the only record of it lives inside a document whose header says it does not describe the tool today.

### 3.8 Agent orchestration semantics

**P1 — `wave:` is not a sealed contract field.** `ctx/contract.py:61` seals `verify/owns/reads/forbid/depends_on/verified/acceptance criteria`. `wave` is the one scope-determining field left out, and `ctx/review.py:150` trusts a present value verbatim (`members = [u for u in units if u.wave == unit.wave]`), deriving it from the graph only when absent. The runner holds `Write` over its own unit file by design. A unit that writes outside its `owns` is correctly refused; changing one line from `wave: 1` to `wave: 2` and re-running yields `done`, exit 0 — and the review package then prints *"Scope violations: None"* and tells the reviewer the file is another unit's work, omitting its diff.

**P1 — The done-sibling excuse is self-justifying.** `ctx/review.py:172` excuses a done sibling's `owns` while they are uncommitted. The violating write is what makes them uncommitted. `01-a` finishes, gates, commits; `02-b` then overwrites `src/x.py`, which reclassifies the already-reviewed `01-a` as "still writing" and excuses the path. `review.py`'s docstring anticipates and dismisses this — "the done-gate already refuses a contract that changes after dispatch" — but `contract.compare` hashes frontmatter, not owned source, and `01-a`'s gate has already run.

**P1 — Open critical findings do not block `done`.** `gate_check` never consults the findings ledger; the only consumer is the opt-in `kind: review`. *Verified by the lead:* `ctx/findings.py:92` writes into **every** findings file that "`critical` and `important` block the done-gate while they are open", and `ctx/commands.py:2465` prints the same claim unconditionally. `ctx findings 02-beta` reports one blocking finding and exits 1; `ctx unit 02-beta --status done` on the next line succeeds. `plan.scaffold_unit` seeds from `config["verify"]`, which `ctx init` never populates with `kind: review`, and neither `commands/plan.md` nor `commands/start.md` mentions it. **6 of this repo's own 63 units declare it.**

**P2 — `ctx start --wave N` dispatches a wave whose prerequisites have not run.** `ctx/dispatch.py:125-136` consults the "lowest wave with unfinished units" guard only when `level is None`. An explicit `--wave 2` seals, snapshots and dispatches a unit whose `depends_on` is still `pending`, with no warning — after which the two can run genuinely concurrently, voiding the read/write-race check, since `plan.collisions` runs only within a computed wave.

**P2 — `--status addressed` closes a finding with no evidence and re-seals authoritatively.** `ctx/findings.py:297` and `:304` guard `disputed` (needs evidence) and `parked` (needs a ruling); `:311` accepts `addressed` unconditionally, and the caller then seals with `authoritative=True` so `findings_drift` will not report it. The module docstring names the failure it is preventing — "performative agreement … which reads as progress, closes nothing, and leaves the defect in the tree" — and states the mechanism is the absence of an `acknowledged` status. `addressed` with no justification is that status under another name.

**Assessed sound:** baselines are captured before the work and cannot be silently retaken — `snapshot.capture` refuses a second capture without `force`, `ctx start` skips both `capture_before` and `contract.seal` for an already-dispatched unit keyed on seal/snapshot existence rather than the runner-writable `status:`, and `--rebaseline` refuses a moved contract and names `--reseal`. Every diff and baseline walks the working tree, not the index. Plan-time parallelism warnings genuinely fire: a four-unit plan all owning `src/hub.py` produced both "every wave is one unit wide" and the bottleneck warning, via an index rather than string equality. Cycles and same-wave collisions are refused before anything is sealed. Worktree paths are plan-scoped; a crashed unit's worktree is reused with the baseline untouched; `merge` refuses a detached HEAD, a wrong `base_branch`, a dirty integration tree and a branch that changed nothing. Findings storage is a locked re-read-modify-write with on-disk id minting, and unknown severities fail *towards* blocking. The review round cap refuses past `MAX_ROUNDS = 3` rather than passing.

---

## 4. Remediation roadmap

Sequenced as a `ctx` plan — the tool should be used to fix itself. Each wave is independently shippable and ends green.

### Wave 1 — Close the execution path and the two silent gate holes (1 day, blocks any external use)

| Unit | Owns | Done when |
|---|---|---|
| `01-probe-interpreter` | `ctx/detect.py` | `availability()` refuses a `parts[0]` that resolves inside the project root. A test plants an executable in the repo and asserts no marker is written, for all four entry points. |
| `02-unknown-kind` | `ctx/plan.py`, `ctx/commands.py` | `plan.check` and `ctx doctor` refuse an unregistered `kind:`, naming the nearest registered one. `test_an_unregistered_kind_is_still_dropped_loudly_enough` is rewritten to assert the refusal its name promises. |
| `03-diff-vacuous` | `ctx/verify.py` | `_check_diff` returns a non-PASS verdict when the changed set is empty. A unit that wrote nothing cannot reach `done` on a `diff` + errored-`cmd` pair. |

### Wave 2 — Make `ctx merge` the same gate as `ctx unit` (1 day)

| Unit | Owns | Done when |
|---|---|---|
| `04-merge-gate` | `ctx/worktree.py` | `merge` calls `verify.gate_check`, so seal, contract and usable-check refusals apply. A unit with no seal, and one with a post-dispatch `owns`, are both refused. |
| `05-merge-status-write` | `ctx/worktree.py` | The `status="done"` write goes through `_set_unit_status` — plan lock held, document re-read. |
| `06-merge-ledger-abort` | `ctx/worktree.py` | An aborted merge caused by uncommitted `.ctx/` paths is reported as such, not as a scope violation. |

### Wave 3 — Make the honest claims true (1 day)

| Unit | Owns | Done when |
|---|---|---|
| `07-findings-block` | `ctx/verify.py` | `gate_check` consults the findings ledger directly, so `findings.py:92`'s promise holds without `kind: review`. |
| `08-seal-wave` | `ctx/contract.py` | `wave` joins `FIELDS`. |
| `09-seal-findings-lock` | `ctx/contract.py` | `seal_findings` holds `plan-<slug>` across read and write. |
| `10-verify-plan-exit` | `ctx/verify.py` | `verify_plan` returns 2 when nothing could run, matching `cmd_verify`. |

### Wave 4 — Docs and release (1 day)

| Unit | Owns | Done when |
|---|---|---|
| `11-version-strings` | `README.md`, `docs/operations.md` | No `0.8.0` outside `docs/history/`; `CTX_REF` names a tag that exists. `tests/test_docs_currency.py` pins version strings and git refs. |
| `12-counted-claims` | `docs/reference.md`, `tests/` | The `ADVISORY` count and the `_PROFILE_MARKERS` table are generated from or pinned against the code. |
| `13-release-gate` | `.github/workflows/` | `release.yml` depends on the matrix, or `ci.yml` also triggers on tags. |
| `14-packaging-import` | `tests/test_packaging.py` | `python -m unittest tests.test_packaging` runs its 26 tests. |

### Wave 5 — The untested layers (ongoing)

Tests for the three surviving mutants (`verify.py:564`, `detect.py:211`, `detect.py:244`); `trust` prompt shows `env:`; policy lock covers parent assignment; `redact.scrub` bounded like `_match_within`; `tests/test_review_telemetry.py:122` forces its interleaving or is deleted; `test_durable_writes.py` recognises `open(..., "w")`.

---

## 5. What is genuinely good

**The remediation stuck, and the suite proves it.** Thirty-five of thirty-eight mutants died. That is the single most important result in this report, and it is not a common one — most codebases with 1864 tests cannot say which of their guards are actually watched. This one now can, for thirty of them.

**The tests that force interleavings are exemplary.** `test_ledger_locks.py`, `test_lock_reclaim.py`, `test_plan_lock.py`, `test_lock_fails_loud.py` and `test_journal_authors.py` use marker-file rendezvous, monkeypatched seams and `SIGXFSZ`, and every one carries a positive control proving the unguarded version fails. `test_plan_lock` demonstrates 8×40 losing nothing *and* the unlocked version demonstrably losing. This is how concurrency is tested.

**The 0.9.2 `symbol` fix is the model for how to close a finding.** The bug was a string iterated character-wise; the fix promotes a bare string to a one-element list; the test builds its fixture from `string.printable` *specifically so the old bug cannot pass by luck*, adds a positive control, and pins the single-character case separately. Four tests die if the fix is removed. The project's own note records that this test passed for the wrong reason twice before it was honest — and the third version is honest.

**`docs/history/` is a genuinely good institution.** Four archived documents, each with an accurate banner saying what it superseded and what has not been re-checked, and several test files cite them by section as the source of the finding they pin. Most projects delete this.

**The CI is better than most commercial pipelines.** Every `uses:` SHA-pinned with a version comment, explicit per-job permissions, `contents: write` only where needed, `id-token` structurally asserted absent, no `continue-on-error` or `|| true` anywhere, a reproducible CycloneDX SBOM, and a linter pinned exactly with a comment explaining that an auto-upgrading linter turns an unrelated push red. The `--json` envelope is derived from the command registry so the flag list cannot drift, and a refusal yields valid JSON, exit 2 and a stderr reason — never a traceback.

**The module docstrings remain the best documentation in the project**, and their candour is what made several findings here findable. A docstring that says "this is what used to be wrong and here is why it isn't now" is worth more than any comment discipline. Where they are wrong, they are wrong by drifting behind a later fix, not by overclaiming from the start.

---

## 6. Method

Eight audit agents ran in parallel, one per dimension: security and trust boundaries; the done-gate; durability and concurrency; enterprise distribution and operations; architecture and the CLI contract; documentation truthfulness; prior-audit regression and mutation testing; and agent-orchestration semantics. Each was given the verified baseline, the defect shapes this codebase actually produces, and an explicit instruction not to pad.

Findings were re-verified by the lead against the source before inclusion. Confirmed directly rather than relayed: the probe RCE (reproduced end to end — a scratch repo, a planted `python3-shim`, `ctx doctor`, and the resulting marker file); the silent drop of an unregistered `kind` (`ordered()` called on `rubric` vs `rubrik`, and `verdict_of([])`); `_check_diff`'s vacuous PASS and the refusal it defeats, read at both anchors; `worktree.merge`'s absence of any `contract` reference and `gate_check`'s two callers; `contract.FIELDS` lacking `wave` and `review.py:150` trusting it; the findings-block claim at `findings.py:92` against a `gate_check` that never mentions findings; the `ADVISORY` set being four; `miniyaml.loads('a: [1,2')`; `_HEX32` redacting an md5; `phases.Ledger.add` bypassing `can_enter`; `detect.py`'s five `research` markers against the table that says none; `python3 -m unittest tests.test_packaging` failing to import; and the non-existence of tag `v0.8.0`. Counts were taken from the tree, not from the docs.

Mutation testing ran on scratch copies at `/tmp/ctx-mutate*`, since deleted. The working tree was confirmed unmodified afterwards: `git status --porcelain -- ctx tests docs README.md CHANGELOG.md .github` returns zero lines, there are no untracked files, HEAD is unmoved at `0d7a45d`, and the suite re-run after all mutation work returned 1864 tests, OK. The only modified file is the journal, which ctx's own `PostToolUse` hook appends to on every shell call.

**Baseline, established before the agents were dispatched:** `python3 -m unittest discover -s tests -q` → 1864 tests, OK (3 skipped), 91s. `python3 -m ctx doctor` → exit 0 with one expected `warn` about verify drift on six finished units. `python3 -m ctx ci` → exit 0. `ruff check ctx/ tests/` at the CI-pinned 0.16.7 → All checks passed. 38 modules and 18,309 lines in `ctx/`; 24 slash commands, 4 agent contracts, 7 hooks, 97 commits.

**Where the audit's own brief disagreed with the code, the code won, three times.** The brief stated 90 test files and 33,390 lines in `tests/`; there are **93 files and 33,789 lines**. It stated that v1.0.0 was tagged at `0d7a45d`; the tag is at `fbbe889`, and **HEAD is one commit past it** — which turned out not to be a clerical detail but blocker-adjacent, since the marketplace serves HEAD while the release artefacts describe the tag. And its baseline block listed `ruff check` as an expected-green command: ruff was not installed on this machine at all, and the first attempt reported exit 0 from `tail` rather than from ruff. It was installed at the pinned version and re-run before anything was claimed about it. A check that silently reports success when it cannot run is the defect class this report spends the most words on; it appeared first in the audit's own method.
