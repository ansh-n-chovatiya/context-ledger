"""Diagnostics for the paths that are forbidden to fail.

`telemetry.record`, `journal.append` and the hook error log all swallow their
own exceptions on purpose: none of them may be the reason a session breaks. The
cost of that bargain was that a read-only mount, a full disk and a permissions
error were indistinguishable from success — every one of them returned quietly
and the caller carried on. This module is the other half of the bargain. The
swallow still happens; the reason for it now has somewhere to go.

Four properties, in the order they constrain the design:

* **Off by default, and off means nothing happens.** The pitch of this plugin
  is a small always-on footprint, so it must not start writing files nobody
  asked for. With `CTX_LOG` unset, every call here is one `os.environ.get` and
  a return: no file is created, no line is printed, nothing is formatted.
* **It cannot break what it observes.** Every write is wrapped. A log
  destination on a read-only disk, a path whose parent does not exist, a
  closed stderr — all of them lose the line and none of them raise. This is
  the one place in the codebase where swallowing an error is unambiguously
  correct, because the alternative is a logger that breaks the thing it exists
  to report on.
* **No `logging`.** The standard library's logger is process-global mutable
  state configured by whoever imports it first. Hooks run inside a process
  this package does not own, and `ctx` as a library sits inside applications
  that have their own handlers; attaching to the root logger would either be
  silenced by someone else's configuration or spray this module's diagnostics
  into someone else's log. Sixty lines of `write` avoid all of it, and keep
  the zero-dependency, no-import-cycle shape of the package intact — this
  module imports nothing from `ctx`, so anything may import it.
* **Environment only, never `ctx.yaml`.** There is deliberately no config key.
  `ctx.yaml` is committed and shared, so a `log:` block would turn one
  engineer's debugging session into a log file on every teammate's machine and
  in CI — the exact "writing logs nobody asked for" failure the first property
  forbids. It could also not report a failure to *read* the config, which is
  one of the failures worth reporting. A support case is a person at a
  terminal for an afternoon, and an environment variable is scoped to exactly
  that.

Usage:

    CTX_LOG=error ctx doctor              # diagnostics to stderr
    CTX_LOG=debug CTX_LOG_FILE=/tmp/ctx.log ctx ci

Lines are not passed through `redact.scrub`: they carry exception text and
paths, they go to a terminal or to a path the operator named, and they are off
unless that operator turned them on. Treat the output like a traceback — read
it before pasting it into a ticket.
"""

import datetime
import os
import sys

#: Sets the level, and by being set at all, turns logging on.
ENV_LEVEL = "CTX_LOG"
#: Optional destination. Unset means stderr, which is where a person running a
#: command with `CTX_LOG=...` is already looking.
ENV_FILE = "CTX_LOG_FILE"

OFF = "off"
ERROR = "error"
WARN = "warn"
INFO = "info"
DEBUG = "debug"

#: Quietest first. `off` is a level rather than a separate flag so that one
#: comparison answers "should this line be emitted".
LEVELS = (OFF, ERROR, WARN, INFO, DEBUG)
_RANK = {name: index for index, name in enumerate(LEVELS)}

# Spellings that are not levels but are obviously intent. `CTX_LOG=1` is what a
# script writes, and refusing it in favour of silence would be the same silent
# demotion this module exists to make impossible.
_ON = {"1", "true", "yes", "on"}
_OFF = {"", "0", "false", "no", "none"}

#: A line is one line. Detail longer than this is cut, because a 40 KB
#: traceback in a diagnostic log is not more informative than its first
#: paragraph — and `hook-errors.log` already keeps the whole thing when it can.
MAX_DETAIL = 600

# A destination left switched on for a week must not become the disk-full
# condition it was turned on to diagnose. Same shape as the hook error log:
# a soft cap, and a trim to the most recent lines when it is crossed.
MAX_BYTES = 512 * 1024
KEEP_LINES = 500

# Whether the "that is not a level" notice has been emitted in this process.
# One line per run, not one per call: the condition is a typo in an
# environment variable, so it is permanent for the life of the process and
# repeating it would bury the diagnostics it is warning about.
_ANNOUNCED = False


def reset_notices():
    """Forget the one-per-process notice. For tests, and for nothing else."""
    global _ANNOUNCED
    _ANNOUNCED = False


def level():
    """The active level, `"off"` when unset. Never raises.

    An unrecognised value turns logging on at `error` rather than leaving it
    off: somebody who exported `CTX_LOG=verbose` wants output, and silently
    giving them none is how a support case ends with "the logging did not
    work". The fallback announces itself, once, on the first line emitted.
    """
    try:
        raw = (os.environ.get(ENV_LEVEL) or "").strip().lower()
    except Exception:  # pragma: no cover - a hostile os.environ
        return OFF
    if raw in _OFF:
        return OFF
    if raw in _RANK:
        return raw
    if raw in _ON:
        return ERROR
    return ERROR


def enabled(severity=ERROR):
    """Would a line at `severity` be emitted? For skipping expensive detail."""
    return _RANK.get(severity, 1) <= _RANK[level()]


def destination():
    """The file lines go to, or None for stderr."""
    try:
        return (os.environ.get(ENV_FILE) or "").strip() or None
    except Exception:  # pragma: no cover - a hostile os.environ
        return None


# The first parameter of every emitter is `site` rather than the more obvious
# `event`, because `event` is the single most useful *field* a caller has to
# pass — `log.failure("hooks.log_error", exc, event="Stop")` — and a keyword
# that collides with a positional parameter is a TypeError raised out of an
# error handler. That is the one shape of bug this module may not have.


def error(site, detail="", **fields):
    """A thing that failed. The level every swallow point reports at."""
    return _emit(ERROR, site, detail, fields)


def warn(site, detail="", **fields):
    """A thing that did not fail but will."""
    return _emit(WARN, site, detail, fields)


def info(site, detail="", **fields):
    return _emit(INFO, site, detail, fields)


def debug(site, detail="", **fields):
    return _emit(DEBUG, site, detail, fields)


def failure(site, exc, **fields):
    """The shape a swallow point uses: an exception, named and typed.

    `except OSError as exc: log.failure("journal.append", exc, path=...)` is
    the whole idiom. Returns whether the line was emitted, which is useful to
    a test and to nobody else — no caller may branch on logging having worked.
    """
    return _emit(ERROR, site, describe(exc), fields)


def describe(exc):
    """`"OSError: Read-only file system"` — the one-line form of an exception.

    Public because a swallow point that reports at `warn` rather than `error`
    still wants the same rendering, and copying two lines of `type(exc)`
    formatting into every such site is how two renderings appear.
    """
    try:
        text = str(exc).strip()
    except Exception:  # pragma: no cover - an exception with a broken __str__
        text = ""
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def _one_line(value, limit=MAX_DETAIL):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format(severity, site, detail, fields):
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    parts = [stamp, f"ctx.{severity}", _one_line(site, 80)]
    if detail:
        parts.append(_one_line(detail))
    for key in sorted(fields):
        value = fields[key]
        if value is not None:
            parts.append(f"{key}={_one_line(value, 200)}")
    return " ".join(parts) + "\n"


def _unrecognised():
    """The raw `CTX_LOG` value when it is not a level at all, else `""`."""
    try:
        raw = (os.environ.get(ENV_LEVEL) or "").strip().lower()
    except Exception:  # pragma: no cover - a hostile os.environ
        return ""
    return "" if raw in _OFF or raw in _RANK or raw in _ON else raw


def _announce():
    """Say once that `CTX_LOG` was not a level, so the fallback is not silent."""
    global _ANNOUNCED
    raw = _unrecognised()
    if not raw or _ANNOUNCED:
        return
    _ANNOUNCED = True
    try:
        line = _format(
            ERROR, "log.level",
            f"{ENV_LEVEL}={raw} is not a level; logging at {ERROR}",
            {"expected": ", ".join(LEVELS)},
        )
    except Exception:  # pragma: no cover - an unformattable environment
        return
    _write(line)


def _emit(severity, site, detail, fields):
    """Format and write one line, or do nothing at all. Never raises."""
    if _RANK.get(severity, 1) > _RANK[level()]:
        return False
    _announce()
    try:
        line = _format(severity, site, detail, fields)
    except Exception:  # pragma: no cover - an unformattable field
        return False
    return _write(line)


def _write(line):
    target = destination()
    try:
        if target is None:
            sys.stderr.write(line)
            sys.stderr.flush()
            return True
        _trim(target)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except Exception:
        # THE ONE CORRECT SWALLOW. An unwritable destination, a closed stderr,
        # a directory where a file was expected: the line is lost and the
        # session is untouched. A logger that can raise is a logger that
        # breaks the thing it observes, which is strictly worse than one that
        # occasionally says nothing.
        return False


def _trim(target):
    """Keep the destination bounded. Best effort; failure is not fatal."""
    try:
        if os.path.getsize(target) <= MAX_BYTES:
            return
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        with open(target, "w", encoding="utf-8") as handle:
            handle.writelines(lines[-KEEP_LINES:])
    except Exception:
        return
