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

from . import config as config_mod, plan as plan_mod, worktree as wt

DISPATCHABLE = ("inline", "subagent")


def model_for(config, unit=None, role="runner"):
    """The model a dispatched role should run on.

    A unit's own `model:` wins, then `models.<role>` in ctx.yaml, then the
    built-in default. This exists so the dispatch brief can name a model on every
    line: an omitted model is not "the cheap one", it is whatever the
    orchestrating session happens to be running, which is the expensive one.
    """
    declared = getattr(unit, "model", "") if unit is not None else ""
    if declared:
        return declared
    models = (config or {}).get("models") or {}
    return str(models.get(role) or config_mod.DEFAULTS["models"][role])


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


def instructions(layout, config, slug, level, units, budget, worktrees=(),
                 worktree_requested=False):
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
            "parallel. Use the `unit-runner` agent, and pass the model named on each "
            "line. Each prompt needs only the path — the unit file is self-contained "
            "by construction:",
            "",
        ]
        for unit in concurrent:
            lines.append(
                f"- `{unit.name}` → unit-runner on **{model_for(config, unit)}**: "
                f"\"Execute the unit contract at {layout.rel(unit.path)}\""
            )
        lines += [
            "",
            "Name the model on every call. A Task call that omits it inherits this "
            "session's model — the most expensive one available — for work already "
            "budgeted for a cheaper seat. Override per unit with `model:` in its "
            "frontmatter when the work genuinely needs more.",
            "",
        ]

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
        lines += _session_block(layout, slug, sessions, prepared,
                                worktree_requested)

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


def _session_block(layout, slug, sessions, prepared, requested):
    """Instructions for tier `session` units, in the main tree or in a worktree.

    The main tree is the default. A worktree holds its branch exclusively, so
    while one exists `git checkout` of that branch here is refused — creating
    them unasked took away the tree the user actually tests in, which is the
    failure this tier was supposed to avoid.

    `requested` separates "you did not ask for worktrees" from "you asked and
    none could be made". Without it a `--worktree` run in a directory git
    refuses to work in printed main-tree instructions as though nothing had been
    asked for, burying the reason in a warning further up.
    """
    if not requested:
        lines = [
            f"## {len(sessions)} unit(s) are tier `session` — run them in this tree",
            "",
            "No worktree was created and no branch was made: `ctx start` leaves git "
            "alone unless asked. Work these one at a time, right here, and test the "
            "way you normally test:",
            "",
        ]
        for unit in sessions:
            lines += [
                f"- `{unit.name}` — {layout.rel(unit.path)}",
                "  ```",
                f"  export CTX_PLAN={slug} CTX_UNIT={unit.name}   # claims this unit "
                "for this terminal only",
                f"  ctx unit {unit.name}          # arms the done-gate for this unit",
                "  ```",
            ]
        lines += [
            "",
            "`ctx start --worktree` instead gives each unit its own temporary "
            "checkout and branch. That buys isolation and costs you this tree: while "
            "a worktree holds a branch, git refuses to check it out here, so you "
            "would test inside the worktree rather than in the main tree.",
            "",
        ]
        return lines

    if not prepared:
        return [
            f"## {len(sessions)} unit(s) asked for a worktree and did not get one",
            "",
            "**Worktree not prepared** — the reason is in the warnings above. Fix it, "
            "or dispatch without `--worktree` to work these in this tree instead:",
            "",
        ] + [f"- `{unit.name}` — {layout.rel(unit.path)}" for unit in sessions] + [""]

    lines = [
        f"## {len(sessions)} unit(s) write in their own worktree",
        "",
        "Each has an isolated checkout and branch, so their edits cannot collide "
        "and a unit that goes wrong is discarded by deleting a directory. Each "
        "branch is held by its worktree until it is merged or removed, so test "
        "inside the worktree rather than here. Run each in its own terminal:",
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
            "  ```",
            f"  cd {path}",
            f"  export CTX_PLAN={slug} CTX_UNIT={unit.name}   # claims this unit "
            "for this terminal only",
            f"  ctx unit {unit.name}          # arms the done-gate for this unit",
            f"  claude   # then: execute the unit contract at {layout.rel(unit.path)}",
            "  ```",
        ]
    lines += [
        "",
        "Commit inside the worktree when done, then from the main tree run "
        "`ctx merge <unit>` for each. That runs the done-gate in the unit's own "
        "worktree, refuses to merge anything that touched a path outside `owns`, "
        "and removes the worktree and its branch on success — which is what gives "
        "this tree the branch back.",
        "",
    ]
    return lines
