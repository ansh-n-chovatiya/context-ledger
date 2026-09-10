---
ctx_schema: 1
unit: 01-packaging
plan: make-ctx-installable-and-governable
tier: subagent
depends_on: []
owns:
  - pyproject.toml
  - .github/workflows/release.yml
  - tests/test_packaging.py
reads:
  - path: ctx/__init__.py
    symbols:
      - __version__
  - path: .claude-plugin/plugin.json
forbid:
  - .github/workflows/ci.yml
  - README.md
  - ctx/cli.py
budget_tokens: 60000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Make "we run ctx 0.8.0" a verifiable statement: real packaging metadata, and a
release workflow that produces artifacts an enterprise can pin, mirror and check.

## Context

There are zero git tags. `.claude-plugin/marketplace.json` declares
`"source": "./"`, so the plugin *is* the default branch — 0.8.0 is whatever
`main` held when each engineer last synced. There is no `pyproject.toml`,
`setup.py` or `package.json`, so nothing exists for `pip-audit`, Dependabot,
Snyk or Syft to read, and `ctx` reaches PATH only by a symlink each engineer
maintains by hand.

The mitigating fact, which is also the strongest claim this project has: the
runtime is 100% stdlib with zero network-capable imports anywhere in the tree.
The supply-chain *risk* is genuinely low; the supply-chain *evidence* is absent.
Packaging is where that evidence goes, so declaring zero runtime dependencies is
a criterion rather than an incidental detail.

**Recorded decisions — settled, not open:**
- Distribution name is **`context-ledger`**. The import package stays `ctx` and
  the console script stays `ctx`.
- The workflow builds and attaches artifacts to a GitHub Release on tag. It
  contains **no publish step of any kind** — no PyPI, no Trusted Publishing.
  That needs registry-side configuration only the maintainer can do.

## Interfaces

Consumes `ctx.__version__` (currently `"0.8.0"`) and
`.claude-plugin/plugin.json`'s `version` field. CI already asserts those two
agree; do not break that check — it lives in `.github/workflows/ci.yml`, which
is a sibling's file and in your `forbid`.

Produces `pyproject.toml` and `.github/workflows/release.yml`. A later unit
documents both; do not write user documentation yourself.

## Acceptance criteria

1. `pyproject.toml` declares `requires-python = ">=3.8"`, license metadata, and
   `project.scripts.ctx` pointing at the existing entry point, so the 3.8 floor
   becomes an install-time constraint rather than a README claim.
2. It declares **no runtime dependencies**, and a test asserts that — an empty or
   absent `dependencies` list, checked structurally, so adding one later fails
   the suite and has to be a deliberate act.
3. The version in `pyproject.toml` does not drift from `ctx.__version__` and
   `plugin.json`. Either derive it dynamically or add a test asserting all three
   agree. Say which you chose and why.
4. **Build it and run it, do not reason about it.** Build an sdist and a wheel
   from a clean checkout, install the wheel into a scratch virtualenv, and run
   `ctx --version` from that virtualenv. It must print the same string as
   `python bin/ctx.py --version`. Paste the verbatim session, including the build
   output and the installed-console-script invocation.
5. The sdist contains what a rebuild needs — the `ctx/` package, `bin/`, and the
   plugin manifest — and excludes `.ctx/runtime/` and test fixtures that would
   bloat it. List what shipped and confirm by inspecting the built archive, not
   the config that was supposed to produce it.
6. `.github/workflows/release.yml` triggers on a `v*` tag and produces four
   artifacts: wheel, sdist, `SHA256SUMS`, and a CycloneDX SBOM. All four attach
   to a GitHub Release.
7. The workflow contains **no publishing step**. A test asserts that no
   `pypa/gh-action-pypi-publish`, no `twine upload`, and no `id-token: write`
   permission appears in it, so the boundary cannot be crossed by accident later.
8. Every action in the release workflow is pinned to a full commit SHA, not a
   tag, and the workflow declares a least-privilege `permissions:` block. It
   needs `contents: write` to attach release assets; nothing more.
9. **No tag is pushed and nothing is published by this unit.** `git tag` must
   list exactly what it listed before you started. Confirm and paste it.
10. Positive control. Mutate and record the dead test, then revert: (a) add a
    runtime dependency to `pyproject.toml`; (b) change the version so the three
    sources disagree; (c) add a `twine upload` line to the release workflow.
    Each must kill at least one test.
11. `python3 -m unittest discover -s tests` is green, count not below 906.
12. No file outside `owns` is modified. Do not edit `README.md` — a later unit
    documents this; report what it must say.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
full criterion-4 build-and-install session · the sdist contents from criterion 5 ·
the `git tag` output from criterion 9 · the three mutation results with the dead
test for each · what the docs follow-up must say · any interface you were forced
to change (that blocks the wave).
