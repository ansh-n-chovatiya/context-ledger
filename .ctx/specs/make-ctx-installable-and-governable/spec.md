---
ctx_schema: 1
spec: make-ctx-installable-and-governable
status: ready
created: 2026-09-11
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Roadmap wave 3. An enterprise cannot pin, roll back, mirror or reproduce a
version of this tool: there are zero tags, no packaging metadata, and
`marketplace.json` declares `"source": "./"` — the plugin *is* whatever `main`
contained when each engineer last synced. "We run ctx 0.8.0" is unverifiable.
Separately, the one control the product exists to provide can be removed by an
environment variable that leaves no record, and the documented CI recipe clones
unpinned HEAD from a personal account and then blanket-accepts whatever shell
commands that repo's PR-editable `ctx.yaml` declares.

## Acceptance criteria

1. `pyproject.toml` exists with `requires-python = ">=3.8"`, license metadata, and
   a console entry point so `ctx` lands on PATH from an install rather than a
   hand-maintained symlink. The 3.8 floor becomes an install-time constraint
   rather than a README claim.
2. A source distribution and a wheel build reproducibly from a clean checkout,
   and the built artifact actually runs: installing it into a scratch
   virtualenv and invoking `ctx --version` prints the same version as
   `python bin/ctx.py --version`. Prove this by doing it, not by reasoning.
3. The packaging declares no runtime dependencies. The runtime is 100% stdlib and
   the packaging must say so, because that is the strongest supply-chain claim
   this project has.
4. A release workflow produces wheel + sdist + `SHA256SUMS` + a CycloneDX SBOM,
   and attaches them to a GitHub Release triggered by a tag.
5. The workflow is not armed to publish anywhere without a human. Whatever
   publishing step exists is gated on an explicit trigger, and the repository is
   left in a state where a maintainer runs one documented command to cut a
   release. **No tag is pushed and nothing is published by this work.**
6. `ctx doctor` reports the version and, when installed as a package, where it
   was installed from — so "which ctx am I running" is answerable.
7. A policy layer resolves system → user → repo, in that precedence, with a
   `locked:` section a repo-level config cannot override. `ctx doctor` prints
   which source supplied the active policy.
8. `CTX_GATE=off` is journalled every time it takes effect, never silent, and a
   `locked:` policy can refuse it outright. Disabling the gate must leave a
   record that a fleet can audit.
9. The documented CI recipe no longer clones unpinned HEAD and no longer runs
   `ctx trust --yes`. It pins a version and verifies against a committed
   `trust.lock` rather than blanket-accepting whatever the repo declares.
10. Every GitHub Action is pinned to a full commit SHA, every workflow declares a
    least-privilege `permissions:` block, and `SECURITY.md`, `CODEOWNERS` and a
    Dependabot config exist.
11. The README's stale claims are corrected: the `--rebaseline`/`--reseal`
    behaviour from the previous wave, the new exit-code contract, and the diff
    check comparing against the dispatch commit.
12. Every guard added carries a positive control: the unit report names the
    mutation applied and the test that died.
13. `python3 -m unittest discover -s tests` is green, the count has not dropped
    below 906, and `ctx doctor` and `ctx ci` exit 0.

## Out of scope

- **Pushing a tag, creating a GitHub Release, or publishing to any index.** Those
  are outward-facing and irreversible; they belong to the maintainer, not to this
  work. Criterion 5 exists to make that boundary explicit.
- Roadmap waves 4 and 5: atomic writes, locking, ADR collisions, the `cli.py`
  extractions, the README split. Separate plans.
- `--json` output (roadmap item 9), still deferred.
- Retiring `AUDIT.md` and `PRODUCTION-AUDIT.md`, which §3.8 recommends archiving.

## Notes

Criterion 8 is the one with teeth. `CTX_GATE=off` is currently honoured at
`cli.py:1091` and `hooks.py:219` and *marketed* at `README.md:1184` as the way to
proceed when the gate blocks. An escape hatch is defensible; an unlogged one that
the docs recommend is not.

Criterion 3 is worth stating as a criterion rather than assuming: §3.4 found the
supply-chain *risk* genuinely low because the runtime imports nothing
network-capable, and the supply-chain *evidence* entirely absent. The packaging
is where that evidence goes.
