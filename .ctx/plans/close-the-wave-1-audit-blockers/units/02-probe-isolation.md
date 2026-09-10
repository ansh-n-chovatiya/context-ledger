---
ctx_schema: 1
unit: 02-probe-isolation
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on: []
owns:
  - ctx/cli.py
  - tests/test_audit_probe_isolation.py
reads:
  - path: ctx/trust.py
    symbols:
      - load
      - is_accepted
      - command_id
  - path: tests/support.py
  - path: report.md
forbid:
  - ctx/verify.py
  - ctx/hooks.py
  - ctx/review.py
  - ctx/snapshot.py
budget_tokens: 55000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 1
---

## Objective

Close the audit's only remote-code-execution finding.

`_availability(command)` in `ctx/cli.py` decides whether a verify command can run.
For the interpreter forms it builds a probe string and executes it:

```
probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(<module>) else 1)"
subprocess.run([parts[0], "-c", probe], capture_output=True, timeout=20)
```

The module name comes from the untrusted `verify.cmd.run` string in a **committed**
`.ctx/ctx.yaml`. Two facts combine into arbitrary code execution:

- `importlib.util.find_spec` on a *dotted* name imports the parent package to
  find its `__path__`. `find_spec("evilpkg.sub")` runs `evilpkg/__init__.py`.
- `python -c` places the current working directory at the front of `sys.path`,
  and `subprocess.run` here inherits the caller's cwd — the cloned repo.

So a hostile repo shipping `run: python3 -m evilpkg.sub` plus an
`evilpkg/__init__.py` executes that `__init__` the moment anyone runs `ctx doctor`
— the first thing the docs tell a new user to do. Reached from `cmd_init`,
`cmd_doctor` and `cmd_ci`.

The bitter detail: the same run prints `MISS 1 of 1 command(s) not accepted on
this machine`. The trust store correctly refuses to *run* the command while the
probe in front of it already did. `AUDIT.md:509` records this probe as the
remediation for an earlier finding — the fix introduced the hole.

## Constraints

- Do **not** weaken `_availability`'s reason strings. They are user-facing and the
  docstring says they must be true; `ctx doctor` output is asserted by existing
  tests.
- Do **not** change the `cmd` trust gate in `ctx/verify.py` — that is a different
  unit's file and the audit found the gate sound.
- The probe must keep distinguishing "interpreter present but module absent" from
  "binary not on PATH". A plain PATH check is not sufficient and reintroduces the
  finding this probe was originally added to fix.

Any of these is an acceptable mechanism, in rough order of preference:

1. Replace the probe with a non-importing lookup — `importlib.machinery.PathFinder`
   over an explicit `sys.path` that excludes the repo, resolving only the
   top-level name so no parent package is ever imported.
2. Run the probe with `python -I` (isolated: no cwd on `sys.path`, no user site,
   no `PYTHON*` env) **and** a `cwd` outside the repository.
3. Gate the probe behind `trust.is_accepted` so an unaccepted command is never
   probed at all.

Whichever you choose, the module name must be treated as data: reject or refuse to
probe a dotted name rather than resolving its parent.

## Interfaces

- `_availability(command)` → `(bool, str)` — signature unchanged. Later units call
  it only through `_runnable(command)`.
- `_runnable(command)` → `bool` — unchanged.
- Callers that must keep working exactly as they do: `cmd_init`, `cmd_doctor`,
  `cmd_ci`.

## Acceptance criteria

1. A repository containing `evilpkg/__init__.py` that writes a marker file, and a
   committed `.ctx/ctx.yaml` declaring `verify: [{kind: cmd, run: python3 -m
   evilpkg.sub}]`, leaves the marker **absent** after each of `ctx init`,
   `ctx doctor` and `ctx ci` runs with that repo as cwd.
2. That test carries a positive control: the same fixture package, imported
   directly in a subprocess with the repo on `sys.path`, **does** write the
   marker. Without this the test can pass because the fixture stopped being
   parsed at all — the fail-green shape the audit calls out by name.
3. A non-dotted hostile name is covered too: `run: python3 -m evilmod` with an
   `evilmod.py` in the repo root that writes a marker leaves that marker absent.
4. `_availability` still returns `(True, "")` for a module that genuinely imports
   on this machine (use one from the stdlib, e.g. `python3 -m json.tool`).
5. `_availability` still returns `(False, ...)` with a reason naming the module
   for an interpreter form whose module is genuinely absent, and `(False, ...)`
   naming the binary for a first token that is not on PATH.
6. The probe cannot hang the caller: a 20s-or-shorter timeout is still enforced
   and a `TimeoutExpired` still yields `(False, ...)` rather than raising.
7. Existing `ctx doctor` and `ctx ci` output assertions across `tests/` pass
   unchanged.
8. `python3 -m unittest discover -s tests` is green.
9. No file outside `owns` is modified. In particular, do not edit `ctx/verify.py`
   or `ctx/hooks.py` — sibling units own them this wave.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · which of
the three mechanisms you chose and why · any interface you were forced to change.
