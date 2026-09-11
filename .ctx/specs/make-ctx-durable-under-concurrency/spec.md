---
ctx_schema: 1
spec: make-ctx-durable-under-concurrency
status: ready
created: 2026-09-11
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Wave 4 of the v0.8.0 remediation roadmap: the durability and multi-user
dimension (report.md §3.3, roadmap items 17–22). Waves 1–3 made the gate
real, the CLI scriptable, and the artifact installable and governable. What
is left is the class of defect where the tool loses or corrupts work that
was already done — and the class where a second person on the same
repository silently collides with the first.

The shape is consistent across all six items and worth naming once, because
it is what makes the wave one wave rather than six errands: this codebase
already contains correct implementations of every primitive it needs. There
is an atomic writer in `frontmatter.Document.write`, an `O_EXCL` lock in
`state.locked` that holds under 8-way contention, and a `plan_slug` in
`worktree.branch_for`. The defects are the call sites that do not use them.
`journal.prune` truncates months of archive with `write_text` three files
away from the atomic writer; the unit read-modify-write window spans a 240s
gate with the lock unused; `path_for` omits the slug its sibling includes.
So this wave is mostly routing, not invention — and that is why it can be
held to not changing a single observable behaviour except the ones named
below.

Two items are different in kind and carry the real risk. Per-author journals
and ADR ids change files that are already committed in users' trees, so they
need a migration answer rather than only an implementation. And
`hooks.main`'s `except SystemExit: raise` is the one item here that is not
about a crash window at all: it is a live failure mode that fires on an
ordinary version skew between two teammates.

## Acceptance criteria

1. `ctx/atomic.py` exposes `write_text(path, text)` — temp file in the
   destination's directory, `fsync`, `os.replace` — and a test asserts that a
   write interrupted after the temp file is written but before the replace
   leaves the original file byte-identical, for a target that already had
   content.
2. `journal.prune` routes the archive write through it. A test that fails the
   write partway through a prune of a month whose day files were already
   folded by an earlier run finds the pre-existing archive intact, with every
   line it held before the run.
3. `journal.write_digest`, the `plan.json` graph write, snapshot manifests,
   and both `migrate` writes (`ctx.yaml` and `plan.json`) route through it. A
   test enumerates the durable committed writers and asserts none of them
   calls `Path.write_text` — so a seventh site added later fails this test
   rather than shipping.
4. An interrupted `migrate` leaves `ctx.yaml` parseable and `migrate --check`
   still reports the file's true schema, rather than `_CONFIG_LINE` matching
   against a half-written file.
5. The unit read-modify-write window is closed: `ctx unit --status done`
   re-reads the unit file after the gate returns and before it writes, under
   a per-plan lock. A test writes `base_branch` into the unit file while a
   slow gate is running and asserts the value survives the `done` transition
   — today it is erased, which disarms the merge-target guard.
6. The findings and phases ledgers take the same per-plan lock for their
   read-modify-write, and a concurrent reviewer write and implementer write
   both survive. `telemetry._rotate` runs under a lock, and 20 concurrent
   records across a rotate all survive — today 0 of 20 do.
7. `worktree.path_for` takes the plan slug and returns a path containing it.
   `ctx worktree remove 01-api --force` run against plan-b does not touch
   plan-a's worktree, asserted by a test that creates both and removes one.
8. `worktree.remove` refuses a tree whose checked-out branch is not the one
   it resolved for this plan and unit, naming both branches, rather than
   removing it.
9. `migrate.discover()` reports `findings/` and `phases/` ledgers with their
   true `ctx_schema`, so `ctx migrate --check` on a tree holding an
   out-of-date findings ledger exits non-zero instead of reporting clean.
10. Journal entries are written to a per-author file, so two people working
    the same day do not conflict. The author key is derived from git config
    with a documented fallback, and `journal.tail`, `write_digest` and
    `prune` read every author's file in one merged, time-ordered stream.
11. Existing `journal/YYYY-MM-DD.md` day files are not rewritten. A test
    seeds a tree with both shapes and asserts `tail` returns one merged,
    time-ordered stream across them, and that the legacy file is unmodified
    afterwards.
12. `DIGEST.md` is untracked in this repository (`git rm --cached` plus a
    `.ctx/.gitignore` line) and regenerates on the next hook fire. For any
    other project, `ctx doctor` reports a tracked `DIGEST.md` as a conflict
    source and prints the two commands; no code path untracks a file in
    someone else's repository on its own.
13. Two ADR files cannot share an id: `ctx doctor` and `ctx ci` fail and name
    both colliding files. The zero-padded `0001-` sequence is unchanged. A
    test writes two ADRs with the same number under different slugs and
    asserts the non-zero exit and both filenames in the message.
14. `hooks.main` no longer re-raises `SystemExit`. A ledger whose
    `schema:` is above the running plugin's produces one line of notice and
    exit 0 from every hook event, not a traceback and a non-zero exit on
    every tool call. A test drives a real hook invocation against a
    future-schema ledger and asserts exit 0 with the notice on stderr.
15. The CI suite floor moves with the suite: `SUITE_FLOOR` in ci.yml and
    `REQUIRED_FLOOR` in tests/test_ci_floor.py both rise to just under the
    count this wave lands with, and the behavioural test that proves the
    floor still passes at the new value.
16. `hooks.on_stop` calls `config.gate_override(layout, config, "stop")`
    instead of its own inline `CTX_GATE` environment test at `hooks.py:219`,
    so the Stop-hook bypass is journalled and policy-refusable like the CLI
    site. The two tests in `tests/test_policy.py` that document this as
    pending are rewritten to drive `on_stop` itself.
17. The full suite, `ctx doctor` and `ctx ci` all pass at the end, and the
    suite count is strictly greater than the 1027 this wave starts from.

## Out of scope

- `--json` on the CLI verbs (roadmap item 9, carried from wave 2). It is a
  presentation change with no durability content and belongs with the wave 5
  CLI-contract work.
- Coverage thresholds and `ruff` (carried from wave 2 via wave 3). Both need
  dev-dependency config decisions and `ruff` over a 3185-line `cli.py` is its
  own backlog, not a line in a durability wave.
- The wave 5 extractions from `cli.py`, and the README split that the wave 3
  docs unit already partly pre-empted.
- Granting execution from `trust.lock`. Wave 3 deliberately left the lockfile
  granting no execution; changing that is only defensible once branch
  protection requires Code Owner review, which is not yet enabled. Recorded
  as a note, not built here.
- A logging subsystem (report §3.4, P1). Real, but it is an operations
  concern rather than a durability one and it would touch every module in the
  tree.
- The stale-lock reclaim double-holder path (`state.py:91-107`). Code-path
  only, never reproduced under contention; it needs a reproduction before it
  needs a fix.

## Notes

Positive control applies to every criterion here: each test must be shown
failing against the current code before its fix lands, because five of the
six items are invisible until a crash or a race, and a test for an invisible
defect is exactly the kind that passes vacuously. The `journal.prune` and
`telemetry._rotate` tests are the ones most likely to be fail-green — both
need a real interruption, not a mocked one.

`state.json` currently points at this spec with no spec directory on disk: a
previous session opened the spec, dropped to L0, and left the pointer
dangling. Creating the spec here resolves that rather than working around it.
