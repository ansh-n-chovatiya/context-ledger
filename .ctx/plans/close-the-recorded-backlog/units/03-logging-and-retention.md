---
ctx_schema: 1
unit: 03-logging-and-retention
plan: close-the-recorded-backlog
tier: subagent
depends_on: []
owns:
  - ctx/log.py
  - ctx/telemetry.py
  - ctx/journal.py
  - ctx/config.py
  - ctx/hooks.py
  - tests/test_logging.py
  - tests/test_journal_retention.py
reads:
  - path: ctx/paths.py
    symbols:
      - Layout
forbid:
  - ctx/commands.py
  - ctx/cli.py
  - ctx/verify.py
  - ctx/lock.py
budget_tokens: 70000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Give `ctx` a way to say that something failed, and stop `keep_days: 0` meaning
the committed journal grows for ever.

## Acceptance criteria

**Logging (report §3.4, P1).** There are zero uses of `logging` across `ctx/`.
`telemetry.record` and `journal.append` swallow every exception by design, so a
read-only mount, a full disk and a permissions error are indistinguishable from
success. There is no way to raise verbosity for a support case.

1. `ctx/log.py` exists and the swallow points route their failures through it.
   Start with `telemetry.record`, `journal.append`, `journal.write_digest` and
   `hooks`' error path; you own those files.
2. **The never-raise guarantee is unchanged.** These paths still must not break
   a session — they log instead of discarding. A test asserts a read-only
   runtime directory leaves `record` and `append` returning normally *and* that
   the failure is now visible somewhere.
3. **Off or minimal by default, and this is not negotiable.** This tool's pitch
   is a small always-on footprint; it must not start writing logs nobody asked
   for. Controlled by an environment variable (and a config key if you judge it
   worth one). A test asserts the default state produces no new file and no
   output on a normal run.
4. Logging cannot itself break a session. If the log destination is unwritable,
   the logger fails silently — the one place where swallowing is correct,
   because the alternative is a logger that breaks the thing it observes.
5. The default and the way to raise it are documented in `docs/operations.md`.
   That file is **not** in your `owns` — report the text you want added and it
   will be picked up.

**Journal retention.** `journal.keep_days` defaults to 0, `journal.prune` reads
it, and `ctx prune` advises setting it. The audit records that 0 lets the
committed journal grow unbounded.

6. Decide what 0 means and make code and documentation agree. Two defensible
   answers: 0 means "never prune" (then say so everywhere, and the growth is a
   documented choice), or 0 means "use a shipped default" (then pick it). State
   which you chose and why.
7. **Changing the shipped default is in scope** if that is the right answer.
   The journal is committed and shared, so unbounded growth is a real cost — but
   a default that silently deletes history is worse. Whatever you choose, a
   destructive default must be loud.
8. `ctx prune`'s advisory text lives in `commands.py`, which you do **not** own.
   If it needs to change, report the exact wording; unit 05 owns that file next.

**Both.**
9. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. Do not move any floor.
10. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the default logging
state and how to raise it · your `keep_days: 0` decision and why · the exact
text unit 05 must put in `commands.py` and the docs.
