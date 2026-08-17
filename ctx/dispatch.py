"""Turning a validated wave into dispatch instructions.

This module produces text for the orchestrating session to act on. It
deliberately does not spawn anything itself: what to hand a subagent is a
decision the harness makes, and a Python script pretending to drive it would
just be a worse version of the Task tool.

The one rule it keeps repeating is orchestrator discipline — the session running
a wave reads unit files and unit reports, never source. That is what keeps its
context flat across a twenty-unit plan, and it is only enforceable because the
plan already declares who owns what.
"""

from . import plan as plan_mod

DISPATCHABLE = ("inline", "subagent")


def prepare(layout, config, slug, level=None):
    """(level, units, problems, budget). Problems mean nothing may be dispatched."""
    grouped, problems = plan_mod.check(layout, slug)
    if problems:
        return None, [], problems, 0

    if level is None:
        level = plan_mod.next_wave(layout, slug)
    if level is None:
        return None, [], ["plan is complete — every unit is done"], 0
    if level not in grouped:
        return level, [], [f"no wave {level} in this plan"], 0

    units = [u for u in grouped[level] if u.status != "done"]
    budget = sum(unit.budget for unit in units)
    cap = int((config.get("plan") or {}).get("wave_budget_tokens", 0) or 0)
    problems = []
    if cap and budget > cap:
        problems.append(
            f"wave {level} budget is {budget:,} tokens against a cap of {cap:,} "
            "— split the wave or raise plan.wave_budget_tokens in ctx.yaml"
        )
    return level, units, problems, budget


def instructions(layout, slug, level, units, budget):
    """The dispatch brief. Read by the orchestrator, not by the units."""
    lines = [
        f"# Wave {level} of plan `{slug}` — {len(units)} unit(s), "
        f"~{budget:,} token budget",
        "",
        "Ownership is disjoint and no unit reads what a sibling rewrites, so these "
        "may run concurrently.",
        "",
        "**Your discipline as orchestrator: do not read source files.** Read unit "
        "files and unit reports only. That is what keeps this session's context flat "
        "no matter how large the plan is.",
        "",
    ]

    concurrent = [u for u in units if u.tier == "subagent"]
    inline = [u for u in units if u.tier == "inline"]
    sessions = [u for u in units if u.tier == "session"]

    if concurrent:
        lines += [
            f"## Dispatch these {len(concurrent)} concurrently",
            "",
            "Send them in a **single message with multiple Task calls** so they run in "
            "parallel. Use the `unit-runner` agent. Each prompt needs only the path — "
            "the unit file is self-contained by construction:",
            "",
        ]
        for unit in concurrent:
            lines.append(
                f"- `{unit.name}` → unit-runner: "
                f"\"Execute the unit contract at {layout.rel(unit.path)}\""
            )
        lines.append("")

    if inline:
        lines += [f"## Work these {len(inline)} here, in this session", ""]
        for unit in inline:
            lines.append(
                f"- `{unit.name}` — run `ctx unit {unit.name}` first so the done-gate "
                f"applies, then work {layout.rel(unit.path)}"
            )
        lines.append("")

    if sessions:
        lines += [
            f"## {len(sessions)} unit(s) need a separate writing session",
            "",
            "The git-worktree tier is not built yet (phase 6). For now either change "
            "`tier: subagent` in the unit file, or run each in its own `claude` session "
            "from a worktree you create by hand:",
            "",
        ]
        for unit in sessions:
            branch = f"ctx/{slug}/{unit.name}"
            lines += [
                f"- `{unit.name}`",
                f"  - `git worktree add -b {branch} .ctx/runtime/worktrees/{unit.name} HEAD`",
                f"  - then in that directory: `ctx unit {unit.name}` and work "
                f"{layout.rel(unit.path)}",
            ]
        lines.append("")

    lines += [
        "## When each unit reports back",
        "",
        "1. Check the report against the unit's **Return contract** — files changed, "
        "criteria passed, verify output. A report missing any of those is incomplete; "
        "ask for the rest rather than assuming.",
        "2. If a unit says it had to change a published interface, **stop the wave**. "
        "That invalidates its siblings' assumptions and is a planning decision.",
        "3. Mark it: `ctx unit <name> --status done`.",
        "4. When the wave is clear, `ctx start` again for the next one.",
    ]
    return "\n".join(lines)
