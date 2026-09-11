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

from . import complexity as complexity_mod, config as config_mod, plan as plan_mod, worktree as wt

DISPATCHABLE = ("inline", "subagent")

# Positional index into `models.tiers` for each complexity band, cheapest tier
# first. `complexity.tier_for` only ever returns one of these three names, so
# the mapping itself is total — but `models.tiers` is not guaranteed to have
# three entries, which is why every lookup through this dict is clamped to
# `len(tiers) - 1` rather than indexed directly. Per ADR 0002: a project
# running a two-entry `models.tiers` must get `deep` clamped onto the dearer
# of the two, not an `IndexError`.
_BAND_INDEX = {"light": 0, "standard": 1, "deep": 2}

def _small_package_bytes(config):
    """`review.small_package_bytes`, config-first like every other threshold
    this module leans on (`models.tiers`, `models.<role>`) — a project must
    be able to retune this without editing code, same reasoning as keeping
    model names out of this module entirely. Lives under `review` rather
    than `complexity` in `config.DEFAULTS`: it is a byte count, not a
    complexity-score point, and burying it in the points block is how a
    later reader misreads both.
    """
    review = (config or {}).get("review") or {}
    return int(
        review.get("small_package_bytes")
        or config_mod.DEFAULTS["review"]["small_package_bytes"]
    )


def model_for(config, unit=None, role="runner", *, stats=None, round=1,
              siblings=None):
    """The model a dispatched role should run on.

    Precedence, highest first:

    1. The unit's own `model:` in its frontmatter. A unit that named its own
       model is immune to everything below — the author knew something the
       score does not, and a heuristic guessing louder than that is not a
       feature. This is also immune to `round`/`escalate_on_failed_round`:
       an intentional choice does not get escalated out from under the unit
       that made it.
    2. A score-derived tier, when `unit` is given — the runner's path. The
       unit that is about to be dispatched is exactly the one whose own
       stated scope `complexity.score` is built from, so `complexity.score`
       turns that frontmatter into a number, `complexity.tier_for` turns the
       number into a band (`light`/`standard`/`deep`), and `_BAND_INDEX` maps
       the band positionally onto `models.tiers` — light to the cheapest
       entry, deep to the dearest, clamped so a project running fewer than
       three tiers still gets *a* model instead of an `IndexError`.
    3. A package-driven tier, when `stats` is given instead — the reviewer's
       path. A reviewer reads what actually changed, after the work is
       already done, so there is no unit-scored "before" left to grade —
       only `review.dispatch_stats`' `bytes` and `out_of_scope`. Under
       `review.small_package_bytes` (`ctx.yaml`, defaulting to
       `config.DEFAULTS["review"]`) with no violations, the tier one below
       the floor is enough; otherwise the floor holds.
    4. `models.<role>` in `ctx.yaml` — the floor every role falls back to
       when neither of the above has anything to say (no `unit`, no `stats`,
       or a `models.<role>` that is not even in `models.tiers`, which a
       positional lookup has no way to place).
    5. The built-in default in `config.DEFAULTS["models"]`, when `ctx.yaml`
       does not set `models.<role>` at all.

    Named on every dispatch line for the same reason as always: an omitted
    model is not "the cheap one", it is whatever the orchestrating session
    happens to be running, which is the expensive one.

    `siblings` is passed straight through to `complexity.score`: the rest of
    the plan, for the one term that cannot be decided from this unit's own
    frontmatter. A caller that already has the plan loaded hands it over so the
    scoring does not re-read the directory once per unit; a caller that does
    not leaves it out and `score` finds them itself. Either way the tier is the
    same — the parameter is about how many times the disk is read, not about
    which model is picked.

    `round` and `models.escalate_on_failed_round` apply last, on top of
    whichever tier steps 2-5 picked. Off — the default — every round of a
    unit dispatches at the same model: the working assumption when the flag
    is off is that a retry is worth trying flat before it is worth trying
    dearer. On, each round past the first walks `config.tier_up` once per
    round past the first: round 2 is one tier up from round 1, round 3 one
    more again, stopping at whatever tier `tier_up` can still reach rather
    than raising past the dearest one.
    """
    declared = getattr(unit, "model", "") if unit is not None else ""
    if declared:
        return declared

    models = (config or {}).get("models") or {}
    tiers = models.get("tiers") or config_mod.DEFAULTS["models"]["tiers"]
    floor = str(models.get(role) or config_mod.DEFAULTS["models"][role])

    picked = floor
    if unit is not None:
        score, _breakdown = complexity_mod.score(config, unit, siblings)
        band = complexity_mod.tier_for(config, score)
        index = min(_BAND_INDEX[band], len(tiers) - 1)
        picked = tiers[index]
    elif stats is not None:
        small = stats.get("bytes", 0) <= _small_package_bytes(config)
        clean = not stats.get("out_of_scope", 0)
        if small and clean and floor in tiers:
            picked = tiers[max(0, tiers.index(floor) - 1)]

    if round > 1 and models.get("escalate_on_failed_round", False):
        for _ in range(round - 1):
            picked = config_mod.tier_up(config, picked)

    return picked


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
    # Width, checked separately from cost, because they fail differently: a
    # wave can sit well inside its token budget and still be too wide for one
    # session to orchestrate, and the budget cap above cannot see that. Both
    # caps count the same list — units not yet `done` — so a wide wave stays
    # workable-off incrementally rather than refusing forever once it is
    # authored. Same vocabulary as the budget refusal on purpose: two caps
    # that refuse in two dialects are two things to learn.
    unit_cap = int((config.get("plan") or {}).get("max_wave_units", 0) or 0)
    if unit_cap and len(units) > unit_cap:
        problems.append(
            f"wave {level} has {len(units)} unit(s) against a cap of {unit_cap} "
            "— split the wave or raise plan.max_wave_units in ctx.yaml"
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
        # Loaded once for the whole block rather than per unit:
        # `complexity.score` needs the plan around a unit to decide whether
        # anything consumes its interface, and `units` here is one wave with
        # the finished units already dropped — a consumer is in a *later* wave
        # by definition, so the wave on its own can never answer the question.
        siblings = plan_mod.load_units(layout, slug)
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
            score, breakdown = complexity_mod.score(config, unit, siblings)
            tier = complexity_mod.tier_for(config, score)
            detail = ", ".join(f"{label}={points}" for label, points in breakdown)
            lines.append(
                f"- `{unit.name}` → unit-runner on "
                f"**{model_for(config, unit, siblings=siblings)}** "
                f"(score {score} = {detail or 'no signals'} → {tier}): "
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
