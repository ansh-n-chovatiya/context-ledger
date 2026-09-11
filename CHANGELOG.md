# Changelog

## Unreleased

Making `ctx` installable and governable: packaging metadata and a tag-gated
release workflow, a policy layer above `ctx.yaml` that a pull request cannot
overrule, a committed lockfile of the shell commands the gate is allowed to run,
and a supply-chain floor for the repository's own CI. The documented CI recipe
that shipped before this was itself the hole: it cloned unpinned `HEAD` from a
personal account and then ran `ctx trust --yes`, which accepts whatever the
branch under test declares.

### Breaking

- **The documented CI recipe changed, and the old one should be replaced.**
  `ctx trust --yes` is gone from it and `ctx trust --verify-lock` is in its
  place, and the clone is pinned to a tag or a commit SHA instead of `--depth 1`
  of the default branch. `--yes` on an ephemeral runner accepts whatever the
  branch under test declares, and `verify.cmd.run` executes with a shell, so a
  pull request that added one line to `ctx.yaml` ran it with the job's token.

  *Migration.* Run `ctx trust --lock`, commit `.ctx/trust.lock`, and swap the
  step. Nothing changes on a developer machine: the lockfile grants nothing
  locally, and `ctx trust` / `--yes` still mean exactly what they meant.

### Added

- **Packaging metadata.** `pyproject.toml` builds an sdist and a wheel from a
  hatchling backend. The distribution is `context-ledger`; the import package
  and the console script are both `ctx`. Two properties are asserted
  structurally by `tests/test_packaging.py` rather than promised in prose: the
  runtime dependency list is empty, and the version is *derived* from
  `ctx/__init__.py` rather than restated, so pyproject cannot drift from the
  source. `requires-python = ">=3.8"` turns the README's "3.8+" into something
  pip refuses an install over. The sdist `include` is an allowlist, so a new
  top-level directory has to be added on purpose to ship — which is what keeps
  `.ctx/runtime/` and `tests/fixtures/` out of it.
- **A release workflow that fires on `v*` and nothing else.**
  `.github/workflows/release.yml` compares the tag, `ctx.__version__` and
  `.claude-plugin/plugin.json` before building, and exits non-zero if the three
  do not describe one release. It builds, installs the built wheel into a clean
  venv and runs `ctx --version` from it, emits a CycloneDX SBOM and asserts the
  component list is exactly `context-ledger`, writes `SHA256SUMS`, and attaches
  the four artefacts to the GitHub Release. **It does not publish to any package
  index** — no `twine`, no Trusted Publishing, no `id-token: write` — and
  `tests/test_packaging.py` asserts that absence, so a publish step cannot
  arrive by accident.
- **A policy layer above `ctx.yaml`.** `/etc/ctx/policy.yaml` (system) and
  `~/.claude/ctx/policy.yaml` (user) are read before the repository's own file,
  in that order, and a `locked:` key — as a list of dotted keys or as a mapping
  that sets and locks in one line — names what a later layer may not change. A
  lock covers its prefixes. `locked:` inside `.ctx/ctx.yaml` is ignored and says
  so: a repository cannot lock its own settings. `ctx doctor` grows a `##
  policy` section naming each layer, every live value that came from above the
  repository, and every attempt a lower layer made to change a locked one.

  Note the shape of it: because `ctx init` renders every default key into
  `ctx.yaml`, an **unlocked** policy value is overwritten by any initialised
  repository. `locked:` is the lever that holds.
- **`CTX_GATE=off` is journalled, and refusable.** Every site that honours the
  variable goes through one decision, so `off`, `0`, `false` and `disabled` mean
  the same thing everywhere (the CLI honoured three spellings and the Stop hook
  four), and each use appends a `gate | CTX_GATE` entry naming the site. A
  policy with `gate.allow_override: false`, locked, refuses the bypass outright:
  the gate runs, and the attempt is journalled either way. If the journal will
  not take the line the bypass still happens — failing a session over a full
  disk is worse than the thing it protects — and says so on stderr and in
  `runtime/hook-errors.log`.
- **`ctx trust --lock` and `ctx trust --verify-lock`.** `--lock` writes
  `.ctx/trust.lock`, a committed, deterministic record of every `cmd` the ledger
  declares: sorted by command id, no timestamp, no hostname, no absolute path,
  `\n` endings, and each entry carrying `run`, `cwd` and `env` beside its id so
  the diff is reviewable by a human. `--verify-lock` checks the ledger against
  it and **accepts nothing**, which is what makes it safe to run on a runner.
  Where a lockfile exists, `ctx ci` gates on it and reports machine-local
  acceptance as a note; `ctx ci` now fails on a declared command absent from the
  lockfile, a lockfile that will not parse, and a lockfile declaring a schema
  newer than the plugin understands. Neither flag accepts anything locally, so
  the developer-machine boundary is exactly what it was.
- **Supply-chain floor for this repository.** Every `uses:` in
  `.github/workflows/` is pinned to a full commit SHA with the version in a
  trailing comment; `.github/dependabot.yml` moves those SHAs forward weekly so
  the pins stay current on purpose rather than by neglect; `SECURITY.md`
  declares GitHub private vulnerability reporting as the only disclosure
  channel, response-window targets, and the latest released minor as the only
  supported version; `CODEOWNERS` routes review for the paths where a bad change
  is quiet rather than loud. `tests/test_supply_chain.py` holds all of it.

### Documentation

- **The CI recipe in the README no longer clones unpinned `HEAD` and no longer
  runs `ctx trust --yes`**, and it explains why in the two sentences a reader
  needs before simplifying it back.
- **Install, release and security are documented**: what to install and what
  lands on `PATH`, the Python floor and where it is enforced, the two-file
  version bump and the tag that has to agree with both, and the disclosure
  channel. Stated plainly rather than implied: `context-ledger` is not on PyPI,
  and GitHub private vulnerability reporting is off by default and off on this
  repository, so the advisory link does not accept a report until a maintainer
  enables it.
- **Stale behavioural claims corrected.** `CTX_GATE=off` is no longer described
  as a consequence-free escape hatch in any of the three places it appeared.
  `--rebaseline` no longer claims to re-seal the contract — it refuses a changed
  one and names `--reseal`, which is now documented alongside it. A unit with no
  dispatch seal is documented as refused, and as `unsealed` on the wave board,
  rather than as a note the gate falls through. The `diff` check is documented
  as comparing against the commit recorded at dispatch, and as *failing* when
  that commit has been amended or rebased away. "There is no TDD support" is
  gone, since `test_first` shipped.
- **Two documentation traps closed.** The sample `ctx.yaml` put comments on the
  parent keys of nested blocks, which `ctx`'s own YAML reader rejects — a reader
  who pasted the "safe to hand-edit" sample got a ledger that would not load.
  Every YAML sample in the README now parses. The README also held its net line
  count flat while absorbing four new sections, by compressing prose rather than
  by growing.

### Known gaps

- The `Stop` hook still reads `CTX_GATE` through its own inline
  `os.environ.get` check at `ctx/hooks.py:219` instead of the shared decision,
  so a bypass *there* is neither journalled nor refusable by policy. `ctx
  doctor` is. Documented as a caveat rather than described as working.
- A policy file that exists but cannot be *opened* — wrong permissions — is
  skipped as if absent, though `config._read_policy` documents itself as fatal
  in that case. A file that parses to something other than a mapping, or that
  will not parse at all, does stop the command.

---

*Everything below is audit-remediation wave 1, shipped earlier in this same
unreleased cycle. Its section headings repeat those above.*

Wave 1 of the enterprise-readiness audit remediation. The audit found five
independent ways to reach `status: done` with nothing verified — one of them the
*default* configuration on a freshly cloned repo — and one path that executed
code from a cloned repository before the trust gate could refuse it. Six
findings, closed here. It also carries the change that makes the CLI safe to
drive from a script: errors and refusals stop exiting 0.

### Breaking

- **A refusal now exits 2, on stderr, for every command.** Previously only
  `verify`, `ci`, `spec-ready`, `plan-check`, `doctor`, `migrate` and `trust`
  failed loudly; the other 34 commands printed the reason to stdout and exited
  0, so `ctx status` in a directory with no `.ctx/` succeeded and a pipeline
  could not tell "it worked" from "there was nothing to work on". Refusals now
  go to stderr with exit 2, which leaves stdout clean for whatever reads it.
  The slash commands are unaffected: every `commands/*.md` file ends its `!`
  line with `|| true`, so a refusal still reaches the prompt body that explains
  what to do about it.
- **An unexpected exception is one line instead of a traceback.** `main` catches
  what it did not anticipate and prints `ctx «command» failed: «message»` on
  stderr with exit 2. Set `CTX_DEBUG=1` to get the traceback back when you are
  the one debugging it.
- **`--strict` / `CTX_STRICT=1` escalates the advisory conditions to exit 1.**
  Exactly three conditions are advisory — a missing argument, nothing active for
  the command to act on, and a snapshot truncated by `review.max_files` — and
  all three still exit 0 by default, because each one's notice *is* the answer.
  A script that would rather hear about them asks with `--strict`, or sets
  `CTX_STRICT=1` for a single run. The flag can only turn strictness on:
  `CTX_STRICT=0` disables the environment variable, not an explicit `--strict`.

  Do not export `CTX_STRICT=1` in a shell profile. Under it a bare `/ctx:task`
  exits 1, and a non-zero `!` line makes Claude Code abandon the slash command
  before the prompt that would have asked you for the name is read.

  *Migration.* A script that treated exit 0 as "the command had something to
  say" now needs to treat 2 as a refusal and 1, under `--strict` only, as an
  advisory condition. A script that used to grep stdout for `no .ctx/ found`
  should read stderr or check the exit code. Nothing needs to change for anyone
  driving `ctx` from the slash commands.

### Security

- **Arbitrary code execution from a cloned repo, in front of the trust gate.**
  `_availability` decided whether `python3 -m «module»` could run by executing a
  probe with the module name interpolated into its source. `find_spec` on a
  *dotted* name imports the parent package to read its `__path__`, and `python
  -c` puts the working directory at the front of `sys.path` — so a hostile
  repository shipping `run: python3 -m evilpkg.sub` plus an `evilpkg/__init__.py`
  ran that file the moment anyone typed `ctx doctor`, which is the first thing
  the docs tell a new user to do. The probe now passes the name in `argv`,
  resolves only its top-level component, drops the working directory from
  `sys.path`, and runs in an empty scratch directory. The trust store had always
  refused to *run* the command; the probe in front of it already had.
- **`exists` and `symbol` were a content oracle over the whole disk.** Neither
  kind is trust-gated — neither executes anything — but both honoured absolute
  paths, so a committed `ctx.yaml` declaring `{kind: exists, path:
  /home/you/.ssh/id_rsa, matches: "BEGIN OPENSSH"}` turned the PASS/FAIL verdict
  into one bit per gate run about any file you can read, with `symbol` echoing
  the probe string back into the transcript. Both kinds now resolve their path
  inside the project — `realpath` on both sides, so `..` and symlinks are caught
  — and refuse anything that climbs out. `matches:` was also an unbounded regex
  from a committed file with no deadline: a catastrophic-backtracking pattern
  was still running after fifteen seconds *inside the `Stop` hook*, where a
  killed hook returns no decision at all. It now runs in a killable child
  process on the gate's remaining `gate.timeout_seconds`.

### Correctness

- **An unrunnable check no longer scores as a passing check.** `verdict_of`
  ranked ERROR above PASS, but both consumers treated ERROR as non-blocking, so
  `ctx unit «name» --status done` printed *"no check could run… so this is not
  blocking"* and marked the unit done. With `PROFILES["code"]` carrying only
  `cmd` checks, any machine that has never run `ctx trust` errors **every**
  check — so clone, `ctx start`, `--status done` gave a green board with zero
  checks executed, on the default configuration. A gate in which no result
  reached PASS now refuses the transition: ungated is not done. An ERROR beside
  at least one PASS is still only a warning, and the `Stop` hook still does not
  block on one — ending a turn is not a claim that the work is finished.
- **`ctx merge` was a complete route around that refusal.** The same all-ERROR
  verdict in `worktree.merge`'s preflight warned, merged, and then set
  `status="done"`. It now refuses on the same condition, in the unit's own
  worktree, and names the configuration problem. The neighbouring PENDING
  refusal — a `rubric` or `human` check nobody has signed off — was a hollow
  guard: mutating it away survived the entire suite. It has tests now.
- **A runner could forge the contract it was judged against.** `verify.is_ledger`
  exempts everything under `.ctx/` from the `diff` scope check — it has to, or a
  wave of concurrent units deadlocks on false Criticals — and the `unit-runner`
  agent holds `Write`. So a unit that could not make the tests pass could edit
  the test instead: delete the failing `verify:` entry, trim an acceptance
  criterion, append `verified: [rubric, human]`, or delete the blocking findings
  a reviewer raised against it. `ctx start` now seals a digest of the fields that
  constitute the promise — `verify`, `owns`, `reads`, `forbid`, `depends_on`,
  `verified`, acceptance criteria — and the done-gate compares against it.
  `status:` is deliberately outside the digest; flipping it is the edit the
  transition exists to make. Findings are sealed the same way and the seal never
  weakens: `ctx findings --add`/`--set` re-seals authoritatively, while a
  `critical` or `important` finding deleted, downgraded or closed by hand reads
  as a changed contract. A unit with no seal is not a violation — the gate says
  so and falls through, because failing closed there would brick every in-flight
  plan on upgrade.
- **Re-running `ctx start` destroyed the evidence `ctx review` judges against.**
  `capture_before` ran unconditionally for every non-`done` unit and
  `snapshot.capture` began with an `rmtree`, while nothing ever set `status:
  running` — so the documented crash-recovery step ("run `/ctx:start` again")
  re-snapshotted finished units over their completed state, and the review then
  diffed post-work against post-work, handed the reviewer an empty package, and
  got `verdict: approved`. `snapshot.capture` now refuses to overwrite an
  existing snapshot without `force`, dispatch marks its units `running`, and a
  second `ctx start` names the units it left alone.
- **Real regressions were laundered into "missing tool" ERRORs.** `_missing_tool`
  scanned the combined output of the *test run*, so a unit that deleted a module
  produced `No module named 'ctx.foo'` inside pytest's own report and the precise
  regression the gate exists to catch became a non-blocking warning; the second
  pattern tripped on any test asserting a shell error string anywhere in a long
  log. Whether a tool exists is now asked of the machine before the command runs,
  and the launcher's own exit code (127, or 9009 on `cmd.exe`) decides the rest.
  Output sniffing survives only on a check that asks for it by name.
- **Both doors out of the done-gate now leave the same trail.** `ctx unit
  --status done --force` still runs the gate, prints what it refused, and
  journals `done (--force overrode the gate: «reason»)`. `ctx merge --skip-gate`
  journalled only `ok`, with the override text scrolling past in the terminal;
  it now journals `ok (--skip-gate overrode the gate: …)` naming the checks that
  did not run. One `grep` for "overrode the gate" finds every override.

### Added

- **`optional: true` on a `cmd` check** — the opt-in for the absent-tool
  leniency that used to be everyone's default. For the one check whose tool some
  machines genuinely will not have. Documented with the warning it needs: the
  work's own failures can resemble an absent tool.
- **`ctx start --rebaseline «unit»`** — retake one unit's review baseline over
  the tree as it stands now, replacing what dispatch recorded. Repeatable,
  journalled, and loud on the terminal. Recorded findings are kept, so it cannot
  launder a deleted one. (A later wave stopped it re-sealing the contract:
  it refuses a contract that moved, and `--reseal` is that door.)
- **`status: running`** — a unit is `running` from dispatch until `--status done`
  closes it. `ctx status` renders it as `running (in flight)`, and a wave whose
  units are all `running` stops `/ctx:next` and the board from advising
  `/ctx:start` again: the wave is out, and the useful next act is a review.

### Documentation

- The verification reference said "Seven kinds" and listed seven; `verify.py` has
  eight. `test_first` is documented.
- `ctx findings` was listed among the commands with "no slash command by design",
  and `commands/findings.md` has shipped all along. `/ctx:findings` is in the
  slash-command table where it belongs.

## 0.8.0

Model selection stops following the seat and starts following the task; bug fixes
gain phases a machine can refuse on; and three claims this project had been
making without evidence are now asserted by tests. Three bugs were found on the
way that nothing in the plan had asked about.

### Added

- **Task-shaped model selection.** `model_for` now scores the unit it is about
  to dispatch — `budget_tokens`, `owns` breadth, `reads` breadth, `depends_on`,
  a judged verify check, a published interface, `kind: bug` — and maps the score
  to a tier. Weights and thresholds live in `ctx.yaml` under `complexity`, and
  the dispatch line prints the score with every input that produced it, so a
  wrong tier is diagnosable without re-running anything. An explicit `model:` in
  a unit's frontmatter still wins over every heuristic, and a model absent from
  `models.tiers` is never auto-escalated.
- **`models.tiers`** — an ordered, cheapest-first model list, the one place a
  model name may appear. `config.tier_up` walks it.
- **Package-driven reviewer tier.** A review package under
  `review.small_package_bytes` with zero scope violations draws a cheaper seat;
  over it, or with any violation, the floor holds.
- **Round escalation, off by default.** With
  `models.escalate_on_failed_round: true`, a failed fix round raises the runner
  one tier and the ledger records the tier moved from, to, and why. Default
  `false`: escalation is a cost hypothesis, and nobody pays for one until the
  telemetry can settle it.
- **`phases:` and `kind: bug`.** Any unit may declare ordered phases. `kind: bug`
  expands to a gated preset — `reproduce`, `locate`, `fix`, `guard` — where
  `reproduce` is satisfied only by a recorded **non-zero** exit, `fix` is locked
  until both `reproduce` and `locate` are recorded, and the fix must re-run the
  *same* command to zero. "No fix before a failing reproduction" is now something
  the code refuses on rather than something a document asks for. `ctx phase`
  drives it and surfaces the refusal verbatim.
- **`test_first` verify kind.** Fails when the implementation snapshot precedes
  every recorded failing run of the unit's test paths. A unit with no recorded
  runs fails rather than passing silently.
- **Telemetry that can answer "was the expensive model worth it".** Dispatch and
  review record `model` and `role`; `ctx telemetry` reports per-role spend.

### Fixed

- **`ctx init` silently dropped every new top-level default.** The generated
  `ctx.yaml` was built from a hand-enumerated key list, so any default added to
  the code never reached the file — a default that only lives in Python is a
  default nobody can find. Now built from `DEFAULTS` with overrides layered on
  top, deep-copied so a loaded config cannot mutate the module's own defaults.
- **Multi-line list items lost every line after the first.** A wrapped
  acceptance criterion or question was truncated at its first physical line
  everywhere it was read — the review package a reviewer grades against, the
  SessionStart briefing, and the done-gate. Fixed at the source in
  `frontmatter.list_items` and `spec`'s question readers, so all five consumers
  are repaired at once.
- **Every wave of two or more units deadlocked its own gate.** `subagent`-tier
  units share one working tree, and the scope check knew only the reviewed
  unit's `owns` — so a sibling's write to its *own declared path* was reported
  as a Critical violation the package called "not open to argument". Since
  `review` is a mechanical gate check and Criticals block, N units produced N-1
  false Criticals apiece. `review.wave_scope` now excludes paths declared by
  units still running in the same wave; a path nobody declared is still a
  violation, and the package says what it actually checked.

### Proven

Claims the project had been making in its own rationale, now asserted in
`tests/test_unproven_claims.py`:

- Two units in one wave, forced concurrent through a barrier, produce review
  packages whose owned-path attribution is byte-identical to the same unit built
  alone. This is what a commit range cannot do, and it had never been tested.
- A session killed mid-fix-loop — a real process, really terminated — resumes
  with its round number, open findings and unit under review intact.
- An escalated dispatch cannot widen what `trust.py` has accepted: the entire
  global trust tree is byte-identical across a real escalation.

### Fixed on Windows

- **The review package measured itself two different ways.** `build` counted
  the rendered text; `dispatch_stats` stat'd the file it was written to, and
  `Path.write_text` rewrites `\n` to `\r\n` on Windows. Those two numbers size
  the reviewer's model, so a package near `review.small_package_bytes` drew a
  dearer seat on Windows than on Linux for the same diff. The package is now
  written untranslated.
- **`ctx status` exited 1 because of an arrow.** Windows resolves piped stdout
  to cp1252, which cannot encode `→`, and `print` took the whole command down
  with it. `_echo` now degrades an unencodable glyph instead — the CLI prints
  `·`, `—` and `→` in 73 places, so this was a class rather than one character.

### Decisions

- Harness portability declined (ADR 0001): eight variants multiply the
  maintenance surface and no P0/P1 capability depends on any of them.
- Complexity bands map to `models.tiers` positionally, clamped (ADR 0002), so
  the tier list stays the one list every module consults.

## 0.7.0

The security and correctness release. Everything below came out of an adversarial
audit (`PRODUCTION-AUDIT.md`); the reproductions live in `tests/`.

### Breaking

- **`ctx start` no longer creates worktrees.** They are opt-in via
  `ctx start --worktree`. A worktree holds its branch exclusively, so creating
  one by default took away the tree you actually test in — `git checkout` of that
  branch in the main tree is refused while the worktree exists. `session`-tier
  units now run in the main tree unless you ask otherwise. `--no-worktree` is
  still accepted and does nothing.
- **Acceptances recorded before 0.7 are forgotten.** The trust store moved out of
  the repository (see below). Run `ctx trust` once to review and re-accept;
  `ctx doctor` names any leftover in-repo store.

### Security

- **A cloned ledger could commit to your branch.** `ctx init` accepted the
  *loaded* config's `verify` block while printing only the commands it had
  detected itself, so a `ctx.yaml` that arrived with a repository was trusted
  without ever being shown. Clone → `ctx init` → any tracked work → session end
  → the Stop hook ran it. `init` now accepts only the commands this run detected
  and printed.
- **`ctx doctor --verify` ran commands it reported as untrusted.** It shelled out
  with no trust check, then printed "these will not run until you review them"
  about the commands it had just run. It now skips them and says so.
- **`ctx init --verify-now` probed ledger-supplied candidates.** `verify_candidates`
  from the config were executed to see whether they passed, before being printed.
  They are now proposed for review and never run or auto-accepted.
- **The trust store lived inside the repository.** `.ctx/runtime/verify.trust` was
  protected only by a `.gitignore`, which `git add -f` defeats — so a hostile
  ledger could ship its own acceptance. It now lives under the global root, keyed
  by project path.

### Correctness

- **The done-gate no longer signs off on checks it never ran.** A verdict of
  ERROR beside a PASS collapsed to PASS, so outside a git repository the `owns`
  scope check silently vanished while the gate reported green.
- **`max_attempts` is a bound again.** After escalating, the gate cleared the
  counter and nothing consulted the recorded status, so the next session started
  over and blocked three more times, indefinitely. An edit re-arms it.
- **The scope check no longer accuses the ledger.** Every `ctx` command writes the
  journal, and those writes were counted as `owns` violations.
- **`ctx merge` refuses to merge into the wrong branch.** It merged into whatever
  HEAD happened to be, unnamed; on a detached HEAD it made the work unreachable
  and reported `done`. It now records the fork point, refuses a mismatch, refuses
  a detached HEAD, and names the branch it merged into.
- Ledger documents are written atomically, so an interrupted write can no longer
  truncate a unit file and revert its status to `pending`.
- `journal prune` no longer discards entries beyond the first 64 KiB of a day file.
- `CTX_GLOBAL_ROOT` is honoured whenever it is set, not only if it was set before
  the first import.

### Fixed

- Redaction destroyed ordinary file paths and rewrote `:` to `=` in prose, while
  letting basic-auth URLs and quoted JSON passwords through.
- Serialised ledger files lost data: a list nested in a list-item became a Python
  `repr` string — which turned the `symbol` interface-freeze check into an
  unconditional pass — and a multi-line value silently erased a whole frontmatter
  block.
- `bin/ctx.cmd` reported success for every failing command on Windows, and had no
  CI coverage. Both fixed.
- `plan-check` on large plans no longer takes minutes, and a broken plan reports a
  bounded list instead of thousands of lines.
- Every dispatched unit now names the model it should run on; an omitted model
  inherited the session's, which is the most expensive one available.
- Slash commands no longer abort before their own guidance is read. A non-zero
  exit from the `!` line killed the command, so `/ctx:verify`'s failure handling,
  `/ctx:merge`'s refusal branches and `/ctx:plan`'s next step were unreachable.

### Added

- **Adversarial code review that needs no commits.** `ctx review <unit>` diffs
  two content snapshots — taken automatically by `ctx start` and again when you
  review — and writes a single package file for the `reviewer` subagent. A path
  that changed outside the unit's `owns` is a Critical finding decided
  mechanically, with no model in the loop. Works in a project with no git
  repository at all, which a commit range cannot do.
- `ctx findings <unit>` records what a reviewer returned, and the new `review`
  verify kind refuses the done-gate while any critical or important finding is
  open. Findings persist in `.ctx/plans/<slug>/findings/<unit>.md`, so a gate
  running days later still knows the change was never signed off. There is no
  "acknowledged" status: a finding leaves `open` by being fixed, refuted with
  evidence, or parked with a ruling. Three rounds, then the loop stops and the
  remaining findings must be ruled on.
- `ctx snapshot <unit>` for work that reached a unit outside `ctx start`.
- `tests/test_git_invariant.py` — the git invariant as executable regression
  tests, including a hostile-ledger suite.
