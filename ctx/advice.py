"""The product's decision tree: given a ledger, the one thing worth doing next.

`ctx next` is the only command that answers a question nobody typed the
arguments for. Everything it reads was already on disk and already decidable —
the schema version, the trust store, the level, the active spec or plan or
task, the wave in flight — it was just spread across `status`, `ask`,
`plan-check` and `start`, so using the tool meant knowing which level you were
at and which command that level implied.

That makes this the most product-shaped code in the repository and, until it
moved here, the least testable: it sat in the middle of the command layer, so
exercising one branch of it meant standing up an argparse namespace and a
command. Nothing here takes an `args`, prints, or exits. `next_action` is a
pure function of (layout, config, state) returning `(command, why)`, which is
what lets a test walk every branch it decides between directly.
"""

from . import (
    config as config_mod, journal, migrate as migrate_mod, plan as plan_mod,
    spec as spec_mod, state as state_mod, trust as trust_mod, work,
)


def wave_in_flight(layout, slug, wave):
    """(dispatched, waiting) — names of a wave's `running` and `pending` units.

    Both lists are needed to tell "this wave has not been started" from "this
    wave is out and nobody has reported back yet". Advising `/ctx:start` for
    the second is a loop: the wave is dispatched, nothing about running it
    again changes anything, and the answer never stops being the same command.
    """
    if not wave:
        return [], []
    grouped, problems = plan_mod.check(layout, slug)
    if problems or wave not in grouped:
        return [], []
    members = [unit for unit in grouped[wave] if unit.status != "done"]
    return ([unit.name for unit in members if unit.status == "running"],
            [unit.name for unit in members if unit.status == "pending"])


def next_action(layout, config, state=None):
    """(command, why). The one thing worth doing, from state alone.

    `state` is the already-loaded `state.load(layout)` mapping, for a caller
    that has one in hand; omitted, it is read here. It is an argument rather
    than always a read because a test that wants to ask "what would ctx advise
    at L1 with three blocked attempts?" should be able to say so in one dict
    instead of writing a ledger to disk to imply it.
    """
    current = state_mod.load(layout) if state is None else state
    level = config_mod.normalise_level(current.get("level"))

    behind, ahead = migrate_mod.pending(layout)
    if ahead:
        return "ctx migrate", "ledger files are newer than this plugin — upgrade it"
    if behind:
        return "ctx migrate", f"{len(behind)} ledger file(s) are on an older schema"

    accepted = trust_mod.load(layout)
    pending = [c for c, _s in trust_mod.declared(layout, config)
               if not trust_mod.is_accepted(c, accepted)]
    if pending:
        return "ctx trust", (
            f"{len(pending)} verify command(s) will not run until this machine "
            "accepts them"
        )

    if level == "2":
        spec = current.get("spec")
        plan = current.get("plan")
        if spec and not plan:
            ready, blocking = spec_mod.ready(layout, spec)
            if not ready:
                return "/ctx:ask", (
                    f"spec {spec} has {len(blocking)} unanswered blocking "
                    "question(s); planning around them is the failure this exists "
                    "to prevent"
                )
            return f"/ctx:plan {spec}", f"spec {spec} is ready to decompose"
        if plan:
            _grouped, problems = plan_mod.check(layout, plan)
            if problems:
                return "/ctx:doctor", (
                    f"plan {plan} has {len(problems)} problem(s); nothing "
                    "dispatches until they are fixed — `ctx plan-check` names them"
                )
            unit = work.claim()[0] or current.get("unit")
            if unit:
                return "/ctx:verify", f"unit {unit} is in progress — run its gate"
            wave = plan_mod.next_wave(layout, plan)
            if wave:
                # A wave whose units are all `running` has been dispatched
                # already. Saying `/ctx:start` again would be advice that never
                # changes anything and never stops being given — and it used to
                # cost more than a wasted command, because a second `ctx start`
                # overwrote the very baselines the review needed.
                dispatched, waiting = wave_in_flight(layout, plan, wave)
                if dispatched and not waiting:
                    return f"/ctx:review {dispatched[0]}", (
                        f"wave {wave} is in flight — {len(dispatched)} unit(s) "
                        f"dispatched and not yet done ({', '.join(dispatched)}); "
                        "review what came back instead of dispatching again"
                    )
                return "/ctx:start", f"plan {plan} has wave {wave} ready to dispatch"
            return "/ctx:handoff", f"plan {plan} is complete — write the resume packet"
        return "/ctx:spec", "at L2 with nothing active"

    if level == "1":
        task = current.get("task")
        if not task:
            return "/ctx:task", "at L1 with no task file"
        item = work.active(layout, current)
        if item is None:
            return "/ctx:task", f"task {task} has no file on disk"
        attempts = (current.get("attempts") or {}).get(item.attempt_key, 0)
        if attempts:
            return "/ctx:verify", (
                f"the gate has blocked {task} {attempts} time(s) — see what is failing"
            )
        return "/ctx:verify", f"task {task} is active — run its gate when you are done"

    recent = journal.recent_paths(layout, 3)
    if recent:
        return "/ctx:resume", "at L0 with recent work — pick up where you left off"
    return "/ctx:task «goal»", (
        "at L0 with nothing recorded. Stay here for anything you could finish in "
        "one sitting; escalate only when criteria are worth writing down"
    )
