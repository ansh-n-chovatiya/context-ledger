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

from . import plan as plan_mod, worktree as wt

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


def prepare_worktrees(layout, slug, units):
    """Create a worktree per session-tier unit. (rows, problems).

    Per the decision taken during design, this prepares the tree and hands back a
    command rather than driving a session headlessly — parallel writes are the
    place a human most wants to stay in the loop.
    """
    rows, problems = [], []
    session_units = [u for u in units if u.tier == "session"]
    if not session_units:
        return rows, problems

    problem = wt.check_repo(layout)
    if problem:
        return rows, [f"{problem} (units: " +
                      ", ".join(u.name for u in session_units) + ")"]

    for unit in session_units:
        path, branch, created, error = wt.create(layout, slug, unit.name)
        if error:
            problems.append(f"{unit.name}: {error}")
        else:
            rows.append((unit, path, branch, created))
    return rows, problems


def instructions(layout, slug, level, units, budget, worktrees=()):
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
        prepared = {unit.name: (path, branch, created) for unit, path, branch, created
                    in worktrees}
        lines += [
            f"## {len(sessions)} unit(s) write in their own worktree",
            "",
            "Each has an isolated checkout and branch, so their edits cannot collide "
            "and a unit that goes wrong is discarded by deleting a directory. Run each "
            "in its own terminal:",
            "",
        ]
        for unit in sessions:
            entry = prepared.get(unit.name)
            if entry is None:
                lines.append(
                    f"- `{unit.name}` — **worktree not prepared**; see the problems above"
                )
                continue
            path, branch, created = entry
            lines += [
                f"- `{unit.name}` on `{branch}` "
                f"({'created' if created else 'reusing existing worktree'})",
                f"  ```",
                f"  cd {path}",
                f"  ctx unit {unit.name}          # arms the done-gate for this unit",
                f"  claude   # then: execute the unit contract at {layout.rel(unit.path)}",
                f"  ```",
            ]
        lines += [
            "",
            "Commit inside the worktree when done, then from the main tree run "
            "`ctx merge <unit>` for each. That runs the done-gate in the unit's own "
            "worktree, refuses to merge anything that touched a path outside `owns`, "
            "and removes the worktree on success.",
            "",
        ]

    lines += [
        "## When each unit reports back",
        "",
        "1. Check the report against the unit's **Return contract** — files changed, "
        "criteria passed, verify output. A report missing any of those is incomplete; "
        "ask for the rest rather than assuming.",
        "2. If a unit says it had to change a published interface, **stop the wave**. "
        "That invalidates its siblings' assumptions and is a planning decision.",
        "3. Mark it: `ctx unit <name> --status done`. That runs the unit's own "
        "checks first and refuses if they do not pass — a report claiming success "
        "is not evidence of it. Pass `--force` only as a decision you say out loud.",
        "4. When the wave is clear, `ctx start` again for the next one.",
    ]
    return "\n".join(lines)
