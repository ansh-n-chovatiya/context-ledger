---
ctx_schema: 1
adr: 1
title: Decline harness portability (G7)
status: accepted
date: 2026-09-09
---

# 0001. Decline harness portability (G7)

## Context
Superpowers ships eight harness variants; ctx ships .claude-plugin only. The 0.8 brief raised G7 as an open question and recommended declining. Deciding not to do it is also an answer, and an unrecorded refusal gets re-litigated every audit.

## Decision
Do not port to additional harnesses in 0.8. ctx targets .claude-plugin only. Each additional harness variant multiplies the maintenance surface — every command, agent and hook gains another copy that CI must exercise and every future change must update in lockstep — and no P0 or P1 capability in this spec depends on any of it.

## Consequences
ctx stays single-harness. Users on other harnesses run it through its CLI (bin/ctx), which is stdlib-only and harness-agnostic by construction, rather than through slash commands. Revisit on evidence, not on symmetry with superpowers: a concrete user on a named harness blocked on a capability the CLI cannot supply. Until then the maintenance cost is paid once, not eight times.

## Status
Accepted. Supersede with a new ADR rather than editing this one.
