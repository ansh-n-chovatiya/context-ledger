"""Hook entry points. Every one fails open.

The scripts in `hooks/` are three-line shims over this module so that a change
to the hook contract touches one place, and so the logic is unit-testable
without a live session. Two invariants:

* **Silent in untracked projects.** No `.ctx/` means no output and no work. The
  plugin is installed globally, so this is what keeps it free everywhere else.
* **Never break a session.** Anything unexpected is appended to
  `.ctx/runtime/hook-errors.log` and the hook exits 0. The done-gate is the
  only hook allowed to fail closed, and it is not registered until the gate
  ships.
"""

import fnmatch
import json
import os
import sys
import traceback

from . import (
    briefing, bundle, config as config_mod, frontmatter, journal, paths,
    spec as spec_mod, state, verify, work,
)


def main(event, stream=None, out=None):
    stream = stream or sys.stdin
    out = out or sys.stdout
    payload = _read_payload(stream)
    root = paths.ctx_dir(payload.get("cwd"))
    if root is None:
        return 0  # untracked project: contribute nothing
    layout = paths.Layout(root)
    try:
        config = config_mod.load(layout)
        handler = HANDLERS.get(event)
        if handler is None:
            return 0
        result = handler(layout, config, payload) or ""
        if isinstance(result, dict):
            # A decision object (the Stop gate). Exit 0; the JSON carries the verdict.
            json.dump(result, out)
            out.write("\n")
        elif result:
            out.write(result if result.endswith("\n") else result + "\n")
        return 0
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - fail open, always
        _log_error(layout, event, traceback.format_exc())
        return 0


def _read_payload(stream):
    try:
        raw = stream.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _log_error(layout, event, detail):
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        with layout.errors.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {event} ---\n{detail}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

def on_session_start(layout, config, payload):
    """The one recurring token cost. Budgeted and deterministic — see briefing."""
    current = state.load(layout)
    session = payload.get("session_id")
    if session and session != current.get("last_session"):
        state.update(layout, last_session=session)
        current["last_session"] = session
    return briefing.build(layout, config, current)


def on_user_prompt_submit(layout, config, payload):
    """Silent unless drift was detected. This is why steady-state cost is zero."""
    message = state.take_nudge(layout)
    if not message:
        return ""
    return f"[ctx] {message}"


def on_pre_tool_use(layout, config, payload):
    """Queue a nudge when an edit strays outside the active unit's write scope."""
    current = state.load(layout)
    if config_mod.normalise_level(current.get("level")) != "2":
        return ""
    target = _edit_target(payload)
    if not target:
        return ""
    owns, forbid = _scope(layout, current)
    if not owns and not forbid:
        return ""
    relative = _relative(layout, target)
    if _matches(relative, forbid):
        state.set_nudge(
            layout,
            f"{relative} is listed under `forbid` for unit {current.get('unit')} — "
            "another unit owns it. Stop and report instead of editing.",
        )
    elif owns and not _matches(relative, owns):
        state.set_nudge(
            layout,
            f"{relative} is outside the `owns` scope of unit {current.get('unit')} "
            f"({', '.join(owns)}). Editing it breaks the wave's isolation guarantee.",
        )
    return ""


def on_post_tool_use(layout, config, payload):
    """Disk-only. Injects nothing, so journalling is free in context terms."""
    target = _edit_target(payload)
    if not target:
        return ""
    current = state.load(layout)
    kind = "write" if payload.get("tool_name") == "Write" else "edit"
    marker = current.get("unit") or current.get("task")
    journal.append(
        layout, config, kind, _relative(layout, target),
        f"unit={marker}" if current.get("unit") else (f"task={marker}" if marker else ""),
    )
    # A judged sign-off must not outlive the code it signed off on.
    item = work.active(layout, current)
    if item is not None and item.clear_recorded():
        journal.append(layout, config, "gate", item.key, "sign-off cleared by edit")
    return ""


def on_pre_compact(layout, config, payload):
    """Flush before history is lost. Mechanical only — no model call, no tokens."""
    journal.append(layout, config, "compact", "session", "state flushed")
    journal.write_digest(layout, config)
    _autosave(layout, config, payload, reason="pre-compact")
    return ""


def on_session_end(layout, config, payload):
    journal.append(layout, config, "session", "end", str(payload.get("reason") or ""))
    journal.write_digest(layout, config)
    return ""


def on_stop(layout, config, payload):
    """The done-gate — the only hook that fails closed, and the only one that
    can refuse to let a session end.

    It blocks on a *criterion* failure, never on a configuration failure, and it
    is bounded: after `gate.max_attempts` blocks it stops, marks the work
    `verify_failed`, and escalates to the user rather than grinding.
    """
    gate = config.get("gate") or {}
    if not gate.get("enabled", True):
        return ""
    if os.environ.get("CTX_GATE", "").lower() in ("off", "0", "false", "disabled"):
        return ""

    current = state.load(layout)
    if config_mod.normalise_level(current.get("level")) == "0":
        return ""  # L0 has no gate at all — that is what makes it free

    item = work.active(layout, current)
    if item is None or not verify.ordered(item.checks):
        return ""

    results, verdict = verify.run(
        layout, config, item.checks, cwd=layout.root.parent, key=item.key,
        owns=item.owns, recorded=item.recorded, judged=False,
    )

    if verdict == verify.PASS:
        state.clear_attempts(layout, item.key)
        journal.append(layout, config, "gate", item.key, "pass")
        return ""
    if verdict == verify.ERROR:
        # Nothing ran. Blocking here would brick every session in the project.
        journal.append(layout, config, "gate", item.key, "not run (config); passing")
        return ""

    limit = max(1, int(gate.get("max_attempts", 3)))
    attempts = state.bump_attempts(layout, item.key)
    journal.append(layout, config, "gate", item.key, f"{verdict} ({attempts}/{limit})")

    if attempts > limit:
        item.set_status("verify_failed")
        state.clear_attempts(layout, item.key)
        state.set_nudge(
            layout,
            f"the done-gate for {item.key} failed {limit} times and has stopped "
            "blocking. Do not keep retrying — tell the user what failed, what you "
            "tried, and what you think is actually wrong.",
        )
        journal.append(layout, config, "gate", item.key, "escalated to user")
        return ""

    return {"decision": "block", "reason": _gate_reason(item, results, attempts, limit)}


def _gate_reason(item, results, attempts, limit):
    lines = [
        f"The done-gate blocked completion of `{item.key}` "
        f"(attempt {attempts} of {limit}).",
        "",
        verify.summarise(results),
    ]
    criteria = item.criteria[:6]
    if criteria:
        lines += ["", "Acceptance criteria:"]
        lines += [f"  {i}. {text}" for i, text in enumerate(criteria, 1)]
    lines += [
        "",
        f"Fix what failed, then finish. After {limit} attempts the gate stops and "
        "escalates to the user, so do not guess repeatedly — if the criterion or "
        "the check itself looks wrong, say so.",
    ]
    return "\n".join(lines)


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_user_prompt_submit,
    "PreToolUse": on_pre_tool_use,
    "PostToolUse": on_post_tool_use,
    "PreCompact": on_pre_compact,
    "SessionEnd": on_session_end,
    "Stop": on_stop,
    "SubagentStop": on_stop,
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _edit_target(payload):
    if payload.get("tool_name") not in _EDIT_TOOLS:
        return ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")


def _relative(layout, target):
    return layout.rel(target)


def _scope(layout, current):
    plan, unit = current.get("plan"), current.get("unit")
    if not plan or not unit:
        return [], []
    doc = frontmatter.read(layout.plans / plan / "units" / f"{unit}.md")
    if doc is None:
        return [], []
    owns = [str(p) for p in (doc.meta.get("owns") or [])]
    forbid = [str(p) for p in (doc.meta.get("forbid") or [])]
    return owns, forbid


def _matches(relative, patterns):
    normalised = relative.replace(os.sep, "/")
    for pattern in patterns:
        cleaned = str(pattern).replace(os.sep, "/").rstrip("/")
        if not cleaned:
            continue
        if fnmatch.fnmatch(normalised, cleaned):
            return True
        if normalised == cleaned or normalised.startswith(cleaned + "/"):
            return True
    return False


def _autosave(layout, config, payload, reason):
    """A mechanical snapshot: enough to resume, produced without inference.

    A semantic bundle needs a model, which would make compaction cost tokens at
    exactly the wrong moment. `/ctx:save` is the semantic path; this is the
    safety net for a compaction you did not ask for.
    """
    session = str(payload.get("session_id") or "session")[:8]
    current = state.load(layout)
    entries, earlier = journal.tail(layout, 15)
    recent = journal.recent_paths(layout, 8)
    level = config_mod.normalise_level(current.get("level"))

    body = [
        f"# Context — autosave {session}",
        "",
        "## Situation",
        f"Mechanical snapshot written at {reason}. Level L{level} "
        f"({config_mod.LEVEL_NAMES[level]}).",
        f"Active task: {current.get('task') or 'none'}. "
        f"Active plan/unit: {current.get('plan') or 'none'}/{current.get('unit') or 'none'}.",
        "",
        "## Established facts",
        "<!-- not inferred: this snapshot records what happened, not what it meant -->",
    ]
    body += [f"- touched `{path}`" for path in recent] or ["- no file changes recorded"]
    body += ["", "## Decisions made", "_see .ctx/decisions/_", "", "## Open questions", ""]
    body += ["", "## Constraints", "", "## Artifacts"]
    body += [f"- journal: `{layout.rel(layout.digest)}`"]
    if entries:
        body += ["", "```", *entries, "```"]
        if earlier:
            body.append(f"_{earlier} earlier entries in .ctx/journal/_")
    body += ["", "## Resume here", "_run /ctx:resume, then /ctx:save to replace this with a real bundle_"]

    # Name has no leading underscore on purpose: slugify would strip it, and a
    # filename that disagrees with the code that wrote it is a debugging trap.
    return bundle.save(
        layout, f"autosave-{session}", "\n".join(body), config=config
    )
