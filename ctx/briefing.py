"""The SessionStart briefing — the only thing that costs tokens on every session.

Two rules govern this module:

1. **Deterministic.** No wall-clock time, no elapsed counters, no anything that
   varies when the underlying state has not. Identical state must produce a
   byte-identical briefing so the prompt cache actually hits across sessions.
2. **Budgeted.** Blocks are emitted in priority order and stop at the character
   cap for the current level. Nothing is inlined that the model could read from
   a path it has been given — criteria are the exception, because they are the
   thing it must not drift from.

Cost at L0 is around 30 tokens against a 61-token cap. That is the number that
makes it acceptable to leave this on for a two-line fix.
"""

from . import config as config_mod, frontmatter, journal, spec as spec_mod

TRUNCATED = "…[briefing truncated]"


def build(layout, config, state):
    level = config_mod.normalise_level(state.get("level"))
    cap = config_mod.briefing_cap(config, level)
    blocks = _blocks(layout, config, state, level)
    return _fit(blocks, cap)


def measure(layout, config, state):
    """Used by `ctx doctor` so the budget is observable, not aspirational."""
    level = config_mod.normalise_level(state.get("level"))
    text = build(layout, config, state)
    return {
        "level": level,
        "chars": len(text),
        "cap": config_mod.briefing_cap(config, level),
        "approx_tokens": round(len(text) / 3.6),
        # `_fit` clamps to the cap, so overflow is impossible and reporting it
        # would be a tautology. Truncation is the signal worth acting on: it
        # means state a session needed was dropped to fit.
        "truncated": TRUNCATED in text,
        "text": text,
    }


def _blocks(layout, config, state, level):
    out = [_headline(layout, state, level)]
    if level == "1":
        out.extend(_task_blocks(layout, state))
    elif level == "2":
        out.extend(_plan_blocks(layout, state))
    recent = journal.recent_paths(layout, 4)
    if recent and level != "0":
        out.append("recent: " + ", ".join(recent))
    out.extend(_auto_load(layout, config, level))
    if level == "0":
        out.append("/ctx:resume for detail · /ctx:task «goal» to track a change")
    return [block for block in out if block]


def _headline(layout, state, level):
    name = config_mod.LEVEL_NAMES[level]
    if level == "0":
        recent = journal.recent_paths(layout, 3)
        tail = (" · last touched " + ", ".join(recent)) if recent else " · no recorded work"
        return f"[ctx] L0 {name}{tail}"
    active = state.get("unit") or state.get("task") or "none"
    return f"[ctx] L{level} {name} · active: {active}"


def _task_blocks(layout, state):
    slug = state.get("task")
    if not slug:
        return ["no active task — /ctx:task «goal» to start one"]
    path = layout.task_file(slug)
    doc = frontmatter.read(path)
    if doc is None:
        return [f"active task {slug} has no file at {layout.rel(path)}"]
    return _work_blocks(doc, layout.rel(path))


def _plan_blocks(layout, state):
    plan, unit = state.get("plan"), state.get("unit")
    if not plan:
        return _spec_blocks(layout, state)
    if not unit:
        return [f"plan {plan} · no unit dispatched · /ctx:status for the board"]
    path = layout.plans / plan / "units" / f"{unit}.md"
    doc = frontmatter.read(path)
    if doc is None:
        return [f"plan {plan} · unit file missing at {layout.rel(path)}"]
    blocks = _work_blocks(doc, layout.rel(path))
    owns = doc.meta.get("owns") or []
    if owns:
        blocks.insert(1, "owns (exclusive write scope): " + ", ".join(map(str, owns)))
    forbid = doc.meta.get("forbid") or []
    if forbid:
        blocks.insert(2, "must not touch: " + ", ".join(map(str, forbid)))
    return blocks


def _spec_blocks(layout, state):
    """At L2 before planning, the gate state is the useful thing to surface."""
    slug = state.get("spec")
    if not slug:
        return ["no active spec — /ctx:spec «intent» to start one"]
    ready, blocking = spec_mod.ready(layout, slug)
    if not ready:
        listed = "; ".join(blocking[:3])
        return [
            f"spec {slug} · BLOCKED on {len(blocking)} unanswered question(s)",
            f"ask before building: {listed}",
        ]
    return [f"spec {slug} · ready · /ctx:plan {slug} to decompose it into units"]


def _work_blocks(doc, rel_path):
    blocks = []
    objective = _first_paragraph(doc.section("objective", "goal"))
    if objective:
        blocks.append(f"objective: {objective}")
    criteria = doc.list_items("acceptance criteria", "criteria")
    if criteria:
        numbered = " ".join(f"({i}) {c}" for i, c in enumerate(criteria, 1))
        blocks.append("criteria: " + numbered)
    checks = _verify_summary(doc.meta.get("verify"))
    if checks:
        blocks.append("verify: " + checks)
    blocks.append(f"file: {rel_path}")
    return blocks


def _verify_summary(verify):
    if not isinstance(verify, list):
        return ""
    parts = []
    for entry in verify:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "?"))
        detail = entry.get("run") or entry.get("path") or ""
        parts.append(f"{kind}:{detail}" if detail else kind)
    return " · ".join(parts)


def _auto_load(layout, config, level):
    names = config.get("auto_load") or []
    if not names:
        return []
    if level == "0":
        return []  # names alone are not worth the characters at L0
    return [f"auto-loaded contexts: {', '.join(map(str, names))} (see .ctx/contexts/)"]


def _first_paragraph(text):
    for chunk in (text or "").split("\n\n"):
        cleaned = " ".join(chunk.split())
        if cleaned:
            return cleaned
    return ""


def _fit(blocks, cap):
    if cap <= 0:
        return ""
    out, used = [], 0
    for block in blocks:
        cost = len(block) + (1 if out else 0)
        if used + cost > cap:
            room = cap - used - len(TRUNCATED) - 1
            if room > 24:
                out.append(block[:room].rstrip() + TRUNCATED)
            elif out:
                out.append(TRUNCATED)
            break
        out.append(block)
        used += cost
    return "\n".join(out)
