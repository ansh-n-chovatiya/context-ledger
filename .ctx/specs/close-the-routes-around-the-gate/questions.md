---
ctx_schema: 1
spec: close-the-routes-around-the-gate
---

## Blocking


<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] What does the diff check compare against, now that committing hides changes?
- [x] How should --rebaseline behave when the contract body has changed?

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved


- What does the diff check compare against, now that committing hides changes? → The dispatch seal's commit. The seal records HEAD at dispatch; the gate compares that SHA to HEAD plus the working tree, so committing no longer hides an out-of-scope edit. Needs a defined behaviour for a rebase or amend under a running unit. (2026-09-10)
- How should --rebaseline behave when the contract body has changed? → Refuse and require a separate explicit flag. --rebaseline retakes the review baseline only; if verify, owns or criteria differ from the seal it refuses, names the changed fields, and directs to a distinct loudly-journalled --reseal. Separates the routine operation from the dangerous one. (2026-09-10)
