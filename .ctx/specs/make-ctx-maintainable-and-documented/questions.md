---
ctx_schema: 1
spec: make-ctx-maintainable-and-documented
---

## Blocking




<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] Item 26 says 'real spend recorded in telemetry so the complexity weights have something to be checked against'. But telemetry records only ms and score — ctx is a CLI and never observes token counts. Drop that half, add an explicit report-in command the orchestrator calls, or record an honest proxy?
- [x] How far does the README split go? Report prescribes README under 300 lines plus docs/reference.md and docs/walkthroughs.md; wave 3's docs unit already pre-empted part of it, and its commit recorded that as a scope error.
- [x] Is --json a stable documented contract with per-command schema tests, or best-effort convenience output that may change between releases?
- [x] AUDIT.md and PRODUCTION-AUDIT.md are both stale (AUDIT.md's header is four minor versions and 432 tests out of date) and the report recommends archiving rather than updating them. Archive, update, or leave?

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved




- Item 26 says 'real spend recorded in telemetry so the complexity weights have something to be checked against'. But telemetry records only ms and score — ctx is a CLI and never observes token counts. Drop that half, add an explicit report-in command the orchestrator calls, or record an honest proxy? → Add an explicit report-in command: ctx telemetry --spend <tokens> --unit <name>, which the orchestrator calls after a wave. Spend arrives by report rather than observation, because a CLI cannot see a model's token usage. The data is partial by construction — an orchestrator that does not call it leaves a gap — so every surface that shows it must label it as reported-and-incomplete rather than measured. That labelling is a criterion, not a nicety: an unlabelled partial number will be used to tune the complexity weights as though it were a full measurement. (2026-09-11)
- How far does the README split go? Report prescribes README under 300 lines plus docs/reference.md and docs/walkthroughs.md; wave 3's docs unit already pre-empted part of it, and its commit recorded that as a scope error. → Full three-way split as the report prescribes. README keeps Why, Requirements, Install, Quickstart and the command tables under about 300 lines; docs/reference.md takes the configuration and verify-kind reference; docs/walkthroughs.md takes the worked examples. The size is the cause, not a side effect: a 55KB file reads as accumulated release notes rather than a maintained reference, which is how an entire release's feature set never reached it. (2026-09-11)
- Is --json a stable documented contract with per-command schema tests, or best-effort convenience output that may change between releases? → A stable, documented contract. Each command's JSON shape is pinned by a schema test and changing it is a breaking change. The flag exists for pipelines, and output with no stability promise is output nobody can depend on — which would leave the audit's complaint (pipelines must regex prose) answered in form only. (2026-09-11)
- AUDIT.md and PRODUCTION-AUDIT.md are both stale (AUDIT.md's header is four minor versions and 432 tests out of date) and the report recommends archiving rather than updating them. Archive, update, or leave? → Archive both under docs/history/, each gaining a one-line header naming the version it describes and stating it is historical. They record real decisions so they are not deleted, but PRODUCTION-AUDIT.md's 'snapshot.py and findings.py: zero tests, zero callers' verdict reads as current while both modules now have dedicated suites. A stale document presented as live is worse than an archived one. report.md remains the authoritative audit. (2026-09-11)
