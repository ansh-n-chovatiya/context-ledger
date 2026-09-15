# Design: `preview.html` always carries real content, before any code changes begin

**Date:** 2026-09-14
**Scope:** L2 (`ctx:spec` → `ctx:plan` → `plan-check`) only. L1 (`ctx:task`) is explicitly out of scope.

## Problem

`preview.html` is meant to let a non-technical reviewer read and approve a
plan before any code changes happen. In practice it is dominated by
placeholder text — "Nobody has written this part down yet." / "put together
automatically" — for every plan-level and per-unit field, because:

1. The plain-language narrative comes only from `plain.md`, which is
   deliberately never auto-written (`commands/preview.md`: "an agent
   answering them is an agent grading its own homework").
2. Nothing in the normal flow (`ctx:spec` → `ctx:plan` → `plan-check` →
   `ctx:start`) ever prompts a human to actually fill `plain.md` in, so it
   sits at its scaffolded, empty state indefinitely.
3. `plan-check` renders `preview.html` eagerly on every run regardless, so
   the placeholder text is never surfaced as a gap — it just reads like
   (empty) content.

This was verified directly against the plan built earlier in this session
(`close-the-p0-and-p1-findings-...`): every plan-level section and every
per-step field showed the generic fallback, while the real substance (owns,
criteria, verify commands) was accurate but hidden behind a "Technical
detail" toggle that defaults off.

## Goal

**By the time `ctx:start` would dispatch the first unit — i.e. before any
code change happens — `preview.html` must already contain real content for
every field it renders, not the generic placeholder.** This is a hard
requirement, not a best-effort improvement: it must hold by construction, the
same way `spec-ready` already hard-refuses to let planning proceed while a
blocking question is open, not by convention or skill instructions alone.

## Non-goals

- L1 (`ctx:task`) is unaffected. It has no `plain.md`/`preview.html` today
  and gets none from this work.
- `plain.md`'s existing contract is unchanged: a human can still write
  directly into it, it is still never auto-written wholesale, and authored
  text still always wins over anything generated or inferred.
- This does not retroactively backfill old plans (including the one built
  this session). Plans created before this ships have no intake record, and
  the page says so honestly rather than fabricating one.
- No new interview questions beyond what's needed to fill the fields the
  page actually renders — the design explicitly avoids over-asking. Most
  fields are meant to resolve by confident inference or by reusing content
  that already exists, not by asking.

## Design: a five-tier fallback chain

Every field `preview.html` renders — five plan-level sections, five fields
per unit — resolves through this chain, first match wins:

1. **Human-authored text in `plain.md`.** Unchanged from today. Always wins.
2. **Plan-level fields only** (`why now`, `what could go wrong`,
   `what changes for you`) **not in `plain.md`** → the intake interview's
   resolved answer (see below) — either the user's own answer, or a
   confident inference the AI made and recorded without asking.
3. **Per-unit `what it does` / `why it matters` not in `plain.md`** → the
   unit's own `## Objective` / `## Background` text, rephrased plainly. This
   text already exists and is already substantive — it is written (by a
   human, or by an AI under a human's direction, at plan-writing time) for
   every unit today, just never surfaced into the plain-language slot.
4. **Per-unit `risk` / `what could go wrong` not in `plain.md`** → mechanical
   signals `plan-check` already computes (`ownership_gaps`, `bottlenecks`,
   `contested`) rendered as plain statements of fact, not an invented
   judgment. `plain.py`'s existing rule — "generated text states facts and
   never judges" — is preserved; this tier just gives it a richer, real
   fact to state instead of "No files are recorded as changing."
5. **True last resort**, which should now be rare: today's generic
   placeholder, kept as an honest "nothing to show" signal for cases outside
   this design (chiefly: plans that predate this feature).

`Plain.unit()`'s current two-way `generated: bool` flag becomes three-way —
`authored` / `inferred` / `generated` — so the rendered page can label
provenance honestly: "confirmed by you," "inferred from your intent, not
directly confirmed," or the existing mechanical-fact styling. This is not
cosmetic: it is what keeps tier 2/3/4 content from being indistinguishable
from tier 1, which would silently recreate the "AI writes plain.md" problem
this design exists to avoid.

## The intake interview (tier 2's source)

`ctx:spec` already has the right mechanism — "write down every question
whose answer would change what you build... be honest about which [questions]
are blocking" — it simply never runs that judgment over the plain-language
fields. This design extends it to three fixed categories: *why now*, *what
could go wrong* (plan-level), *what changes for you*.

For each category, `ctx:spec` drafts a candidate answer from whatever
already exists (the ticket, the prompt, the spec's own Intent) before doing
anything else:

- **If confident**, it records the answer as `inferred` in `questions.md` —
  no question shown to the user — along with a one-line rationale for why it
  didn't ask. This is not silent: it is auditable, and a reviewer who
  disagrees can still correct it, same as any other recorded answer.
- **If not confident**, it asks — via a real question, the draft answer
  offered as the recommended option, so confirming costs one choice, not an
  essay.

This directly answers the earlier objection ("no user is ever going to write
an essay"): the default path for a well-described ticket is zero new
questions, because the AI's draft is usually confirmable rather than
answerable-from-scratch.

## The enforcement point

`ctx spec-ready` — which already exits non-zero while any blocking question
is open — is extended to also require that all three intake categories have
*some* recorded resolution (`answered` or `inferred`), not just "no open
blocking questions." `ctx plan.check` is extended to require a non-empty
`Objective` per unit (today `REQUIRED = ("unit", "tier", "owns")` at
`ctx/plan.py:35` does not include it, even though `scaffold_unit` always
writes a placeholder for it).

Both are code-level gates, not skill-instruction conventions — which is the
difference between this holding "always" and this holding "usually, until
someone runs the raw CLI or a different session skips the doc." That
distinction is the entire lesson of this session's `report.md` findings
(silent gate holes that only worked because a convention was followed), and
this design does not repeat it.

## Data flow, end to end

```
ctx:spec        → Intent written, as today.
                → for each of 3 intake categories: draft an answer from
                  existing text; record as `inferred` (silent) or ask (draft
                  pre-filled as the recommended option).
                → ctx resolve records the outcome either way.
ctx spec-ready  → refuses if any blocking question is open, OR if any of the
                  3 intake categories has no recorded resolution at all.
ctx:plan        → decomposes into units; plan.check refuses a unit with an
                  empty Objective.
ctx plan-check  → computes waves/ownership_gaps/bottlenecks (unchanged) →
                  renders preview.html, whose per-field content now resolves
                  through the 5-tier chain above.
ctx:start       → dispatches units. First point any code changes happen.
                  By construction, preview.html already had real content
                  before this line ever ran.
```

## Error handling / degradation

- A plan with no intake record at all (built before this shipped, or built
  by tooling that bypassed `ctx:spec` entirely) renders tier 5 honestly —
  it does not fabricate an inference retroactively.
- Any rendering failure degrades to a `note:` line, exactly as
  `_plan_check_preview` already does today — nothing here is allowed to
  block planning on a rendering bug.
- An `Objective` that fails the new non-empty check is refused at
  `plan.check`, naming the unit, the same way a missing `owns` is today.

## Testing

- A fresh `ctx:spec` run always produces a recorded resolution (`answered`
  or `inferred`) for all three intake categories — never silently skipped.
- An `inferred` resolution is traceable to real source text (the ticket/
  spec Intent), never invented from nothing — asserted the same way
  `test_kind_table_reaches_complexity.py` pairs each assertion with a
  control that proves it isn't vacuous.
- `spec-ready` refuses a spec missing any of the 3 intake resolutions, same
  test shape as its existing blocking-question refusal.
- `plan.check` refuses a unit with an empty `Objective`.
- `preview.html`'s per-field rendering is tested tier by tier: authored wins
  over inferred wins over generated; the three-way `generated` flag renders
  the right provenance label for each; a legacy plan with no intake record
  degrades to tier 5 without error.
- A regression test pins the actual scenario that motivated this: build a
  plan through the full flow and assert zero occurrences of the literal
  string `"Nobody has written this down yet."` anywhere in the rendered
  page — the closest thing to an end-to-end proof of the stated goal.

## Open questions left to the implementation plan

- Exact confidence heuristic for "infer silently" vs. "ask" is a judgment
  call embedded in `ctx:spec`'s skill instructions, not enforceable in code
  the way the rest of this design is. Worth naming as a known soft spot:
  the gate enforces *that* a resolution exists, not that the inference was
  good. That asymmetry is inherent to asking an LLM to judge its own
  confidence, and is mitigated (not eliminated) by the `inferred` label
  staying visible on the page for a human to challenge.
- Exact rephrasing rules for turning `Objective`/`Background` (written for
  an implementer) into plain language (written for a non-technical reader)
  need concrete examples during implementation, not just a rule of thumb.
