---
ctx_schema: 1
spec: make-the-ctx-cli-safe-to-automate
---

## Blocking



<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] Does --strict close the exit-code P0, or only mitigate it?
- [x] Do coverage and ruff land in this wave or with pyproject in wave 3?
- [x] What value does the CI test-count floor take?

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved



- Does --strict close the exit-code P0, or only mitigate it? → Hybrid. Genuine errors exit non-zero by default, which closes the P0; --strict/CTX_STRICT additionally escalates advisory paths that legitimately exit 0 today. Accepted as a breaking change worth a changelog entry. (2026-09-10)
- Do coverage and ruff land in this wave or with pyproject in wave 3? → Deferred to wave 3. Only the test-count floor and fetch-depth 0 land now; both need no dev-dependency config, and ruff on cli.py is its own backlog. (2026-09-10)
- What value does the CI test-count floor take? → User deferred to my judgement. Floor set just under the current 749 rather than the roadmap's 600, because the DONE bar is explicitly that the count has not silently dropped, which a floor of 600 cannot detect. (2026-09-10)
