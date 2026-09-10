---
ctx_schema: 1
unit: 04-docs-currency
plan: make-ctx-installable-and-governable
tier: subagent
depends_on:
  - 01-packaging
  - 02-policy-and-trust-lock
  - 03-supply-chain
owns:
  - README.md
  - CHANGELOG.md
reads:
  - path: pyproject.toml
  - path: .github/workflows/release.yml
  - path: ctx/trust.py
    symbols:
      - the lockfile write and verify entry points
forbid:
  - ctx/cli.py
  - .github/workflows/ci.yml
budget_tokens: 70000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective

Make the README describe the tool that now exists: a safe CI recipe, the new
exit-code and gate behaviour, and the install path.

## Context

The README currently contains a documented CI recipe that is a supply-chain hole
in its own right (`README.md:1135-1141`): it clones unpinned HEAD from a personal
GitHub account and then runs `ctx trust --yes`, which rubber-stamps whatever
shell strings that repo's PR-editable `ctx.yaml` declares. A malicious pull
request gets shell in CI. The audit calls this the single item that fails a
security review outright.

Three earlier waves also moved behaviour the README still describes the old way.
Your `depends_on` units will each report what their change requires; read those
reports as your source, and verify each claim against the code rather than
trusting the summary.

Known stale claims to fix, from the audit and from this session:
- `README.md:1184` and `:902` market `CTX_GATE=off` as the way to proceed when
  the gate blocks. It is now journalled and can be refused by policy.
- The `--rebaseline` description says it re-seals the contract. It no longer
  does; `--reseal` is the deliberate route, and `--rebaseline` refuses a changed
  contract naming the fields that moved.
- The exit-code contract changed: refusals exit 2 on stderr for all commands, an
  unexpected exception is one line with `CTX_DEBUG=1` restoring the traceback,
  and `--strict`/`CTX_STRICT=1` escalates three enumerated advisory conditions.
- The `diff` check now compares against the commit recorded at dispatch, and a
  dispatch commit removed by amend or rebase fails the gate until `--reseal`.
- `ctx unit --status done` refuses a unit with no dispatch seal; `ctx status`
  shows such a unit as `unsealed`.

## Interfaces

Consumes the three sibling units' reports and the files they produced. Produces
documentation only — **no code changes**. If documenting something reveals a bug,
report it; do not fix it here.

## Acceptance criteria

1. The CI recipe no longer clones unpinned HEAD and no longer runs
   `ctx trust --yes`. It pins a version and verifies against the committed
   lockfile that unit 02 built. The recipe must be one a reader can paste and run
   — verify the commands exist and the flags are real, do not compose them from
   the unit report's prose.
2. The recipe explains *why* it is shaped that way in one or two sentences, so a
   reader who is tempted to simplify it back to `trust --yes` understands what
   they would be giving up.
3. Install documentation covers the packaged path from unit 01: what to install,
   what lands on PATH, and the supported Python floor.
4. Every stale claim listed in Context above is corrected. For each, state in
   your report the line you changed and what it now says.
5. You must verify, not assume. For each behavioural claim you write, name how
   you checked it — a command you ran, or the code you read. A claim taken from a
   sibling's report and not checked is the failure mode this criterion exists to
   prevent; the audit found two directly checkable false claims in this README
   already.
6. `CHANGELOG.md` gains an entry covering this wave: packaging and the release
   workflow, the policy layer, the trust lockfile, the CI hardening, and the
   documentation corrections. Breaking changes are marked as such.
7. The README does not grow. It is already 1,386 lines and 55KB, which the audit
   identifies as how the drift happened. Net line count must not increase — if
   you add, remove or relocate an equivalent amount. Report the before and after
   counts. Splitting the README into `docs/` is roadmap wave 5 and out of scope
   here, so this is a holding constraint, not a restructure.
8. No false claims are introduced. Any statement about behaviour must be true of
   the code as it stands at the end of this wave.
9. `python3 -m unittest discover -s tests` is green, count not below 906, and
   `ctx doctor` and `ctx ci` exit 0. Several tests assert README content; if one
   fails, the README is wrong, not the test — unless the test pins a claim this
   wave deliberately changed, in which case say so explicitly.
10. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · for each
corrected claim, the line and what it now says · for each behavioural claim, how
you verified it (criterion 5) · README line count before and after · any test
that pins a claim this wave changed · any bug you found while documenting and did
not fix · any interface you were forced to change (that blocks the wave).
