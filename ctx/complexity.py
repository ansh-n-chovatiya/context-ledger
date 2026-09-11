"""Turning a unit's own frontmatter into a single number, and that number into
a dispatch tier.

Every signal here is already on disk before a subagent is ever spawned — the
unit's stated `budget_tokens`, how many paths it `owns` and `reads`, how many
`depends_on` entries it carries, whether any `verify` check needs a model's or
a human's judgement, whether it publishes an `## Interfaces` section a sibling
is waiting on, and whether its `kind` is `bug`. None of that requires reading
the unit's *body* the way a model would; it is a cheap proxy for how much can
go wrong, not a measurement of how much work it actually is.

The score exists to be printed, not just compared. Every dispatch line shows
the breakdown that produced it, so a tier that looks wrong can be diagnosed by
reading the line rather than by re-running anything. That is why `score`
returns the breakdown alongside the number instead of just the number: the
number on its own is not an audit trail.

Weights and thresholds live in `config.DEFAULTS["complexity"]`, not here — a
project that disagrees with a weight edits `ctx.yaml`, it does not fork this
module. See that block's comments for the reasoning behind each constant;
this module only ever reads them, never repeats them.
"""

from . import config as config_mod, verify

# `judged_verify` fires once, flat, if *either* judged kind is present — but the
# breakdown label names which one actually fired, because "rubric=2.0" and
# "human=2.0" are printed on the dispatch line as different diagnoses even
# though they cost the same.
#
# Which kinds those are is asked of `verify` at the moment it is needed, never
# frozen here. `_JUDGED_KINDS = tuple(verify.JUDGED)` at import time used to
# snapshot the two kinds that existed when this module was first imported, so a
# judged kind registered into `verify.KIND_TABLE` afterwards was invisible to
# scoring: the unit that declared it was tiered as though its gate were
# mechanical, and the `judged_verify` weight silently never applied. That is a
# direct hole in the claim `KIND_TABLE` exists to make — that a new kind is one
# dict entry and every consumer sees it — and a frozen tuple is exactly the
# shape of consumer that makes the claim false.


def _weights(config):
    """Default weights with any `ctx.yaml` override laid on top, key by key.

    A project that only overrides one weight (`_merge` in `config.py` already
    guarantees this for a config that went through `config.load`) must not
    lose the rest — a dict-level `.get` with a single fallback would replace
    every sibling weight the moment one of them is touched by hand outside
    `config.load`, e.g. a caller that builds a partial dict directly.
    """
    weights = dict(config_mod.DEFAULTS["complexity"]["weights"])
    weights.update(((config or {}).get("complexity") or {}).get("weights") or {})
    return weights


def _thresholds(config):
    """Same key-by-key fallback as `_weights`, for the two crossing points."""
    thresholds = dict(config_mod.DEFAULTS["complexity"]["thresholds"])
    thresholds.update(((config or {}).get("complexity") or {}).get("thresholds") or {})
    return thresholds


def _term(label, points):
    """One breakdown entry, or nothing.

    A zero or negative contribution is omitted rather than printed as
    `owns 0 paths=0.0` on every dispatch line — the absence of a term already
    says the signal did not fire, which is the whole point of a breakdown
    meant to be read at a glance. Clamping to zero here, term by term, is also
    what keeps the total non-negative even if a hand-edited `ctx.yaml` sets a
    weight negative: a single bad weight can only cancel its own term, never
    push the sum as a whole below zero.
    """
    points = round(points, 2)
    return [(label, points)] if points > 0 else []


def score(config, unit):
    """(score, breakdown). `breakdown` is `[(label, points), ...]`; `score` is
    exactly `sum(points for _, points in breakdown)` — computed that way, not
    recomputed from the raw weights a second time, so the two can never drift.

    Rounding happens once, per term, before a term is ever added to the total:
    dividing a `budget_tokens` by 15,000 is the one signal here that is not
    already an exact binary fraction (unlike the halves everywhere else), so
    without rounding it the total would carry invisible float noise that a
    printed breakdown could not account for. Rounding the *total* instead —
    or rounding it again after summing — is what this function deliberately
    does not do, because a second rounding step is exactly the kind of "close
    enough" that turns "sums to the score" into "sums to the score, allegedly".
    """
    weights = _weights(config)
    breakdown = []

    budget = max(0, unit.budget)
    if budget:
        breakdown += _term(
            f"budget {budget // 1000}k",
            weights["budget_per_15k"] * budget / 15000,
        )

    owns = len(unit.owns)
    if owns:
        plural = "path" if owns == 1 else "paths"
        breakdown += _term(f"owns {owns} {plural}", weights["owns_per_path"] * owns)

    reads = len(unit.reads)
    if reads:
        plural = "path" if reads == 1 else "paths"
        breakdown += _term(
            f"reads {reads} {plural}", weights["reads_per_2paths"] * reads / 2
        )

    depends = len(unit.depends_on)
    if depends:
        plural = "unit" if depends == 1 else "units"
        breakdown += _term(
            f"depends_on {depends} {plural}", weights["depends_on_each"] * depends
        )

    judged = sorted({
        check.get("kind") for check in (unit.checks or [])
        if isinstance(check, dict) and check.get("kind") in verify.JUDGED
    })
    if judged:
        breakdown += _term("+".join(judged), weights["judged_verify"])

    if unit.publishes_interface:
        breakdown += _term("interface", weights["publishes_iface"])

    if unit.kind == "bug":
        breakdown += _term("bug", weights["kind_bug"])

    return sum(points for _, points in breakdown), breakdown


def tier_for(config, value):
    """`"light"`, `"standard"` or `"deep"` — where `value` (a `score()` result)
    falls against `complexity.thresholds`.

    The boundary is inclusive on the low side of each band: a score that lands
    exactly on `thresholds["standard"]` is what "crosses into standard dispatch"
    means, per the comment in `config.DEFAULTS`, so `>=` rather than `>` is the
    deliberate choice here, not an off-by-one.
    """
    thresholds = _thresholds(config)
    if value >= thresholds["deep"]:
        return "deep"
    if value >= thresholds["standard"]:
        return "standard"
    return "light"
