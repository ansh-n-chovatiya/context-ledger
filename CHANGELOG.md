# Changelog

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
