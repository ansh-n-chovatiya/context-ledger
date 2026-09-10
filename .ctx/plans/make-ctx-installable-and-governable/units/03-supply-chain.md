---
ctx_schema: 1
unit: 03-supply-chain
plan: make-ctx-installable-and-governable
tier: subagent
depends_on: []
owns:
  - .github/workflows/ci.yml
  - SECURITY.md
  - CODEOWNERS
  - .github/dependabot.yml
  - tests/test_supply_chain.py
reads:
  - path: tests/test_ci_floor.py
    symbols:
      - the YAML-subset parser and the step-extraction helpers
forbid:
  - .github/workflows/release.yml
  - README.md
  - ctx/cli.py
  - pyproject.toml
budget_tokens: 60000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Pin what CI runs, constrain what it can do, and give the project the governance
files a vendor-intake questionnaire asks for.

## Context

Every GitHub Action in `ci.yml` is referenced by a mutable tag (`actions/checkout@v4`,
`actions/setup-python@v5`), so what runs is whatever that tag points at today. No
workflow declares a `permissions:` block, so each job gets the default token
scope. There is no `SECURITY.md` — no documented vulnerability-disclosure
channel, which is a required field on most vendor-intake questionnaires and a
conspicuous gap for a tool that executes shell commands arriving via `git clone`.
No `CODEOWNERS`, no Dependabot config.

**Do not weaken what is already there.** `ci.yml` gained a test-count floor and a
`fetch-depth: 0` deep checkout pinned to one matrix entry in an earlier wave.
`tests/test_ci_floor.py` proves the floor by *executing* the extracted step
against synthetic suites, and asserts the deep checkout is enabled for exactly
one matrix combination. Your changes must leave all of that green. That file is
**not** yours to edit — if a change of yours breaks it, the change is wrong.

Note for reuse rather than reinvention: `tests/test_ci_floor.py` already contains
a strict YAML-subset parser and step-extraction helpers, written because PyYAML
is not a dependency (the plugin is stdlib-only). Import from it rather than
writing a second parser.

## Interfaces

Consumes the existing `ci.yml` structure. Produces no Python API.

`.github/workflows/release.yml` belongs to a concurrent sibling and is in your
`forbid`. Pin actions only in the file you own; say in your report that the
sibling's workflow needs the same treatment so the orchestrator can confirm it.

## Acceptance criteria

1. Every `uses:` in `.github/workflows/ci.yml` is pinned to a full 40-character
   commit SHA with the human-readable version retained in a trailing comment.
   Resolve each SHA against the real upstream repository — do not invent one. Say
   how you resolved them, and if you could not resolve one, say so rather than
   guessing.
2. Every job declares an explicit least-privilege `permissions:` block. The test
   jobs need `contents: read` and nothing more.
3. A test asserts criteria 1 and 2 **structurally**, by parsing the workflow, not
   by grepping for substrings a comment could satisfy. Reuse the parser in
   `tests/test_ci_floor.py`. State how you avoided comment-satisfiability.
4. The test fails on an unpinned action: a `uses:` with a tag rather than a SHA
   must be detected wherever it appears in the file, including in a job added
   later. Prove this with the positive control in criterion 8.
5. `SECURITY.md` exists with a real disclosure channel, a supported-versions
   statement, and an expected response window. It must not promise a channel that
   does not exist — if there is no security contact address available to you, use
   GitHub's private vulnerability reporting and say so, rather than inventing an
   email address.
6. `CODEOWNERS` and `.github/dependabot.yml` exist. Dependabot covers
   `github-actions` at minimum; there is no Python dependency manifest to watch
   unless a concurrent sibling's `pyproject.toml` has landed, and you must not
   assume it has — if you reference it, the config must be harmless when the file
   is absent.
7. Everything `ci.yml` proved before, it still proves: the test-count floor, the
   deep checkout on exactly one matrix entry, the Windows wrapper job, the ledger
   self-check, and the version-agreement check. `tests/test_ci_floor.py` passes
   unmodified — confirm with `git diff --exit-code tests/test_ci_floor.py` and
   paste the result.
8. Positive control. Mutate and record the dead test, then revert: (a) unpin one
   action back to a tag; (b) remove a `permissions:` block; (c) widen a
   `permissions:` block to `write-all`. Each must kill at least one test.
9. `python3 -m unittest discover -s tests` is green, count not below 906.
10. No file outside `owns` is modified. Do not edit `README.md` — report what the
    docs unit must say.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · how you
resolved each action SHA and any you could not · how criterion 3 avoids being
comment-satisfiable · the `git diff --exit-code tests/test_ci_floor.py` result ·
the three mutation results with the dead test for each · what the docs follow-up
must say · confirmation that the sibling's release workflow still needs pinning ·
any interface you were forced to change (that blocks the wave).
