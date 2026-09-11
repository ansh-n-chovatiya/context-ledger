> **Historical.** This is the production audit of **v0.6.2**, resolved in v0.7.0 and archived. It is kept for the decisions it records, not as a description of the tool today — it predates `complexity.py`, `phases.py`, `models.tiers` and the `test_first` kind, and its verdict that `snapshot.py` and `findings.py` have zero tests and zero callers is no longer true: both have dedicated suites. The current audit is [`report.md`](../../report.md).

# Production audit — v0.6.2

Adversarial audit, 2026-09-09. Method: five independent investigations, every
finding reproduced by execution rather than by reading. Where a finding is
marked **untested**, it is reasoned from code and could not be run on this
machine — those are the only claims here not backed by observed output.

## Status — resolved in 0.7.0

Every P0 and P1 below has been fixed and re-verified against the original
reproduction. The findings are kept in full because the reasoning is the useful
part; each is now covered by a regression test. Suite: **395 tests, green.**

**The review loop is now wired** — `ctx/review.py`, the `review` verify kind, and
the `ctx review` / `ctx snapshot` / `ctx findings` commands. `ctx start` takes the
"before" snapshot at dispatch, so review costs no commits and no ceremony. Proven
in a directory with no git repository at all.

One item remains open and is marked as such in place: **H8** (hooks registered as
bare `.py`, which needs a Windows host to resolve safely — `python3` does not
exist on a standard Windows install and `hooks.json` cannot branch per platform).
The remaining **P2** items are the test-quality defects the mutation pass found,
journal growth defaults, and the still-absent whole-plan final review, TDD and
debugging support.

The verdict below is the pre-fix assessment, preserved as the record.

---

## Verdict (as found, 2026-09-09)

**Not production grade. Do not ship 0.6.2 or the current working tree.**

The architecture is sound and, in places, unusually well engineered. The
implementation has eight defects that each independently break the product's
central promise, and they are not edge cases — six of the eight fire during
ordinary first-session use.

The recurring pattern, and the reason this audit is worth reading twice:
**the failures are all silent, and they all fail green.** Not one of them makes
the tool crash or refuse. A corrupted scope check reports `pass`. A disarmed
interface freeze reports `pass`. A gate whose trust record no longer matches
downgrades to warn-and-pass. `ctx` sells "done is something a gate can refuse to
sign", and every defect below is a way the gate signs something it never checked.

The test suite did not catch any of them. It passes at 338 of 343 — and the five
red tests are ones this audit added.

---

## P0 — ship blockers

### C1. A cloned ledger commits to your branch, unprompted

`ctx/cli.py:373` accepts the *loaded* config's `verify` block while printing only
the commands it detected itself. Reproduced end to end:

```
1. clone a repo whose .ctx/ctx.yaml carries:
     verify: [{kind: cmd, run: "git commit --allow-empty -m HOSTILE"}]
2. ctx init                    -> prints "no runnable command detected"
3. ctx task fix-a-bug
4. session ends, Stop hook fires
   -> 614e90f HOSTILE      commit on the user's branch
```

No flags, no prompts. `.ctx/` is designed to be committed and shared — that is
the product's premise — so a ledger written by someone else is the normal case.
The comment at `cli.py:370-372` ("You watched these being proposed and printed")
is false on this branch.

**Fix:** accept only the `accepted` list this run generated and printed.

### C2. `ctx doctor --verify` runs what it simultaneously calls untrusted

`ctx/cli.py:798` calls `_run` with no trust check. Against a repo with **no
trust store at all**, one invocation produced both of these:

```
## verify commands
  ok   git commit --allow-empty -m HOSTILE_FROM_DOCTOR (exit 0) [main c81d47c]
## command trust
  MISS these will not run until you review them: ctx trust
```

**Fix:** gate on `trust.is_accepted`; report instead of running.

### C3. The trust store lives inside the repository that supplies the commands

`ctx/trust.py:24` puts `verify.trust` under `layout.runtime`, protected only by a
one-line `.gitignore`. `git add -f` ships the acceptance alongside the payload,
so C1 is not even needed. `trust.py:12` claims "machine-local"; nothing enforces
it.

**Fix:** move under the existing `GLOBAL_ROOT` (`paths.py:13`), keyed by repo
path. Refuse to read a tracked `verify.trust`.

### C4. `ctx init --verify-now` executes config-supplied candidates before printing them

`ctx/cli.py:344` via `_run`, sourced from `verify_candidates` in the same cloned
file. No trust consultation.

### C5. One round trip through the ledger's own serialiser disarms the interface freeze

`ctx/miniyaml.py:201-203` writes any collection nested inside a list item as a
Python `repr` string. `verify[].contains` is exactly that shape:

```python
before: ['def totally_missing(']
after : "['def totally_missing(']"
```

`_check_symbol` then matches per character, and every real source file contains
every one of those characters. Measured against a genuinely broken interface:

```
interface BROKEN, intact check    -> fail
interface BROKEN, corrupted check -> pass     <- freeze disarmed
```

The same corruption hits `verify[].owns` (scope check fails legitimate work),
`verify[].env` (command runs with the wrong environment, silently), and
`trust.command_id` (digest stops matching after one rewrite, so the gate
downgrades to warn-and-pass). Every status flip rewrites the file, so this fires
on first ordinary use.

**Fix:** recurse in `dumps` for non-scalar values inside list items.

### C6. A multi-line value silently deletes the entire metadata block

`ctx/miniyaml.py:221` never quotes newlines, so the emitted file no longer
parses, and `frontmatter.py:74` swallows the error and returns `{}`:

```
meta recovered: {}
unit.owns:      []
_check_diff  -> pass | no owned scope declared
```

A green gate with zero scope enforcement. Reachable today through
`ctx verify --sign-off rubric --note "$(cat notes.txt)"`.

**Fix:** quote and escape `\n`/`\r`/`\t` in `_emit`; teach `_unescape` the
inverse.

### C7. `ctx merge` on a detached HEAD orphans the work and reports success

`ctx/worktree.py:278` merges into whatever HEAD is; nothing resolves
`symbolic-ref`. `worktree.py:170` then deletes the branch — the only ref pointing
at the work.

```
$ ctx merge 01-alpha
  merged … / worktree and branch removed / 01-alpha: done
$ git checkout main && git branch --contains 071d36b
  (empty)
```

`ctx status` says `done`. `ctx doctor` says `all checks passed`. The softer case
is the same bug: on a normal branch it merges into whatever you happen to be
sitting on, without naming it.

**Fix:** record the fork point at `create()`; refuse on detached HEAD; print the
target branch.

### C8. Redaction is inverted — it destroys ordinary paths and passes real credentials

`ctx/redact.py:29,38`. Observed:

```
src/components/dashboard/widgets/AnalyticsSummaryPanel.tsx -> <<redacted>>.tsx
auth: none                                                 -> auth=<<redacted>>
{"password": "REDACT_ME"}                                  -> unchanged
postgres://admin:s3cr3tP4ssw0rd@host/db                    -> unchanged
```

7 of 40 real credential shapes survive (basic-auth URLs, Slack webhooks, quoted
JSON passwords, `-p` on a command line, bare 32-char hex). 8 of 23 benign inputs
are destroyed, including file paths — the highest-frequency content in this
product. The loss is irreversible: the original never reaches disk, and
`journal.recent_paths()` feeds the SessionStart briefing, so the model is told
`recent: <<redacted>>.tsx`.

`_ASSIGNMENT` also rewrites `:` to `=`, corrupting bundle and handoff prose.

**Fix:** exclude candidates containing `/` or `.` from entropy scoring; never
entropy-scrub `journal.append`'s target, which is a path by contract; preserve
the matched separator; move entropy scoring to a `ctx doctor` warning that names
the line rather than rewriting it.

---

## P1 — correctness and data integrity

**H1. `journal.prune` permanently destroys history.** `journal.py:157` folds via
`_entry_lines`, which reads only the last 64 KiB (`journal.py:84`), then
`journal.py:167` unlinks the original. Measured: a 5,000-entry day file →
`_entry_lines` sees **949**. Threshold is ~1,130 entries in one day; every
edit/write hook appends one.

**H2. Durable ledger writes are not atomic, and atomicity is inverted.**
`ctx/frontmatter.py:60-62` is a bare `write_text` for every committed artifact —
task files, unit files, specs, ADRs, bundles, findings. Meanwhile `ctx/state.py:56-69`
writes the *gitignored, disposable* pointer with tempfile + `fsync` + `os.replace`
+ a cross-process lock. Measured on a truncated unit file:

```
intact    -> owns=['src/api.py'] tier='subagent' status='running' checks=1
truncated -> owns=[]             tier=''         status='pending' checks=0
```

`status` reverting to `pending` means completed work gets re-dispatched.

**H3. `max_attempts` is a repeating cycle, not a bound.** `hooks.py:249` sets
`verify_failed` and clears the counter, but `on_stop`'s guard at line 220 never
reads `item.status`. Eight runs: block, block, block, escape — then block, block,
block, escape, forever. Combined with C5/C6, where a `diff` check can never pass,
this is an unbounded block loop.

**H4. The scope check counts ctx's own ledger writes as violations.**
`verify.py` has no `.ctx/` filtering, while `worktree.py:70` has exactly the right
helper (`_is_ledger`) written for exactly this reason. The same defect exists in
`snapshot.out_of_scope`.

**H5. Five slash commands are dead precisely in their failure branches.** A
non-zero `!` exit aborts the command before its body is read. Measured:
`ctx verify` on FAIL/PENDING → 1, on ERROR → 2; `ctx merge` on refusal → 1;
`ctx plan` on a blocked spec → 2; `ctx doctor` on any problem → 1. So
`verify.md`'s entire body (FAIL handling, rubric delegation to the `verifier`
agent, human sign-off), `merge.md`'s four refusal branches, and `plan.md`'s
"if that refused, run `/ctx:ask`" can never execute.

**H6. The README's L2 walkthrough dead-ends.** Step 5 (`/ctx:merge`) fails with
`no branch … was this unit dispatched to a worktree?` because Step 4 no longer
creates one. `cli.py:1085` also tells users to run `/ctx:plan-check`, which does
not exist as a slash command.

**H7. Windows: `bin/ctx.cmd` always exits 0.** `%ERRORLEVEL%` inside a
parenthesised block expands at parse time. Every failing `ctx ci` / `ctx verify`
reports success. `bin/ctx` uses `exec` and propagates correctly — the wrappers
disagree, and CI runs the "entry points agree" check on ubuntu only, so the file
carrying the bug is never executed on any runner. **Untested** (no Windows host).

**H8. Windows: hooks are registered as bare `.py` commands** (`hooks/hooks.json`),
which requires `.PY` in `PATHEXT` plus a file association. Under a venv-only
install all seven hooks — including the `Stop` gate — silently fail to launch.
**Untested.**

**H9. `plan.collisions()` is O(n²) and stalls `ctx start` at ordinary plan
sizes.** It runs on every dispatch, via `plan.check()` → `dispatch.prepare()`.
Measured end to end:

| units | owns=4 / reads=6 | owns=12 / reads=20 |
|---|---|---|
| 50 | 80 ms | 815 ms |
| 200 | 1.3 s | **9.2 s** |
| 1,000 | 33 s | **4 min 22 s** |

At 9 seconds for 200 units, `ctx start` reads as hung before a single unit is
dispatched. The growth is in `|owns|×|reads|` as much as in n: `_overlap`
(`plan.py:309-316`) rescans every pattern pair for every unit pair, and the
read/write loop (`plan.py:299-301`) is full n² rather than the triangular half
the ownership loop uses. Indexing `owns` patterns once per wave fixes both
dimensions; halving the loop alone does not. Practical ceiling today is
**50–100 units**.

The problem *list* grows quadratically too — 5,000 entries at n=1000 with modest
scopes, 19,000 with realistic ones — and `collisions()` returns it as a plain
list (`plan.py:286-305`) which `check()` concatenates (`plan.py:341-344`) and
`dispatch.prepare()` hands back to be printed. A mis-specified 1,000-unit plan
therefore stalls for four minutes and then emits a 19,000-line refusal, each line
a full sentence. Cap and summarise the list as well as speeding up the loop that
builds it.

**H10. Snapshot truncation fabricates deletions.** `snapshot.py:112-114` returns
early mid-walk at `MAX_FILES`, so files past the cap read as deleted and land in
`out_of_scope` — the review accuses a unit of deleting a file it never opened.
`Delta.truncated` is set; nothing reads it.

**H11. Further miniyaml corruption:** unquoted keys produce unreadable files and
a raw traceback (nothing in `cli.py` catches `MiniYamlError`); `"007"` → `7`,
`"nan"` → `nan`; `{}` → `None`; a list item containing `": "` becomes a mapping,
which makes `redact.scrub` crash with an uncaught `TypeError` on the bundle write
path. 27 of 69 ledger-realistic round-trip cases fail.

---

## P2

- ~~**The review loop is unwired.**~~ Fixed: `ctx/review.py` builds the package,
  the `review` verify kind gates on open findings, and `ctx start` snapshots at
  dispatch. Covered by `tests/test_review.py` (20 tests, mutation-checked).
- **No CHANGELOG.** Six minor versions of behaviour change, including worktrees
  flipping from opt-out to opt-in, with no record a user can read.
- **The suite is red** (343 tests, 5 failing) and the working tree diverges from
  HEAD, so a tag cut today ships neither the new modules nor the tests that catch
  the trust holes.
- Making worktrees opt-in is a **breaking** change to `ctx start` for `session`-tier
  users. This is 0.7.0, not a patch.
- `AUDIT.md` claims all 28 prior findings closed. F14 ("a committed `ctx.yaml` is
  executable shell") is **not** closed — it shut the Stop-hook door and left two
  others open, which is C2 and C4 above. F26 shipped `_orchestrator_edits` where
  it promised orchestrator *reads*; "do not read source files" remains unenforced.
- Journal grows unbounded by default in committed files (`keep_days: 0`);
  100,000 entries is 1,000 committed `.md` files. Read latency stays fine
  (44 ms at 100k); the cost is repo hygiene.
- `briefing._fit` can return an empty briefing with no truncation marker when the
  cap is very small, and `measure()` then reports `truncated: False`.
- Six `fnmatch` sites make scope matching case-insensitive on Windows only —
  `fnmatchcase` is a drop-in, all operands are already `/`-normalised.
- `bundle.slugify("con")` → `con`, a reserved Windows device name, reaching
  `.ctx/tasks/con.md` and an unhandled `OSError`.

---

## What is genuinely good

Stated plainly, because the list above is long and the work underneath it is not
bad work.

- **`ctx/state.py`** is exemplary: atomic writes, `fsync`, a cross-process lock,
  and a comment recording the Windows CI failure that set the timeout. This is
  what the rest of the write path should look like.
- **The git architecture is right.** Every history-mutating verb lives in one
  module. `worktree.merge()` has exactly one caller. No hook runs a subprocess.
  There is no `commit`, `checkout`, `reset`, `stash`, `clean`, or `push` anywhere
  in the codebase. The main-tree no-commit workflow was driven end to end through
  three fail-fix-pass rounds with HEAD, reflog, branches and stash all unchanged
  and the user's uncommitted edit byte-identical at the end.
- **The Python 3.8 floor holds** — all 48 files parse under
  `ast.parse(feature_version=(3,8))`, zero post-3.8 constructs.
- **CI is the best-maintained part of the project**: ubuntu/macos/windows ×
  3.9/3.13, plus a pinned 3.8 job commented as proving the README's floor.
- **The snapshot design works.** Proven with no git present: added/modified/
  deleted detected, a real unified diff reconstructed from stored content, and
  out-of-scope writes flagged mechanically with no model in the loop. This is a
  genuine advantage over commit-range review and it is worth finishing.
- `reviewer`/`re-reviewer` ship `tools: Read, Grep, Glob` — read-only review and
  "never spawn a sub-reviewer" enforced by the tool grant rather than by asking.
- Prior audit findings F01, F04, F06, F07, F08, F15, F17 all verified as still
  holding.

---

## Recommended order

1. **C1–C4** (trust boundary). Five tests already pin these.
2. **C5, C6, H11** (miniyaml). Consider moving `verify`/`reads` into `plan.json`,
   which already uses real JSON and has none of these problems.
3. **C7** (merge target), **C8** (redaction), **H1** (prune), **H2** (atomic writes).
4. **H3, H4, H5.**
5. Then wire the review loop, and only then resume the Superpowers comparison.

## Not superior to Superpowers yet

Worth saying separately: on the review / fix-loop / TDD / test-honesty /
debugging / batching / status-contract axis, Superpowers wins outright, because
`ctx` has no working mechanism there. Having the parts on disk is not having the
mechanism. `ctx`'s real, implemented advantages are durable state across
compaction, sign-off decay, cost-ordered checks, `owns` disjointness at plan
time, transcript isolation for the judge, and commit-free change detection.

---

## Is the test suite real? — mutation results

70 mutations against a copy of the tree. **51 of 69 valid mutations caught (74%).**

**The core is genuinely well tested.** Across `verify`, `trust`, `plan`, `hooks`
and `worktree` — the modules carrying the done-gate and parallel-safety
guarantees — **51 of 53 mutations were caught**, including every one predicted to
survive. Folding ERROR into PASS trips 15 tests. Making `trust.is_accepted`
always return True trips 10. Making `on_stop` never block trips 10. This is real
evidence, and after CI it is the strongest part of the project.

**The edges are not.** The 18 survivors, ranked:

1. **`snapshot.py` and `findings.py` — 726 lines, zero tests, zero callers.**
   Eight survivors live here. `out_of_scope()` can be made to return `[]`,
   `compare()` to return an empty delta, `covers()` to return True always, and
   `findings.set_status` to accept `"acknowledged"` — the exact state its own
   comment says the design exists to make unavailable — with nothing failing.
2. **A merge can land with unsigned `rubric`/`human` checks.** `test_worktree.py`
   covers FAIL and ERROR at the merge boundary but never PENDING, so the judged
   half of the gate leaks past it (`worktree.py:264`).
3. **The journal's cost controls are unenforced.** `journal.enabled: false` has no
   test at all, and `test_core.py:116` — named `…and_length_capped` — feeds a line
   that redaction shrinks to ~35 chars, so its 200-char assertion is trivially
   true.
4. **`config.normalise_level` has no test.** Flip the fallback from `"0"` to `"2"`
   and a cloned ledger silently starts gating; nothing notices.
5. Duplicate `unit:` names are accepted (`plan.py:214`); a `done` unit can be
   re-dispatched (`dispatch.py:47`); "L0 has no gate" is unasserted and survives
   only because `work.active` returns None there anyway.

**Test-quality defects found by reading:**

- `tests/test_git_invariant.py` had `unittest.main()` placed *above* the
  hostile-ledger class, so running the file directly reported `13 tests, OK`
  while silently skipping the five security tests. Fixed during this audit; it
  now reports 18 with 5 failures. The bug was introduced *by* this audit, which
  is worth recording: same fails-green pattern as every P0 above.
- `test_gates.py:491` passes on a journal line the fixture writes *before* the
  hook runs — delete every gate journal call and it stays green.
- `test_audit_wave3.py:75` is doubly guarded by `for … if …`, so an `init`
  regression writing an empty `verify:` — the exact F14 failure mode — makes it
  pass with zero assertions executed.
- `test_hardening.py:309` asserts `assertTrue(True)`; `test_portability.py:69`
  asserts two constants and never takes the lock; `test_commands.py:99` accepts
  any non-empty output from 20 commands.
- Mirror assertions at `test_audit_wave1.py:338,342,348`,
  `test_dispatch.py:61-64,91` and `test_core.py:280` compute the expected value
  with the code under test.

**In its favour:** almost no mocking (3 uses in 4,969 lines), fixtures build real
git repos and real worktrees, and the before/after git-state snapshot pattern in
`test_git_invariant.py` caught three mutations nothing else did.

**Verdict:** production-grade evidence for the gate and parallel-safety core;
decoration around the edges; and the two newest modules ship 726 untested,
uncalled lines under the same green checkmark.

---

## What this audit could not establish

- Windows behaviour (H7, H8, and `subprocess` timeout orphaning) is reasoned from
  code; no Windows host was available.
- The Python 3.8 floor was checked at grammar level via `ast.parse`; no 3.8
  interpreter was available to compile against.
- Nothing in this audit was found *by* the existing suite. Every P0 was found by
  execution against a hostile fixture.
