"""Hook entry points. Every one fails open.

The scripts in `hooks/` are three-line shims over this module so that a change
to the hook contract touches one place, and so the logic is unit-testable
without a live session. Two invariants:

* **Silent in untracked projects.** No `.ctx/` means no output and no work. The
  plugin is installed globally, so this is what keeps it free everywhere else.
* **Never break a session.** Anything unexpected is appended to
  `.ctx/runtime/hook-errors.log` and the hook exits 0 — including a ledger this
  plugin is too old to read, which is a refusal `config.load` signals with
  `SystemExit` because the CLI needs it to stop a command. A hook has no command
  to stop, so it reports one line and contributes nothing. The done-gate is the
  only hook allowed to fail closed, and it fails closed on a *criterion*, never
  on a configuration.
"""

import datetime
import fnmatch
import json
import os
import re
import sys
import time
import traceback

from . import (
    briefing, bundle, config as config_mod, frontmatter, journal, log, paths,
    spec as spec_mod, state, telemetry, verify, work,
)


def main(event, stream=None, out=None):
    stream = stream or sys.stdin
    out = out or sys.stdout
    payload = _read_payload(stream)
    root = paths.ctx_dir(payload.get("cwd"))
    if root is None:
        return 0  # untracked project: contribute nothing
    layout = paths.Layout(root)
    started = time.perf_counter()
    try:
        try:
            config = config_mod.load(layout)
        except SystemExit as refusal:
            # The one condition the blanket fail-open below deliberately did not
            # cover, and the likeliest one to actually happen. A teammate commits
            # a ledger written by a newer plugin; `config.load` raises the CLI's
            # "stop the command" signal; `except SystemExit: raise` handed it to
            # the shim, which exited non-zero with a bare error line — on *every
            # tool call*, in a session that cannot act on it and did not ask.
            #
            # A hook is not a command: it has nothing to stop, and refusing to
            # run is the whole of what it can usefully do. So the refusal is
            # reported as a notice and the hook contributes nothing, exactly as
            # it does for every other failure here.
            #
            # Scoped to this one call on purpose. A `SystemExit` out of a
            # *handler* is not a config refusal, it is a bug, and the `raise`
            # below still lets it out rather than hiding it.
            _report_unusable_config(event, refusal)
            _measure(layout, event, started, failed=True)
            return 0
        handler = HANDLERS.get(event)
        if handler is None:
            return 0
        result = handler(layout, config, payload) or ""
        if isinstance(result, dict):
            # A decision object (the Stop gate). Exit 0; the JSON carries the verdict.
            json.dump(result, out)
            out.write("\n")
            _measure(layout, event, started, config, decision=result.get("decision"))
        else:
            if result:
                out.write(result if result.endswith("\n") else result + "\n")
            # `chars` is what the session actually paid, as opposed to what
            # `ctx doctor` predicts it would pay.
            _measure(layout, event, started, config,
                     chars=len(result) if result else 0)
        return 0
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - fail open, always
        _log_error(layout, event, traceback.format_exc())
        _measure(layout, event, started, failed=True)
        return 0


def _report_unusable_config(event, refusal):
    """One line of notice for a ledger this plugin cannot read. Never raises.

    Deliberately not deduplicated across events. Every hook event is its own
    process, so a cache would be empty on arrival and could only ever quieten a
    test suite — and a per-session store for a condition that is permanent until
    someone upgrades the plugin would outlive its usefulness immediately. One
    line per event, and never a traceback, is the bargain.
    """
    message = " ".join(str(refusal).split())
    if not message or message.lstrip("-").isdigit():
        # `SystemExit(2)` and `SystemExit()` stringify to "2" and "" — say
        # something a reader can act on rather than echoing an exit status.
        message = "this ledger could not be read by the installed plugin"
    try:
        print(
            f"ctx: {message} "
            f"(the {event} hook did nothing rather than failing your session)",
            file=sys.stderr,
        )
    except Exception as exc:  # pragma: no cover - a console that cannot take it
        log.failure("hooks.report_unusable_config", exc, event=event)


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


def _measure(layout, event, started, config=None, **fields):
    if config is not None and not telemetry.enabled(config):
        return
    telemetry.record(layout, event, (time.perf_counter() - started) * 1000, **fields)


# The log is append-only across the life of a project, and `ctx doctor` reads it.
# Without a bound, one bad afternoon leaves a megabyte of tracebacks that every
# doctor run re-reads; without a timestamp, doctor cannot tell a failure from
# this morning from one in March and had to treat both as a live problem.
ERROR_LOG_MAX_BYTES = 64 * 1024
ERROR_LOG_KEEP_LINES = 200


def _log_error(layout, event, detail):
    # Two destinations for one failure, and they answer different questions.
    # `hook-errors.log` is the durable record `ctx doctor` reads afterwards;
    # the diagnostic log is off unless somebody turned it on, and exists for
    # the support case where the answer is wanted *now*, on the terminal,
    # possibly because `hook-errors.log` is itself the thing that will not
    # write. Neither may raise: a hook that fails while reporting a failure is
    # the one outcome this whole module is built to prevent.
    log.error(f"hooks.{event}", detail)
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        _rotate_errors(layout.errors)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with layout.errors.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {stamp} {event} ---\n{detail}\n")
    except OSError as exc:
        # The durable half is gone: a read-only checkout or a full disk, and
        # `ctx doctor` will report nothing at all about this session.
        log.failure("hooks.log_error", exc, event=event, path=layout.errors)


def _rotate_errors(path):
    try:
        if not path.is_file() or path.stat().st_size <= ERROR_LOG_MAX_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(True)
        path.write_text("".join(lines[-ERROR_LOG_KEEP_LINES:]), encoding="utf-8")
    except OSError as exc:
        log.failure("hooks.rotate_errors", exc, path=path)


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
    targets = _edit_targets(payload)
    if not targets:
        return ""
    owns, forbid = _scope(layout, current)
    if not owns and not forbid:
        return ""
    unit = work.claim()[0] or current.get("unit")
    for target in targets:
        relative = _relative(layout, target)
        if _matches(relative, forbid):
            state.set_nudge(
                layout,
                f"{relative} is listed under `forbid` for unit {unit} — "
                "another unit owns it. Stop and report instead of editing.",
            )
            return ""
        if owns and not _matches(relative, owns):
            state.set_nudge(
                layout,
                f"{relative} is outside the `owns` scope of unit {unit} "
                f"({', '.join(owns)}). Editing it breaks the wave's isolation "
                "guarantee.",
            )
            return ""
    return ""


def on_post_tool_use(layout, config, payload):
    """Disk-only. Injects nothing, so journalling is free in context terms."""
    targets = _edit_targets(payload)
    if not targets:
        return ""
    current = state.load(layout)
    name = payload.get("tool_name")
    kind = "write" if name == "Write" else ("shell" if name == "Bash" else "edit")
    marker = work.claim()[0] or current.get("unit") or current.get("task")
    note = f"unit={marker}" if marker else ""
    for target in targets[:4]:  # a one-liner can touch several; cap the noise
        journal.append(layout, config, kind, _relative(layout, target), note)
    # A judged sign-off must not outlive the code it signed off on.
    item = work.active(layout, current)
    if item is not None and item.clear_recorded():
        journal.append(layout, config, "gate", item.key, "sign-off cleared by edit")
    if item is not None and item.status == "verify_failed":
        # Work that was escalated and has now been edited is being worked on
        # again, so the gate re-arms. Without this the status would be terminal
        # and the gate would never fire again for this item.
        item.set_status("pending")
        journal.append(layout, config, "gate", item.key, "re-armed by edit")
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
    # One definition of "off", one journal line, one place a policy can refuse
    # it. This site used to spell the test out inline — four spellings against
    # the CLI's three, and no record anywhere that the gate had been walked past.
    if config_mod.gate_override(layout, config, "stop"):
        return ""

    current = state.load(layout)
    if config_mod.normalise_level(current.get("level")) == "0":
        return ""  # L0 has no gate at all — that is what makes it free

    item = work.active(layout, current)
    if item is None or not verify.ordered(item.checks):
        return ""
    if item.status == "verify_failed":
        # The gate already escalated this work to the user and stood down. It
        # used to stand down only for one session: `attempts` was cleared on the
        # way out and nothing consulted the status afterwards, so the next Stop
        # started counting from one and blocked three more times, for ever. The
        # status is the memory that makes `max_attempts` a bound rather than a
        # cycle; an edit clears it again in `on_post_tool_use`.
        return ""

    results, verdict = verify.run(
        layout, config, item.checks, cwd=layout.root.parent, key=item.key,
        owns=item.owns, recorded=item.recorded, judged=False,
    )

    if verdict == verify.PASS:
        state.clear_attempts(layout, item.attempt_key)
        journal.append(layout, config, "gate", item.key, "pass")
        return ""
    if verdict == verify.ERROR:
        # A check could not run: a missing binary, an unaccepted command, no git
        # repository. That is infrastructure, not the work, and blocking on it
        # would brick every session in the project. It is journalled as
        # incomplete rather than as a pass — the gate did not sign anything.
        #
        # Deliberately *not* the same answer as `cli._gate_check`, which refuses
        # an all-ERROR `--status done`. The difference is what each one is
        # deciding: ending a turn is not a claim that the work is finished, so
        # unrunnable checks cost the session nothing; marking a unit `done` is
        # exactly that claim, and an ungated claim is the thing the gate exists
        # to refuse. Making this branch block too would brick every session in a
        # project whose toolchain is not installed, which is not an improvement.
        journal.append(
            layout, config, "gate", item.key,
            "incomplete (a check could not run); not blocking",
        )
        return ""

    limit = max(1, int(gate.get("max_attempts", 3)))
    attempts = state.bump_attempts(layout, item.attempt_key)
    journal.append(layout, config, "gate", item.key, f"{verdict} ({attempts}/{limit})")

    if attempts > limit:
        item.set_status("verify_failed")
        state.clear_attempts(layout, item.attempt_key)
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


# `SubagentStop` is deliberately absent. It used to run `on_stop`, which meant
# every subagent that finished — an exploratory search, the `verifier` itself,
# any unrelated Task — ran the project's whole verify suite and could be blocked
# `max_attempts` times against acceptance criteria it had never touched. Under a
# dispatched wave that was N concurrent test runs against one shared tree, all
# judging whichever unit the single `state.unit` pointer happened to hold.
#
# The gate belongs to the session that owns the work, so it fires on `Stop`.
HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_user_prompt_submit,
    "PreToolUse": on_pre_tool_use,
    "PostToolUse": on_post_tool_use,
    "PreCompact": on_pre_compact,
    "SessionEnd": on_session_end,
    "Stop": on_stop,
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

# Shell verbs that write. `owns` isolation is the reason a wave can run in
# parallel at all, and it used to be enforced only against the structured edit
# tools — so one `sed -i` walked straight past it, and the journal never saw the
# change either. Matching the shell is necessarily a heuristic, which is why the
# result is an advisory nudge and never a block; `verify`'s `diff` kind reads git
# and stays the authoritative answer.
_WRITE_VERB = re.compile(
    r"(?:^|[;&|]|&&|\|\|)\s*(?:sudo\s+|env\s+\S+=\S+\s+)*"
    r"(sed|tee|cp|mv|rm|dd|patch|truncate|install|rsync|ln|touch|mkdir|"
    r"chmod|chown|python3?|node|perl|awk)\b"
)
_GIT_WRITE = re.compile(r"\bgit\s+(checkout|restore|apply|clean|reset|rm|mv)\b")
_REDIRECT = re.compile(r"(?:^|[^>\d])>{1,2}\s*(?!&)([^\s;&|)]+)")
_HEREDOC_TO = re.compile(r">{1,2}\s*([^\s;&|)]+)\s*<<")
_PATHLIKE = re.compile(r"^[^\s\-][^\s]*[/.][^\s]*$")
# `s/a/b/`, `y|x|z|`, `1,3d` — a sed script, not a file. Without this the
# expression in `sed -i 's/a/b/' src/x.py` reads as a second path.
_SCRIPT_EXPR = re.compile(r"^[a-z]{1,2}[/|,#!]")


def _bash_targets(command):
    """Paths a shell command looks likely to write. Advisory, not exhaustive."""
    text = str(command or "")
    if not text:
        return []
    writes = bool(_WRITE_VERB.search(text)) or bool(_GIT_WRITE.search(text))
    redirected = _REDIRECT.findall(text) + _HEREDOC_TO.findall(text)
    if not writes and not redirected:
        return []

    found = list(redirected)
    if writes:
        for token in text.replace(";", " ").replace("|", " ").replace("&", " ").split():
            cleaned = token.strip("\"'`()")
            if cleaned.startswith("-") or "=" in cleaned.split("/")[0]:
                continue
            if _SCRIPT_EXPR.match(cleaned):
                continue
            if _PATHLIKE.match(cleaned):
                found.append(cleaned)
    out = []
    for path in found:
        cleaned = path.strip("\"'`")
        if cleaned and cleaned not in out and not cleaned.startswith(("/dev/", "http")):
            out.append(cleaned)
    return out


def _edit_targets(payload):
    """Every path this tool call appears to write. Empty means nothing to do."""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return []
    name = payload.get("tool_name")
    if name in _EDIT_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        return [target] if target else []
    if name == "Bash":
        return _bash_targets(tool_input.get("command"))
    return []


def _relative(layout, target):
    return layout.rel(target)


def _scope(layout, current):
    plan, unit = current.get("plan"), current.get("unit")
    if not plan or not unit:
        return [], []
    doc = frontmatter.read(layout.unit_file(plan, unit))
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
        if fnmatch.fnmatchcase(normalised, cleaned):
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
