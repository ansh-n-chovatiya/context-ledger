"""`ctx/cli.py`'s command registry — forty-one subcommands, one table.

`build_parser` was forty-one hand-written blocks of `add_parser` /
`add_argument` / `set_defaults`. Adding a command meant three coordinated
edits in three places, and giving every command a shared flag meant a loop
over `sub.choices` after the fact — which is how `--strict` was already done,
and the admission that the blocks were data pretending to be code.

This file is the guard on that conversion. `SURFACE` below was *generated from
the hand-written parser* and committed: every subcommand, every flag, every
default, choice list, metavar, argparse action class and help string as they
were before the registry existed. `test_the_surface_is_unchanged` rebuilds the
same rows from the live parser and compares. Forty-one commands' worth of flags
is too many to re-read by eye, so nobody re-reads them: the fixture does.

The one deliberate difference is `--json`, which this same unit added to seven
commands. It is excluded from the comparison by name, and pinned separately —
see `tests/test_json_output.py`.

Regenerating the fixture by hand from the current parser would make this test
tautological. If a flag genuinely has to change, change `SURFACE` in the same
commit as the code, and the diff shows exactly what a caller's scripts will
notice.
"""

import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import cli  # noqa: E402

CLI_PATH = Path(__file__).resolve().parent.parent / "ctx" / "cli.py"

# Added by this unit, on purpose, to the seven commands that carry `--json`.
# Everything else in the parser is expected to be byte-for-byte what it was.
ADDED = ("--json",)


def _row(action):
    """One argparse action, flattened the way `SURFACE` records it."""
    def shown(value):
        return "<SUPPRESS>" if value is argparse.SUPPRESS else value

    return (
        "/".join(action.option_strings) or "<positional>",
        action.dest,
        action.nargs,
        action.const,
        shown(action.default),
        getattr(action.type, "__name__", None) if action.type else None,
        tuple(action.choices) if action.choices is not None else None,
        action.required,
        action.metavar,
        type(action).__name__,
        shown(action.help),
    )


def _live():
    """The parser as it is now: `{name: (help, func, (rows...))}`."""
    parser = cli.build_parser()
    sub = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    helps = {c.dest: c.help for c in sub._choices_actions}
    return {
        name: (
            helps.get(name),
            subparser.get_default("func").__name__,
            tuple(_row(a) for a in subparser._actions
                  if not set(a.option_strings) & set(ADDED)),
        )
        for name, subparser in sub.choices.items()
    }


SURFACE = {
    'init': (
        'scaffold .ctx/ and propose verify commands',
        'cmd_init',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--profile', 'profile', None, None, None, None, ('code', 'data',
             'docs', 'infra', 'research'), False, None, '_StoreAction',
             None),
            ('--verify-now', 'verify_now', 0, True, False, None, None, False,
             None, '_StoreTrueAction',
             'run each proposed command and keep only those that pass'),
            ('--timeout', 'timeout', None, None, 120, 'int', None, False, None,
             '_StoreAction',
             None),
            ('--force', 'force', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'rewrite ctx.yaml'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'status': (
        'level, active work, budget, recent journal',
        'cmd_status',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'briefing': (
        'print exactly what SessionStart would inject',
        'cmd_briefing',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'resume': (
        'expanded state for on-demand recall',
        'cmd_resume',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'digest': (
        'regenerate journal/DIGEST.md',
        'cmd_digest',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'drop': (
        'return to L0 trace',
        'cmd_drop',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'list': (
        'saved contexts, project and global',
        'cmd_list',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'level': (
        'set the engagement level',
        'cmd_level',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'level', None, None, None, None, ('0', '1', '2'),
             True, None, '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'task': (
        'escalate to L1 with a single task file',
        'cmd_task',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('<positional>', 'rest', '*', None, None, None, None, False, None,
             '_StoreAction',
             'objective, as loose words'),
            ('--objective', 'objective', None, None, None, None, None, False,
             None, '_StoreAction',
             None),
            ('--force', 'force', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'save': (
        'write a portable context bundle',
        'cmd_save',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('<positional>', 'rest', '*', None, None, None, None, False, None,
             '_StoreAction',
             'more of the name, as loose words'),
            ('--stdin', 'stdin', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'read the bundle body from stdin'),
            ('--file', 'file', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--tag', 'tag', None, None, [], None, None, False, None,
             '_AppendAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'load': (
        'print a bundle: project, then global, then path',
        'cmd_load',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('<positional>', 'rest', '*', None, None, None, None, False, None,
             '_StoreAction',
             'more of the name, as loose words'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'promote': (
        'copy a bundle into the global store',
        'cmd_promote',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('<positional>', 'rest', '*', None, None, None, None, False, None,
             '_StoreAction',
             'more of the name, as loose words'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'journal': (
        'append one entry',
        'cmd_journal',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'kind', None, None, None, None, None, True, None,
             '_StoreAction',
             None),
            ('<positional>', 'target', None, None, None, None, None, True,
             None, '_StoreAction',
             None),
            ('--note', 'note', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'prune': (
        'fold old journal days into monthly archives',
        'cmd_prune',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--before', 'before', None, None, None, None, None, False, None,
             '_StoreAction',
             'YYYY-MM-DD; defaults to journal.keep_days'),
            ('--discard', 'discard', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'delete rather than archive'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'doctor': (
        'check layout, budgets, verify commands, gate',
        'cmd_doctor',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--verify', 'verify', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'actually run verify commands'),
            ('--clear', 'clear', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'delete the hook error log before checking'),
            ('--timeout', 'timeout', None, None, 300, 'int', None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'spec': (
        'escalate to L2 and scaffold a spec',
        'cmd_spec',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('<positional>', 'rest', '*', None, None, None, None, False, None,
             '_StoreAction',
             'intent, as loose words'),
            ('--intent', 'intent', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'question': (
        'add questions to a spec',
        'cmd_question',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', None, None, None, None, None, True, None,
             '_StoreAction',
             None),
            ('<positional>', 'text', '+', None, None, None, None, True, None,
             '_StoreAction',
             None),
            ('--non-blocking', 'non_blocking', 0, True, False, None, None,
             False, None, '_StoreTrueAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'ask': (
        'list questions still open on a spec',
        'cmd_ask',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'resolve': (
        'answer a question and record it',
        'cmd_resolve',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--question', 'question', None, None, None, None, None, True,
             None, '_StoreAction',
             'substring of the question'),
            ('--answer', 'answer', None, None, None, None, None, True, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'spec-ready': (
        'Gate 1 as an exit code (0 = ready)',
        'cmd_spec_ready',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'decide': (
        'record an ADR',
        'cmd_decide',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'title', '*', None, None, None, None, False, None,
             '_StoreAction',
             'the decision, as loose words'),
            ('--context', 'context', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--decision', 'decision', None, None, None, None, None, False,
             None, '_StoreAction',
             None),
            ('--consequences', 'consequences', None, None, None, None, None,
             False, None, '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'verify': (
        'run the done-gate for the active work',
        'cmd_verify',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--sign-off', 'sign_off', None, None, None, None, ('rubric',
             'human'), False, None, '_StoreAction',
             'record a judged check as passed'),
            ('--note', 'note', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             'verify every unit in a plan headlessly (for CI)'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'plan': (
        'scaffold a plan (refuses if the spec is ambiguous)',
        'cmd_plan',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--spec', 'spec', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--unit', 'unit', None, None, [], None, None, False, None,
             '_AppendAction',
             None),
            ('--no-spec', 'no_spec', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'plan without a spec'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'plan-unit': (
        'scaffold one unit file',
        'cmd_plan_unit',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', None, None, None, None, None, True, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--objective', 'objective', None, None, None, None, None, False,
             None, '_StoreAction',
             None),
            ('--tier', 'tier', None, None, 'subagent', None, ('inline',
             'subagent', 'session'), False, None, '_StoreAction',
             None),
            ('--owns', 'owns', None, None, [], None, None, False, None,
             '_AppendAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'plan-check': (
        'compute waves and check for collisions',
        'cmd_plan_check',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'start': (
        'dispatch brief for the next (or given) wave',
        'cmd_start',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--wave', 'wave', None, None, None, 'int', None, False, None,
             '_StoreAction',
             None),
            ('--worktree', 'worktree', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'isolate each session-tier unit in its own temporary worktree; '
             'off by default, because a worktree holds its branch '
             'exclusively and the main tree can no longer check it out'),
            ('--rebaseline', 'rebaseline', None, None, [], None, None, False,
             'UNIT', '_AppendAction',
             "re-capture this unit's review baseline over the current tree, "
             'replacing what dispatch recorded. Repeatable. Refuses if the '
             'contract itself changed — that is what --reseal is for. For '
             'the real crash case; everything else keeps the baseline it was '
             'dispatched with'),
            ('--reseal', 'reseal', None, None, [], None, None, False, 'UNIT',
             '_AppendAction',
             "accept this unit's contract as it now stands: re-record the "
             'promise the done-gate holds it to, and retake the baseline '
             'with it. Repeatable, journalled by name and by changed field. '
             'A planning decision, never implied by --rebaseline'),
            ('--no-worktree', 'no_worktree', 0, True, False, None, None, False,
             None, '_StoreTrueAction',
             '<SUPPRESS>'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'snapshot': (
        'capture a content snapshot for a unit',
        'cmd_snapshot',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--phase', 'phase', None, None, 'before', None, ('before',
             'after'), False, None, '_StoreAction',
             None),
            ('--round', 'round', None, None, 1, 'int', None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'review': (
        'build the review package for a unit',
        'cmd_review',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--round', 'round', None, None, None, 'int', None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'findings': (
        "list or update a unit's review findings",
        'cmd_findings',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--add', 'add', None, None, None, None, ('critical', 'important',
             'minor'), False, None, '_StoreAction',
             None),
            ('--summary', 'summary', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--where', 'where', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--evidence', 'evidence', None, None, None, None, None, False,
             None, '_StoreAction',
             None),
            ('--set', 'set', None, None, None, 'int', None, False, 'ID',
             '_StoreAction',
             None),
            ('--status', 'status', None, None, None, None, ('open',
             'addressed', 'disputed', 'parked'), False, None, '_StoreAction',
             None),
            ('--ruling', 'ruling', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'phase': (
        "advance or inspect a unit's phase gate (kind: bug, or a declared "
        '`phases:` list)',
        'cmd_phase',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             'unit name'),
            ('<positional>', 'phase', '?', None, None, None, None, False, None,
             '_StoreAction',
             'phase to record; omit to list status instead'),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--command', 'command', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--exit-code', 'exit_code', None, None, None, 'int', None, False,
             None, '_StoreAction',
             None),
            ('--evidence', 'evidence', None, None, None, None, None, False,
             None, '_StoreAction',
             None),
            ('--note', 'note', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'merge': (
        "land a unit's worktree branch after its gate passes",
        'cmd_merge',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--skip-gate', 'skip_gate', 0, True, False, None, None, False,
             None, '_StoreTrueAction',
             "merge without running the unit's verify checks"),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'worktree': (
        'list or discard ctx worktrees',
        'cmd_worktree',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'action', None, None, None, None, ('list',
             'remove'), True, None, '_StoreAction',
             None),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             "which plan's worktree to discard, when two share a name"),
            ('--force', 'force', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'discard uncommitted work in the worktree'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'next': (
        'the single most useful next action, from state',
        'cmd_next',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'escalate': (
        'L1 to L2, carrying the task into a spec',
        'cmd_escalate',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--spec', 'spec', None, None, None, None, None, False, None,
             '_StoreAction',
             'name the spec differently'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'trust': (
        'review and accept the verify commands to run',
        'cmd_trust',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--yes', 'yes', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'accept the listed commands'),
            ('--lock', 'lock', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'write .ctx/trust.lock from the declared commands, to commit'),
            ('--verify-lock', 'verify_lock', 0, True, False, None, None, False,
             None, '_StoreTrueAction',
             'check the ledger against .ctx/trust.lock; accepts nothing'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'migrate': (
        "upgrade ledger files to this plugin's schema",
        'cmd_migrate',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--check', 'check', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             'report what needs migrating and exit 1; writes nothing'),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'budget': (
        'predicted and measured context cost',
        'cmd_budget',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'telemetry': (
        'hook durations, briefing sizes, reported spend',
        'cmd_telemetry',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--spend', 'spend', None, None, None, None, None, False, 'TOKENS',
             '_StoreAction',
             'record what --unit actually cost, as reported by its runner '
             '(partial data by construction)'),
            ('--unit', 'unit', None, None, None, None, None, False, 'NAME',
             '_StoreAction',
             'the unit --spend refers to'),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             "pair reported spend with this plan's predicted scores"),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'ci': (
        'every headless check in one exit code',
        'cmd_ci',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('--plan', 'plan', None, None, [], None, None, False, None,
             '_AppendAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'unit': (
        'focus a unit, or record its outcome',
        'cmd_unit',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--plan', 'plan', None, None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--status', 'status', None, None, 'running', None, ('pending',
             'running', 'blocked', 'verify_failed', 'done'), False, None,
             '_StoreAction',
             None),
            ('--force', 'force', 0, True, False, None, None, False, None,
             '_StoreTrueAction',
             "mark done even though the unit's gate did not pass"),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
    'handoff': (
        'write a resume packet for a session or person',
        'cmd_handoff',
        (
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction',
             'show this help message and exit'),
            ('<positional>', 'name', '?', None, None, None, None, False, None,
             '_StoreAction',
             None),
            ('--strict', 'strict', 0, True, '<SUPPRESS>', None, None, False,
             None, '_StoreTrueAction',
             'escalate advisory conditions to exit 1'),
        ),
    ),
}


def _value(row):
    """A value the parser will accept for this argument."""
    _flags, _dest, _nargs, _const, _default, type_name, choices, *_rest = row
    if choices:
        return str(choices[0])
    return "1" if type_name == "int" else "x"


def _minimal_argv(name, rows):
    """`ctx <name> …` with everything the parser insists on, and nothing else."""
    argv = [name]
    for row in rows:
        flags, _dest, nargs, _const, _default, _type, _choices, required = row[:8]
        if flags == "<positional>":
            if nargs not in ("?", "*"):
                argv.append(_value(row))
        elif required:
            argv.append(flags.split("/")[0])
            if nargs != 0:
                argv.append(_value(row))
    return argv


def _portable(entry):
    """The surface with the one field argparse computes differently per version.

    The value is `(help, func_name, actions)`; only the actions are rewritten.
    A *positional*'s `required` is not something this project sets — argparse
    derives it, and the derivation changed between 3.9 and 3.14, so a fixture
    generated on one reports `False` where the other reports `True`. It went
    unnoticed until the suite first met the 3.8/3.9 matrix on CI. Every field
    this project actually declares is still compared byte for byte; only the
    derived one is dropped, and only for positionals, where `_minimal_argv`
    keys off `nargs` rather than reading it anyway.
    """
    help_text, func, actions = entry
    out = []
    for row in actions:
        row = tuple(row)
        if row and row[0] == "<positional>":
            row = row[:7] + (None,) + row[8:]
        out.append(row)
    return (help_text, func, tuple(out))


class TestTheSurfaceIsUnchanged(unittest.TestCase):
    """Forty-one subcommands still parse exactly as they did."""

    def test_the_same_commands_in_the_same_order(self):
        live = _live()
        self.assertEqual(list(live), list(SURFACE))
        self.assertEqual(len(live), 41)

    def test_every_command_is_unchanged(self):
        live = _live()
        for name, expected in SURFACE.items():
            with self.subTest(command=name):
                self.assertEqual(_portable(live[name]), _portable(expected))

    def test_the_top_level_parser_is_unchanged(self):
        parser = cli.build_parser()
        rows = [_row(a) for a in parser._actions
                if not isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(rows, [
            ('-h/--help', 'help', 0, None, '<SUPPRESS>', None, None, False,
             None, '_HelpAction', 'show this help message and exit'),
            ('--version', 'version', 0, None, '<SUPPRESS>', None, None, False,
             None, '_VersionAction', "show program's version number and exit"),
            ('--cwd', 'cwd', None, None, None, None, None, False, None,
             '_StoreAction', 'resolve the ledger from here'),
            ('--strict', 'strict', 0, True, False, None, None, False, None,
             '_StoreTrueAction', 'escalate advisory conditions to exit 1'),
        ])

    def test_every_subcommand_still_parses_and_dispatches(self):
        """Parsing `ctx <name> …` reaches the same `cmd_*` it always did.

        The argv is derived from the fixture rather than written out: a
        required flag gets a value, a positional gets one of its choices, and
        anything optional is left off. It is the cheapest proof that the rows
        above describe a parser that actually parses.
        """
        parser = cli.build_parser()
        for name, (_help, func, rows) in SURFACE.items():
            with self.subTest(command=name):
                args = parser.parse_args(_minimal_argv(name, rows))
                self.assertEqual(args.func.__name__, func)


class TestOneCommandIsOneEntry(unittest.TestCase):
    """The property the conversion was for."""

    def test_the_registry_and_the_parser_cannot_disagree(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        self.assertEqual([entry.name for entry in cli.commands()],
                         list(sub.choices))

    def test_every_entry_names_a_function_that_exists(self):
        for entry in cli.commands():
            with self.subTest(command=entry.name):
                self.assertTrue(callable(entry.func))
                self.assertIs(getattr(cli, entry.func.__name__), entry.func)
                self.assertTrue(entry.func.__name__.startswith("cmd_"))

    def test_every_entry_has_help_text(self):
        """`ctx --help` is the whole command list; a blank row is a bug."""
        for entry in cli.commands():
            with self.subTest(command=entry.name):
                self.assertTrue((entry.help or "").strip())

    def test_the_shared_flags_are_added_by_the_loop_not_by_the_entries(self):
        """`--strict` and `--json` are one line in `build_parser`, not 41.

        An entry that declared either itself would still work, and would be the
        first step back to forty-one copies of a flag that has to agree.
        """
        for entry in cli.commands():
            for names, _options in entry.args:
                with self.subTest(command=entry.name, flag=names):
                    self.assertNotIn("--strict", names)
                    self.assertNotIn("--json", names)

    def test_adding_a_command_is_one_entry(self):
        """The claim, exercised: append a row, get a working subcommand."""
        added = cli.command("smoke-test-only", "a command added by a test",
                            cli.cmd_status, cli.flag("--loud", action="store_true"))
        original = cli.commands
        cli.commands = lambda: original() + (added,)
        try:
            args = cli.build_parser().parse_args(["smoke-test-only", "--loud"])
        finally:
            cli.commands = original
        self.assertIs(args.func, cli.cmd_status)
        self.assertTrue(args.loud)
        # And it is gone again, because the registry is the only definition.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["smoke-test-only"])

    def test_the_registry_is_built_when_the_parser_is_not_when_imported(self):
        """Choice lists and `cmd_*` are read at build time, as they were.

        The hand-written parser ran its forty-one blocks inside
        `build_parser`, so `--sign-off`'s choices came from `verify.JUDGED` as
        it stood *then*, and `set_defaults(func=cmd_status)` bound whatever
        the module attribute was *then*. A registry held as a module-level
        tuple would have frozen both at import; two existing suites depend on
        neither being frozen.
        """
        first, second = cli.commands(), cli.commands()
        self.assertIsNot(first, second)

        def stub(_args):
            return 0

        original = cli.cmd_status
        cli.cmd_status = stub
        try:
            parser = cli.build_parser()
        finally:
            cli.cmd_status = original
        self.assertIs(parser.parse_args(["status"]).func, stub)

    def test_json_is_declared_once_per_command_and_derived_everywhere_else(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        for entry in cli.commands():
            has_flag = any("--json" in a.option_strings
                           for a in sub.choices[entry.name]._actions)
            with self.subTest(command=entry.name):
                self.assertEqual(has_flag, entry.emits_json)
                self.assertEqual(entry.name in cli.JSON_COMMANDS,
                                 entry.emits_json)

    def test_every_command_accepts_strict(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        for name, subparser in sub.choices.items():
            with self.subTest(command=name):
                strict = [a for a in subparser._actions
                          if "--strict" in a.option_strings]
                self.assertEqual(len(strict), 1)
                # SUPPRESS, so a `--strict` given before the subcommand name
                # is not overwritten by the subparser's own default.
                self.assertIs(strict[0].default, argparse.SUPPRESS)


if __name__ == "__main__":
    unittest.main()
