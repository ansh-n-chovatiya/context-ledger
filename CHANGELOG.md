# Changelog

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
