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

from . import bundle, config as config_mod, frontmatter, journal, spec as spec_mod

TRUNCATED = "…[briefing truncated]"


def build(layout, config, state):
    return _render(layout, config, state)[2]


def measure(layout, config, state):
    """Used by `ctx doctor` so the budget is observable, not aspirational."""
    level, cap, text, dropped = _render(layout, config, state)
    return {
        "level": level,
        "chars": len(text),
        "cap": cap,
        "approx_tokens": round(len(text) / 3.6),
        # `_fit` clamps to the cap, so overflow is impossible and reporting it
        # would be a tautology. Truncation is the signal worth acting on: it
        # means state a session needed was dropped to fit.
        #
        # Reported by `_fit` rather than by looking for the marker in the
        # text. The marker needs room to be printed, so a cap too small for
        # even one block used to produce an empty briefing that claimed
        # nothing had been dropped - the one case where a caller most needs to
        # know. A block whose own content happened to contain the marker would
        # have been read the other way round.
        "truncated": dropped,
        "text": text,
    }


def _render(layout, config, state):
    """`(level, cap, text, dropped)` — everything both callers above need."""
    level = config_mod.normalise_level(state.get("level"))
    cap = config_mod.briefing_cap(config, level)
    blocks = _blocks(layout, config, state, level)
    text, dropped = _fit(blocks, cap)
    return level, cap, text, dropped


def _blocks(layout, config, state, level):
    out = [_headline(layout, state, level)]
    if level == "1":
        out.extend(_task_blocks(layout, state))
    elif level == "2":
        out.extend(_plan_blocks(layout, state))
    recent = journal.recent_paths(layout, 4)
    if recent and level != "0":
        out.append("recent: " + ", ".join(recent))
    if level == "0":
        out.append("/ctx:resume for detail · /ctx:task «goal» to track a change")
    out.extend(_auto_load(layout, config, level))
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
    path = layout.unit_file(plan, unit)
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


# Sections worth carrying into every session. `Situation` and `Resume here` are
# about one piece of work, so they are noise as standing context; constraints and
# verified facts are the parts that stay true.
STANDING_SECTIONS = ("constraints", "established facts")


def _auto_load(layout, config, level):
    """Inject the content of bundles named in `auto_load` — house conventions.

    Emitted last, so the cap truncates these before it drops active work. At L0
    the 220-char cap leaves almost no room; that shows up as truncation in
    `ctx doctor` rather than silently doing nothing, and raising
    `briefing_chars.l0` is the deliberate way to make space.
    """
    out = []
    for name in config.get("auto_load") or []:
        path = bundle.resolve(layout, str(name))
        if path is None:
            out.append(f"auto_load: no context named {str(name)!r} — check ctx.yaml")
            continue
        doc = frontmatter.read(path)
        if doc is None:
            continue
        text = _standing_content(doc)
        if text:
            out.append(f"[{name}] {text}")
    return out


def _standing_content(doc):
    parts = []
    for heading in STANDING_SECTIONS:
        body = doc.section(heading)
        if not body:
            continue
        cleaned = " ".join(
            line.strip().lstrip("-*").strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        )
        if cleaned:
            parts.append(f"{heading}: {cleaned}")
    return " · ".join(parts)


def _first_paragraph(text):
    for chunk in (text or "").split("\n\n"):
        cleaned = " ".join(chunk.split())
        if cleaned:
            return cleaned
    return ""


def _fit(blocks, cap):
    """`(text, dropped)` for `blocks` inside `cap` characters.

    `dropped` is the answer to "did a session lose something it was meant to
    have", and it is returned rather than inferred because the marker is not
    always affordable: at a cap under its own 21 characters there is no room
    to say anything at all, and an empty string is exactly what a project with
    nothing to report produces.

    `cap <= 0` is that other case and is never a truncation. It is L0's
    documented "no briefing" setting, reached by putting `briefing_chars.l0:
    0` in `ctx.yaml`; treating it as a truncation would make `ctx doctor` and
    `ctx ci` fail every project that had deliberately turned the briefing off.
    """
    if cap <= 0:
        return "", False
    out, used, dropped = [], 0, False
    for block in blocks:
        cost = len(block) + (1 if out else 0)
        if used + cost > cap:
            dropped = True
            room = cap - used - len(TRUNCATED) - (1 if out else 0)
            if room > 24:
                out.append(block[:room].rstrip() + TRUNCATED)
            elif room >= 0:
                # Too little of the block to be worth keeping, but enough
                # space to say so. Previously this needed `out` to be
                # non-empty, so a cap that fitted neither the first block nor
                # a trimmed head of it returned an empty briefing.
                out.append(TRUNCATED)
            # `room < 0` leaves no room for the marker either, and no block is
            # given back to make some: the blocks are in priority order, so
            # dropping the headline to print "truncated" would cost the reader
            # more than it told them. `dropped` carries it instead.
            break
        out.append(block)
        used += cost
    return "\n".join(out), dropped
