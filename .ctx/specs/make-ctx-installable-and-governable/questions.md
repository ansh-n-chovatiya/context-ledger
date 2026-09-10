---
ctx_schema: 1
spec: make-ctx-installable-and-governable
---

## Blocking


<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] What distribution name goes in pyproject.toml?
- [x] How much of the PyPI publishing path should the release workflow contain?

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved


- What distribution name goes in pyproject.toml? → context-ledger. Import package stays ctx and the console script stays ctx; the distribution matches the repo and the marketplace entry and will not collide with the very common three-letter name. (2026-09-11)
- How much of the PyPI publishing path should the release workflow contain? → None. The workflow builds wheel, sdist, SHA256SUMS and a CycloneDX SBOM and attaches them to a GitHub Release on tag. No publish step is written at all, because Trusted Publishing needs PyPI-side configuration only the maintainer can do. (2026-09-11)
