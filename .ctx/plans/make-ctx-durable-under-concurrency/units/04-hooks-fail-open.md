---
ctx_schema: 1
unit: 04-hooks-fail-open
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 02-plan-scoped-lock
owns:
  - ctx/hooks.py
  - tests/test_hooks_fail_open.py
  - tests/test_policy.py
reads:
  - path: ctx/config.py
    symbols:
      - load
      - gate_override
      - SCHEMA
  - path: ctx/state.py
    symbols:
      - load
forbid:
  - ctx/config.py
  - ctx/cli.py
  - ctx/journal.py
  - ctx/worktree.py
budget_tokens: 45000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Stop a teammate's newer ledger schema from putting a raw traceback and a non-zero
exit on every single tool call, and route the Stop hook's `CTX_GATE` test through
the one shared definition wave 3 built for it.

## Interfaces
Consumes `config.gate_override(layout, config, site)` — already built and tested
in wave 3. It returns an object with `.disabled`, `.recorded`, `.value`, `.lock`
and `.note`, and it performs the journalling itself. Produces nothing other units
consume.

## Acceptance criteria

**The fail-open carve-out (report §3.3, roadmap item 22).** `hooks.main` wraps
everything in `except BaseException` to fail open, then carves out
`except SystemExit: raise` immediately above it (`hooks.py:58`) — and
`config.load` raises `SystemExit` on a schema above the plugin's
(`config.py:169`). The single condition deliberately excluded from fail-open is
the most likely real-world one.

1. A hook invocation against a ledger whose `schema:` is above the running
   plugin's exits 0 and writes one line of notice to stderr naming the ledger's
   schema, the plugin's, and that the plugin needs upgrading. No traceback on any
   stream.
2. Positive control: run the same invocation against the current code first and
   quote the non-zero exit and the traceback. Then quote the fixed run. Do this
   for at least two distinct hook events, including `SessionStart` — a defect
   that fires on every tool call must be tested on more than one of them.
3. A genuine `SystemExit` raised by a *handler* — not by `config.load` — still
   propagates, or if you decide it should not, say so and pin the decision with a
   test. Do not silently widen the catch to swallow every exit in the module.
4. The notice is not repeated per event within a session if you can avoid it
   cheaply; if you cannot, say so rather than building a cache that outlives its
   value. One line per event is acceptable. A traceback is not.
5. The existing fail-open behaviour is unchanged for every other exception type,
   pinned by the tests already in the suite.

**The Stop-hook `CTX_GATE` site (carried follow-up).** `hooks.py:219` still does
its own `os.environ.get("CTX_GATE", "").lower() in (...)` test and returns `""`
silently.

6. `hooks.on_stop` calls `config.gate_override(layout, config, "stop")` and
   obeys it, so the Stop-hook bypass is journalled and a locked
   `gate.allow_override: false` policy refuses it there exactly as it does at the
   CLI site.
7. The two tests in `tests/test_policy.py` that currently drive
   `config_mod.gate_override(..., "stop")` as a stand-in —
   `test_the_stop_hook_site_journals_the_bypass` and
   `test_every_occurrence_is_recorded_not_just_the_first`, in
   `TestTheOffSwitchLeavesARecord`, whose docstring says explicitly that this is
   not that unit's file to edit — are rewritten to drive `hooks.on_stop` itself.
   The class docstring is updated to describe what now happens rather than what
   was pending.
8. A test asserts all five spellings (`off`, `0`, `false`, `disabled`, `OFF`)
   disable the gate *through `on_stop`*, and that `on`/`1`/`true`/empty do not.
9. Journalling the bypass cannot change what the gate does: assert that a
   journal write failure (read-only journal directory) still leaves `on_stop`
   returning the same verdict.

**Both.**
10. `python3 -m unittest discover -s tests -q` passes; suite count strictly up.
11. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs for criterion 2 on both hook events · your decision on criterion
3 and its test · any behaviour in `tests/test_policy.py` you had to change beyond
the two named tests.
