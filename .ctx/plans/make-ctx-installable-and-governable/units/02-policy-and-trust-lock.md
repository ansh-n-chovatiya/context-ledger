---
ctx_schema: 1
unit: 02-policy-and-trust-lock
plan: make-ctx-installable-and-governable
tier: subagent
depends_on: []
owns:
  - ctx/config.py
  - ctx/cli.py
  - ctx/trust.py
  - tests/test_policy.py
reads:
  - path: ctx/hooks.py
    symbols:
      - the CTX_GATE check at hooks.py:219
  - path: ctx/journal.py
    symbols:
      - append
forbid:
  - README.md
  - pyproject.toml
  - .github/workflows/ci.yml
budget_tokens: 110000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Give the gate a control plane a repository cannot overrule, make disabling it
leave a record, and give CI a way to verify trust against a committed lockfile
instead of blanket-accepting whatever the repo declares.

## Context

**No policy layer.** `config.load` reads only the repository's own
PR-editable `.ctx/ctx.yaml`. There is no org, user or system layer, so there is
no way to say "this control is not negotiable per-repo."

**An unlogged global off-switch.** `ctx/cli.py:1091` and `ctx/hooks.py:219` both
honour `CTX_GATE=off`, and `README.md:1184` markets it: *"The gate keeps blocking
and I can't finish. `CTX_GATE=off` disables it immediately."* One environment
variable removes the control the product exists to provide, with no audit record
and no fleet-wide way to detect it. An escape hatch is defensible; an unlogged one
the docs recommend is not.

**Trust degrades to a rubber stamp in CI.** The trust store is keyed by resolved
project path *outside* the repo (`trust.py:44-63`), so every ephemeral runner
starts empty and `ctx ci` cannot pass without `ctx trust --yes` — which
rubber-stamps whatever shell strings the repo's PR-editable `ctx.yaml` declares.
The control is real for a human at a terminal and vacuous everywhere an
enterprise actually runs it. There is currently **no** committed lockfile; trust
lives in gitignored `.ctx/runtime/verify.trust`.

## Interfaces

`config.load(...)` must keep its current signature and return shape — `hooks.py`
and many modules call it and are not yours. Add the policy resolution *inside* it
or in a new function it calls; do not change what callers pass or receive.

`trust.command_id(check)` already hashes `run` + `cwd` + `env` and is the right
identity for a lockfile entry. Reuse it rather than inventing a second identity
scheme — two ways to name the same command is how a lockfile silently stops
matching.

You own `ctx/cli.py`, which is 2,380 lines. Do not refactor it — that is roadmap
wave 5 and a separate plan. Make the smallest changes that satisfy the criteria.

## Acceptance criteria

1. Policy resolves **system → user → repo**, in that precedence, with later
   sources overriding earlier ones except where locked. State the file locations
   you chose for the system and user layers, per-platform, and why.
2. A `locked:` section in a system or user policy cannot be overridden by a
   repo-level `.ctx/ctx.yaml`. A repo that tries must not silently win: the
   attempt is visible, and the locked value holds.
3. `ctx doctor` prints which source supplied the active policy, so "why is this
   setting what it is" is answerable without reading three files.
4. Absent policy files change nothing. A machine with no system or user policy
   behaves exactly as it does today — this is the criterion that keeps the change
   from breaking every existing user, so test it explicitly.
5. `CTX_GATE=off` is journalled **every time it takes effect**, at both sites
   (`cli.py` and the `hooks.py` equivalent — you own `cli.py`; for `hooks.py`,
   which you do not own, report what needs to change rather than editing it).
6. A `locked:` policy can refuse `CTX_GATE=off` outright, and when it does, the
   gate runs and the refusal is recorded.
7. Journalling the escape hatch must not itself become a way to break a session:
   if the journal write fails, the gate's behaviour is unchanged and the failure
   is not silent. Say how you resolved that tension.
8. `ctx trust` can write a **committed** lockfile recording accepted command ids,
   and can verify the current config against it without accepting anything new.
   A command in the config but absent from the lockfile fails verification and is
   named. This is what replaces `ctx trust --yes` in CI.
9. The lockfile is diffable and stable: the same set of accepted commands
   produces byte-identical content across runs and platforms, so it is reviewable
   in a pull request. Sort deterministically; no timestamps, no absolute paths.
10. A tampered lockfile does not silently pass. Changing a command's `run` text
    in `ctx.yaml` after locking must fail verification, naming the command.
11. Positive control. Mutate and record the dead test, then revert: (a) let a
    repo config override a `locked:` value; (b) make `CTX_GATE=off` silent again;
    (c) make lockfile verification accept a command absent from the lock.
    Each must kill at least one test.
12. `python3 -m unittest discover -s tests` is green, count not below 906, and
    `ctx doctor` and `ctx ci` exit 0.
13. No file outside `owns` is modified. Do not edit `README.md` — report what the
    docs unit must say, including the replacement CI recipe in outline.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
policy file locations per platform and the reasoning · how you resolved the
criterion-7 tension · what `hooks.py` needs (criterion 5) since you do not own it ·
the three mutation results with the dead test for each · the outline of the
replacement CI recipe · any interface you were forced to change (that blocks the
wave).
