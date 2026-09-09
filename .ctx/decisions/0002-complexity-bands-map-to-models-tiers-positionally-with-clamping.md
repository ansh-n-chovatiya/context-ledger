---
ctx_schema: 1
adr: 2
title: Complexity bands map to models.tiers positionally, with clamping
status: accepted
date: 2026-09-09
---

# 0002. Complexity bands map to models.tiers positionally, with clamping

## Context
complexity.tier_for returns a band name (light/standard/deep) derived from complexity.thresholds, which defines two boundaries and therefore three bands. config.models.tiers holds ordered model names (haiku/sonnet/opus), cheapest first, and config.tier_up walks that list. Nothing defined how a band name becomes a model name — the plan underspecified the interface between 05-complexity-score and 08-dispatch-selection. An explicit complexity.tier_models mapping in ctx.yaml was the obvious alternative.

## Decision
Map positionally: band index into models.tiers, clamped to the last entry. light -> tiers[0], standard -> tiers[1], deep -> tiers[2], and any band index beyond the list clamps to the dearest available model. Rejected an explicit band-to-model mapping in config because it would spell model names in a second place, and models.tiers is meant to be the one list every module consults — a project swapping in a house model should get that honoured everywhere at once, which a duplicate mapping defeats. 08-dispatch-selection owns the mapping function; it lives in dispatch.py because complexity.py and config.py are already closed.

## Consequences
The band count and the tier-list length are deliberately decoupled: a project running two tiers gets deep clamped onto the dearer of the two rather than an IndexError, and a project running four gets the fourth reachable only by tier_up escalation, never by scoring. That asymmetry is intended — scoring picks a starting seat, escalation is what reaches past it. If a project ever wants a fourth scored band it adds a threshold, not a model. Clamping must be asserted, not assumed: a two-entry models.tiers is the test case that catches an unclamped index.

## Status
Accepted. Supersede with a new ADR rather than editing this one.
