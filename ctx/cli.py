"""`python -m ctx …` — everything the slash commands and CI call.

Anything that can be decided without inference lives here rather than in a
prompt: status boards, collision checks, digests, scope checks and budget
measurement all cost zero tokens when they run as code.
"""

import argparse
import collections
import contextlib
import io
import json
import sys
import traceback
import types

# Imported for its side effect on this module's namespace, and for nothing
# else this file calls: two suites reach for `cli.subprocess.run` to intercept
# every shell-out the command layer makes. It is the one `ctx.commands` runs
# through — there is only one `subprocess` module — so patching it here still
# reaches the bodies that moved.
import subprocess  # noqa: F401

from . import (
    __version__, commands as commands_mod, config as config_mod,
    findings as findings_mod, plan as plan_mod, verify,
    # `cli.review_mod` is a patch point for the review baseline suite. Same
    # module object either way; the name has to keep resolving here.
    review as review_mod,  # noqa: F401
)

# The command layer, re-exported. Three reasons a name is on this list, and
# `_CommandLayer` at the foot of this file is the fourth:
#
#   * `commands()` builds each registry row from a bare `cmd_*` name, looked
#     up in *this* module's globals at build time — that is what lets a test
#     replace one and still reach the parser.
#   * `main` and `_finish` need the advisory list, the render holder and the
#     four printing/flag helpers. `_ADVISED` and `_RENDER` are shared by
#     identity, not by value: `main` mutates the same objects in place.
#   * the rest are private spellings four audit suites pin by name, including
#     unit 04's six one-line detector/advice aliases. They were reachable as
#     `cli.<name>` before the move and they still are.
from .commands import (  # noqa: F401
    cmd_ask, cmd_briefing, cmd_budget, cmd_ci, cmd_decide, cmd_digest,
    cmd_doctor, cmd_drop, cmd_escalate, cmd_findings, cmd_handoff, cmd_init,
    cmd_journal, cmd_level, cmd_list, cmd_load, cmd_merge, cmd_migrate,
    cmd_next, cmd_phase, cmd_plan, cmd_plan_check, cmd_plan_unit,
    cmd_promote, cmd_prune, cmd_question, cmd_resolve, cmd_resume,
    cmd_review, cmd_save, cmd_snapshot, cmd_spec, cmd_spec_ready, cmd_start,
    cmd_status, cmd_task, cmd_telemetry, cmd_trust, cmd_unit, cmd_verify,
    cmd_worktree, ADVISORY, DIGEST_IGNORE_LINE, _ADVISED, _PLAN_ARGUMENT,
    _RENDER, _advise, _availability, _detect_profile, _echo, _emit,
    _env_flag, _next_action, _plan_or_report, _python_exe,
    _recent_hook_errors, _runnable, _set_unit_status, _strict,
    _verify_candidates, _warn, duplicate_adrs,
)


# --------------------------------------------------------------------------- #
# --json: one document, written in one place
# --------------------------------------------------------------------------- #

# The schema version of the envelope every `--json` run prints. It is a
# *contract*: `tests/test_json_output.py` pins the envelope and each of the
# seven command shapes inside it, and changing a field is a breaking change
# that belongs with a bump of this number.
JSON_SCHEMA = 1


def _finish(args, code, captured, error=None):
    """Write the JSON document for a `--json` run, and hand back `code`.

    Called from every exit path in `main`, and only from there: one writer, so
    the document is written once, whole, after the exit code is settled.
    Anything the command printed on the way is in `lines` — it was captured
    rather than printed, so prose can never be interleaved with the document.

    `print` rather than `_echo`, and deliberately: `json.dumps` escapes every
    non-ASCII character by default, so the document is pure ASCII and there is
    no glyph for a console to fail to spell. The prose inside `lines` is
    escaped with it, which is the only way a `→` survives a Windows pipe.
    """
    if captured is None:
        return code
    document = {
        "schema": JSON_SCHEMA,
        "command": args.command,
        "ok": error is None and code in (0, None),
        "exit_code": 0 if code is None else code,
        "advisory": sorted(set(_ADVISED)),
        "error": error,
        "data": _RENDER["document"],
        "lines": captured.getvalue().splitlines(),
    }
    print(json.dumps(document, indent=2, default=str))
    return code


# --------------------------------------------------------------------------- #
# the command registry
# --------------------------------------------------------------------------- #
#
# `build_parser` used to be forty-one hand-written blocks, and the flags every
# command shares — `--strict`, and now `--json` — were added by a loop over the
# subparsers afterwards, because writing them out forty-one times was worse.
# The registry finishes that thought: a command is *data* — a name, a help
# line, the function it runs, its own flags — and the loop below turns every
# row into a subparser the same way. Adding a command is one row; giving one
# `--json` is one keyword on that row. Nothing has to be kept in step by hand,
# which is what the two coordinated edits per command used to require.
#
# `tests/test_commands_registry.py` pins the whole parser surface — every flag,
# default, choice list and help string of all forty-one — against a fixture
# generated from the hand-written parser this replaced.

Command = collections.namedtuple("Command", "name help func args emits_json")


def command(name, help_text, func, *args, emits_json=False):
    """One row of the registry: a subcommand, its help, its flags.

    `emits_json=True` gives the command `--json`; it must also route its result
    through `_emit`, which is what actually makes the flag mean anything.
    """
    return Command(name, help_text, func, args, emits_json)


def flag(*names, **options):
    """One `add_argument` call, held as data until `build_parser` makes it."""
    return names, options


NAME = flag("name", nargs="?", default=None)
REST = lambda help_text: flag("rest", nargs="*", help=help_text)  # noqa: E731
PLAN = flag("--plan", default=None)
STRICT_HELP = "escalate advisory conditions to exit 1"


def commands():
    """The registry, built fresh on every call.

    A function rather than a module-level tuple for two reasons, both of them
    behaviours the hand-written `build_parser` had for free by virtue of
    running its forty-one blocks at call time:

      * The choice lists are read *now*. `verify.JUDGED`, `plan_mod.TIERS` and
        `findings_mod.SEVERITIES` are derived from tables a module can extend;
        frozen into a tuple at import, a kind registered afterwards would be
        missing from `--sign-off` for the life of the process, and
        `tests/test_kind_table_reaches_complexity.py` refuses exactly that.
      * `cmd_*` is looked up in this module's globals when the row is built,
        so replacing one — which is how `tests/test_cli_exit_codes.py` makes a
        command raise — still reaches the parser.
    """
    return (
        command(
            "init", "scaffold .ctx/ and propose verify commands", cmd_init,
            flag("--profile", choices=sorted(config_mod.PROFILES)),
            flag("--verify-now", action="store_true",
                 help="run each proposed command and keep only those that pass"),
            flag("--timeout", type=int, default=120),
            flag("--force", action="store_true", help="rewrite ctx.yaml"),
        ),
        command("status", "level, active work, budget, recent journal", cmd_status,
                emits_json=True),
        command("briefing", "print exactly what SessionStart would inject", cmd_briefing),
        command("resume", "expanded state for on-demand recall", cmd_resume),
        command("digest", "regenerate journal/DIGEST.md", cmd_digest),
        command("drop", "return to L0 trace", cmd_drop),
        command("list", "saved contexts, project and global", cmd_list),
        command(
            "level", "set the engagement level", cmd_level,
            flag("level", choices=list(config_mod.LEVELS)),
        ),
        command(
            "task", "escalate to L1 with a single task file", cmd_task,
            NAME,
            REST("objective, as loose words"),
            flag("--objective", default=None),
            flag("--force", action="store_true"),
        ),
        command(
            "save", "write a portable context bundle", cmd_save,
            NAME,
            REST("more of the name, as loose words"),
            flag("--stdin", action="store_true", help="read the bundle body from stdin"),
            flag("--file", default=None),
            flag("--tag", action="append", default=[]),
        ),
        command(
            "load", "print a bundle: project, then global, then path", cmd_load,
            NAME,
            REST("more of the name, as loose words"),
        ),
        command(
            "promote", "copy a bundle into the global store", cmd_promote,
            NAME,
            REST("more of the name, as loose words"),
        ),
        command(
            "journal", "append one entry", cmd_journal,
            flag("kind"),
            flag("target"),
            flag("--note", default=None),
        ),
        command(
            "prune", "fold old journal days into monthly archives", cmd_prune,
            flag("--before", default=None,
                 help="YYYY-MM-DD; defaults to journal.keep_days"),
            flag("--discard", action="store_true", help="delete rather than archive"),
        ),
        command(
            "doctor", "check layout, budgets, verify commands, gate", cmd_doctor,
            flag("--verify", action="store_true", help="actually run verify commands"),
            flag("--clear", action="store_true",
                 help="delete the hook error log before checking"),
            flag("--timeout", type=int, default=300),
            emits_json=True,
        ),
        command(
            "spec", "escalate to L2 and scaffold a spec", cmd_spec,
            NAME,
            REST("intent, as loose words"),
            flag("--intent", default=None),
        ),
        command(
            "question", "add questions to a spec", cmd_question,
            flag("name"),
            flag("text", nargs="+"),
            flag("--non-blocking", action="store_true"),
        ),
        command("ask", "list questions still open on a spec", cmd_ask, NAME),
        command(
            "resolve", "answer a question and record it", cmd_resolve,
            NAME,
            flag("--question", required=True, help="substring of the question"),
            flag("--answer", required=True),
        ),
        command("spec-ready", "Gate 1 as an exit code (0 = ready)", cmd_spec_ready, NAME),
        command(
            "decide", "record an ADR", cmd_decide,
            flag("title", nargs="*", help="the decision, as loose words"),
            flag("--context", default=None),
            flag("--decision", default=None),
            flag("--consequences", default=None),
        ),
        command(
            "verify", "run the done-gate for the active work", cmd_verify,
            flag("--sign-off", choices=list(verify.JUDGED), default=None,
                 help="record a judged check as passed"),
            flag("--note", default=None),
            flag("--plan", default=None,
                 help="verify every unit in a plan headlessly (for CI)"),
            emits_json=True,
        ),
        command(
            "plan", "scaffold a plan (refuses if the spec is ambiguous)", cmd_plan,
            NAME,
            flag("--spec", default=None),
            flag("--unit", action="append", default=[]),
            flag("--no-spec", action="store_true", help="plan without a spec"),
        ),
        command(
            "plan-unit", "scaffold one unit file", cmd_plan_unit,
            flag("name"),
            PLAN,
            flag("--objective", default=None),
            flag("--tier", choices=list(plan_mod.TIERS), default="subagent"),
            flag("--owns", action="append", default=[]),
        ),
        command("plan-check", "compute waves and check for collisions", cmd_plan_check,
                NAME, emits_json=True),
        command(
            "start", "dispatch brief for the next (or given) wave", cmd_start,
            NAME,
            flag("--wave", type=int, default=None),
            flag("--worktree", action="store_true",
                 help="isolate each session-tier unit in its own temporary "
                      "worktree; off by default, because a worktree holds its "
                      "branch exclusively and the main tree can no longer "
                      "check it out"),
            flag("--rebaseline", action="append", default=[], metavar="UNIT",
                 help="re-capture this unit's review baseline over the current "
                      "tree, replacing what dispatch recorded. Repeatable. "
                      "Refuses if the contract itself changed — that is what "
                      "--reseal is for. For the real crash case; everything "
                      "else keeps the baseline it was dispatched with"),
            flag("--reseal", action="append", default=[], metavar="UNIT",
                 help="accept this unit's contract as it now stands: re-record "
                      "the promise the done-gate holds it to, and retake the "
                      "baseline with it. Repeatable, journalled by name and by "
                      "changed field. A planning decision, never implied by "
                      "--rebaseline"),
            # Accepted and ignored: this was the opt-out before worktrees became
            # opt-in, and it still reads correctly in older docs and scripts.
            flag("--no-worktree", action="store_true", help=argparse.SUPPRESS),
        ),
        command(
            "snapshot", "capture a content snapshot for a unit", cmd_snapshot,
            NAME,
            PLAN,
            flag("--phase", choices=["before", "after"], default="before"),
            flag("--round", type=int, default=1),
        ),
        command(
            "review", "build the review package for a unit", cmd_review,
            NAME,
            PLAN,
            flag("--round", type=int, default=None),
        ),
        command(
            "findings", "list or update a unit's review findings", cmd_findings,
            NAME,
            PLAN,
            flag("--add", choices=list(findings_mod.SEVERITIES), default=None),
            flag("--summary", default=None),
            flag("--where", default=None),
            flag("--evidence", default=None),
            flag("--set", type=int, default=None, metavar="ID"),
            flag("--status", choices=list(findings_mod.STATUSES), default=None),
            flag("--ruling", default=None),
            emits_json=True,
        ),
        command(
            "phase",
            "advance or inspect a unit's phase gate (kind: bug, or a declared "
            "`phases:` list)",
            cmd_phase,
            flag("name", nargs="?", default=None, help="unit name"),
            flag("phase", nargs="?", default=None,
                 help="phase to record; omit to list status instead"),
            PLAN,
            flag("--command", default=None),
            flag("--exit-code", type=int, default=None),
            flag("--evidence", default=None),
            flag("--note", default=None),
        ),
        command(
            "merge", "land a unit's worktree branch after its gate passes", cmd_merge,
            NAME,
            PLAN,
            flag("--skip-gate", action="store_true",
                 help="merge without running the unit's verify checks"),
        ),
        command(
            "worktree", "list or discard ctx worktrees", cmd_worktree,
            flag("action", choices=["list", "remove"]),
            NAME,
            # Spelled as `ctx merge --plan` is. Without it the ambiguity refusal
            # that `worktree.remove` raises — "pass --plan to say which one to
            # discard" — named a flag the subparser did not define, so the one
            # message that told the user what to do could not be acted on.
            flag("--plan", default=None,
                 help="which plan's worktree to discard, when two share a name"),
            flag("--force", action="store_true",
                 help="discard uncommitted work in the worktree"),
        ),
        command("next", "the single most useful next action, from state", cmd_next,
                emits_json=True),
        command(
            "escalate", "L1 to L2, carrying the task into a spec", cmd_escalate,
            NAME,
            flag("--spec", default=None, help="name the spec differently"),
        ),
        command(
            "trust", "review and accept the verify commands to run", cmd_trust,
            flag("--yes", action="store_true", help="accept the listed commands"),
            flag("--lock", action="store_true",
                 help="write .ctx/trust.lock from the declared commands, to commit"),
            flag("--verify-lock", dest="verify_lock", action="store_true",
                 help="check the ledger against .ctx/trust.lock; accepts nothing"),
        ),
        command(
            "migrate", "upgrade ledger files to this plugin's schema", cmd_migrate,
            flag("--check", action="store_true",
                 help="report what needs migrating and exit 1; writes nothing"),
        ),
        command("budget", "predicted and measured context cost", cmd_budget, PLAN),
        command(
            "telemetry", "hook durations, briefing sizes, reported spend", cmd_telemetry,
            # Spend is reported, never observed: this process cannot see a model's
            # token usage, so the orchestrator that ran the wave hands the number
            # over afterwards. Kept as a flag on `telemetry` rather than its own
            # command because it writes to, and is read back out of, exactly the
            # same file.
            flag("--spend", default=None, metavar="TOKENS",
                 help="record what --unit actually cost, as reported by its "
                      "runner (partial data by construction)"),
            flag("--unit", default=None, metavar="NAME",
                 help="the unit --spend refers to"),
            flag("--plan", default=None,
                 help="pair reported spend with this plan's predicted scores"),
        ),
        command(
            "ci", "every headless check in one exit code", cmd_ci,
            flag("--plan", action="append", default=[]),
            emits_json=True,
        ),
        command(
            "unit", "focus a unit, or record its outcome", cmd_unit,
            NAME,
            PLAN,
            flag("--status", choices=list(plan_mod.STATUSES), default="running"),
            flag("--force", action="store_true",
                 help="mark done even though the unit's gate did not pass"),
        ),
        command("handoff", "write a resume packet for a session or person", cmd_handoff,
                NAME),
    )


# The commands that answer in JSON. Derived, never maintained: a command gets
# on this list by carrying `emits_json=True` in the registry above, which is
# the same edit that gives it the flag.
JSON_COMMANDS = frozenset(entry.name for entry in commands() if entry.emits_json)


def build_parser():
    parser = argparse.ArgumentParser(prog="ctx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ctx {__version__}")
    parser.add_argument("--cwd", default=None, help="resolve the ledger from here")
    parser.add_argument("--strict", action="store_true", help=STRICT_HELP)
    sub = parser.add_subparsers(dest="command", required=True)

    for entry in commands():
        subparser = sub.add_parser(entry.name, help=entry.help)
        for names, options in entry.args:
            subparser.add_argument(*names, **options)
        if entry.emits_json:
            # Rendering, and nothing else: the exit code, the refusals and the
            # work done are the same with the flag and without it.
            subparser.add_argument(
                "--json", action="store_true",
                help="print one JSON document instead of prose")
        # `--strict` belongs to every command, not to a list of them, because
        # the caller who needs it is a script and it must not have to remember
        # which subcommands accept it. SUPPRESS keeps the subparser from
        # writing its own default over a `--strict` given before the
        # subcommand name.
        subparser.add_argument("--strict", action="store_true",
                               default=argparse.SUPPRESS, help=STRICT_HELP)
        subparser.set_defaults(func=entry.func)

    return parser


# Exit codes, and there are only three:
#
#   0  the command did what was asked — or hit an advisory condition (see
#      ADVISORY) while `--strict` was off.
#   1  an advisory condition, escalated, and only under `--strict`/`CTX_STRICT=1`.
#   2  the command refused, or failed: a message-carrying `SystemExit` raised
#      from anywhere, or an exception nobody expected.
#
# 2 rather than 1 for refusals because `verify`, `ci`, `spec-ready`,
# `plan-check`, `doctor`, `migrate` and `trust` already returned 2 here. Every
# other command moves from 0 to 2, and no caller that already reads an exit
# code sees the meaning of one change underneath it.


def main(argv=None):
    args = build_parser().parse_args(argv)
    _ADVISED.clear()
    # `--json` changes rendering and nothing else: the same function runs, with
    # the same arguments, and returns the same exit code. What changes is where
    # its prose goes — into a buffer that becomes the document's `lines`, so
    # that stdout carries one JSON document and never prose next to one. The
    # capture is around the whole call rather than inside `_echo` because the
    # gate, `verify_plan` and `plan.check` print through `verify.echo` too, and
    # a caller parsing stdout must not have to know which printer ran.
    _RENDER["json"] = bool(getattr(args, "json", False))
    _RENDER["document"] = None
    captured = io.StringIO() if _RENDER["json"] else None
    held = (contextlib.redirect_stdout(captured) if captured
            else contextlib.nullcontext())
    try:
        with held:
            code = args.func(args)
    except SystemExit as exc:
        # How a command refuses: `raise SystemExit("why")`. It used to reach
        # the shell as exit 0 with the reason on stdout for all but seven
        # commands, which left a script unable to tell a refusal from a result.
        message = str(exc)
        if message and not message.isdigit():
            _warn(message)
            return _finish(args, 2, captured, error=message)
        raise
    except KeyboardInterrupt:
        # Not ours to describe or to convert: the caller pressed ^C and expects
        # the interpreter's own 130, not a diagnosis.
        raise
    except Exception as exc:  # noqa: BLE001 — the point is to catch everything
        # Anything unforeseen — an OSError from a path the filesystem refuses,
        # a bug in this file. A traceback is not a diagnosis for the person who
        # typed the command, so it goes behind CTX_DEBUG and one line stands in
        # its place. The exception type is in the line because `[Errno 63] File
        # name too long` reads very differently from `KeyError: 'wave'`.
        if _env_flag("CTX_DEBUG"):
            traceback.print_exc()
        message = f"ctx {args.command} failed: {type(exc).__name__}: {exc}"
        _warn(message)
        return _finish(args, 2, captured, error=message)
    if _ADVISED and _strict(args) and code in (0, None):
        # Advisory, and the caller asked to hear about it. The notice itself is
        # already on stdout; this names the condition so a log says which one.
        _warn("strict: " + ", ".join(sorted(set(_ADVISED)))
              + " — advisory, escalated by --strict/CTX_STRICT")
        return _finish(args, 1, captured)
    return _finish(args, code, captured)


# --------------------------------------------------------------------------- #

class _CommandLayer(types.ModuleType):
    """This module's type, so that writing `cli.<name>` reaches `ctx.commands`.

    The bodies moved; the *patch surface* did not. Four suites install a
    replacement over a command or a helper through this module — `cli.cmd_status
    = stub` to prove the registry reads `cmd_*` at build time, and two positive
    controls that put the pre-fix `_set_unit_status` back to show the write it
    lost was really lost. A plain re-export rebinds only the name here, leaves
    the body in `ctx.commands` calling the original, and turns a control that
    must fail into one that passes for no reason.

    So a write to a name `ctx.commands` defines is forwarded there as well as
    recorded here, which is exactly what the same write did when the two files
    were one. Reads are ordinary; module bodies assign into `__dict__` directly
    and never reach this, so nothing above is affected.
    """

    def __setattr__(self, name, value):
        if name in vars(commands_mod):
            setattr(commands_mod, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CommandLayer
