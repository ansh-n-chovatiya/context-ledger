---
description: Advance or inspect a unit's phase gate (reproduce/locate/fix/guard for kind: bug, or a declared `phases:` list)
allowed-tools: Bash, Read, Task
argument-hint: «unit» [phase] [--plan slug] [--command "…"] [--exit-code N] [--evidence "…"] [--note "…"]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" phase $ARGUMENTS || true`

A unit with `kind: bug` gates through four phases in order — `reproduce`,
`locate`, `fix`, `guard` — and each has to be recorded before the next one
opens. A unit with its own `phases: […]` gates through exactly that list
instead, with no bug semantics attached: no phase named `fix` there means
anything special.

**With no phase named**, the line above only inspects — it lists every
declared phase, how many times it has been recorded, and whether it is open
right now. Run it before recording anything; it tells you what the gate
actually wants next instead of you guessing from the unit file.

**With a phase named**, this tries to record it, and the write only happens
if the gate is open. If a prerequisite is missing, the command refuses and
prints exactly what it found missing. Show the user that reason as given —
it already names the specific phase that has to be recorded first, and a
generic "not allowed" throws that away.

For a bug unit, in order:

- **`reproduce`** — record a run that actually **failed**:
  `ctx phase «unit» reproduce --command "pytest tests/test_x.py::case" --exit-code 1 --evidence "AssertionError at line 42"`.
  A zero exit reproduces nothing and does not satisfy this phase.
- **`locate`** — record where, in `file:line` form:
  `ctx phase «unit» locate --evidence "src/auth.py:118 — token compared before the expiry check"`.
  A bare assertion of where the bug lives does not satisfy this either.
- **`fix`** — after the change, record the **same** reproduction command
  exiting zero: `ctx phase «unit» fix --command "pytest tests/test_x.py::case" --exit-code 0`.
  A different, easier command passing does not prove this bug is gone.
- **`guard`** only opens once `fix` is recorded, and it is judged, not
  mechanical: the listing above shows what to judge under "judged on" —
  dispatch the `verifier` agent with that text and the reproduce/locate/fix
  evidence already on record, then write its verdict:
  `ctx phase «unit» guard --exit-code 0 --evidence "<verifier's verdict>"`.

Recording anything out of order is refused in the words the gate used to
refuse it — do not retype the command with different values hoping it goes
through; read the refusal and record the phase it names first.
