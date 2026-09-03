"""The done-gate: six verify kinds, cheapest first, short-circuiting.

Two distinctions carry the whole design.

**Work failure vs infrastructure failure.** A criterion that fails is a reason to
block. A verify command that *cannot run* — missing binary, exit 127, timeout —
is a configuration bug, and blocking on it would brick every session in the
project. Infrastructure failures warn and pass.

**Mechanical vs judged.** `diff`, `exists`, `symbol` and `cmd` are decidable by a
script,
so the `Stop` hook runs them directly and they cost nothing. `rubric` and
`human` need a model or a person, so they are evaluated by `/ctx:verify` and
*recorded* in the work file; the hook only checks whether a recording exists.
Any subsequent edit clears those recordings, so a sign-off cannot outlive the
code it signed off on.

Checks run in cost order and stop at the first failure, which is why a scope
violation never pays for a test run.
"""

import os
import re
import subprocess
import time
from pathlib import Path

from . import redact

MECHANICAL = ("diff", "exists", "symbol", "cmd")
JUDGED = ("rubric", "human")
KINDS = MECHANICAL + JUDGED

# Cheapest first: `diff` is a git call, `exists` is a stat, `cmd` is a whole
# subprocess, and the judged kinds cost a model call or a human's attention.
COST = {"diff": 0, "exists": 1, "symbol": 2, "cmd": 3, "rubric": 4, "human": 5}

PASS, FAIL, ERROR, PENDING = "pass", "fail", "error", "pending"


class Result:
    def __init__(self, kind, label, status, message="", log_path=None):
        self.kind = kind
        self.label = label
        self.status = status
        self.message = message
        self.log_path = log_path

    def __repr__(self):
        return f"<{self.kind} {self.label} {self.status}>"

    def line(self):
        icon = {PASS: "ok", FAIL: "FAIL", ERROR: "warn", PENDING: "pending"}[self.status]
        text = f"  {icon:<8}{self.kind}: {self.label}"
        if self.message:
            text += f"\n            {self.message.splitlines()[0][:120]}"
        return text


def ordered(checks):
    """Checks sorted cheapest-first, dropping anything malformed."""
    valid = [c for c in (checks or []) if isinstance(c, dict) and c.get("kind") in KINDS]
    return sorted(valid, key=lambda c: COST[c["kind"]])


def label_of(check):
    kind = check.get("kind")
    if kind == "cmd":
        return str(check.get("run") or "<no command>")
    if kind == "exists":
        return str(check.get("path") or "<no path>")
    if kind == "symbol":
        names = check.get("contains") or []
        return f"{check.get('path') or '?'} still provides {', '.join(map(str, names))}"
    if kind == "diff":
        return "changed files within owned scope"
    if kind == "rubric":
        return str(check.get("about") or "criteria judged against the diff")
    return str(check.get("about") or "explicit sign-off")


def run(layout, config, checks, *, cwd, key, owns=(), recorded=(), judged=False):
    """Run checks in cost order, stopping at the first blocking failure.

    `judged=False` (the hook's mode) does not evaluate `rubric`/`human`; it only
    reports them PENDING unless their kind appears in `recorded`.

    `gate.timeout_seconds` is the budget for the **whole run**, not for each
    command. Per-command it was unenforceable: three commands at 240s each can
    run for twelve minutes against a Stop hook the harness kills at five, and a
    killed hook returns no decision at all — so an over-long suite silently
    stopped gating anything. Spending one shared budget makes that case an
    explicit ERROR instead.

    Returns (results, verdict).
    """
    results = []
    gate = config.get("gate") or {}
    budget = max(1, int(gate.get("timeout_seconds", 240)))
    head = int(gate.get("output_head", 40))
    tail = int(gate.get("output_tail", 20))
    patterns = config.get("redact") or []
    deadline = time.monotonic() + budget

    for check in ordered(checks):
        kind = check["kind"]
        if kind == "diff":
            result = _check_diff(check, cwd, owns)
        elif kind == "exists":
            result = _check_exists(check, cwd)
        elif kind == "symbol":
            result = _check_symbol(check, cwd)
        elif kind == "cmd":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = Result(
                    "cmd", label_of(check), ERROR,
                    f"the gate's {budget}s budget was spent before this check ran "
                    "— split the suite or raise gate.timeout_seconds",
                )
            else:
                result = _check_cmd(
                    layout, check, cwd, key, remaining, head, tail, patterns
                )
        else:
            result = _check_judged(check, kind, recorded, judged)
        results.append(result)
        if result.status == FAIL:
            break  # short-circuit: nothing more expensive needs to run

    return results, verdict_of(results)


def verdict_of(results):
    if any(r.status == FAIL for r in results):
        return FAIL
    if any(r.status == PENDING for r in results):
        return PENDING
    if results and all(r.status == ERROR for r in results):
        return ERROR
    return PASS


# --------------------------------------------------------------------------- #
# individual kinds
# --------------------------------------------------------------------------- #

def _check_diff(check, cwd, owns):
    scope = [str(p) for p in (check.get("owns") or owns or [])]
    if not scope:
        return Result("diff", label_of(check), PASS, "no owned scope declared")
    changed, error = changed_files(cwd)
    if error:
        return Result("diff", label_of(check), ERROR, error)
    stray = [path for path in changed if not _within(path, scope)]
    if stray:
        return Result(
            "diff", label_of(check), FAIL,
            "changed outside owned scope: " + ", ".join(sorted(stray)[:8]),
        )
    return Result("diff", label_of(check), PASS)


def _check_exists(check, cwd):
    raw = str(check.get("path") or "")
    if not raw:
        return Result("exists", label_of(check), ERROR, "no path configured")
    target = os.path.join(str(cwd), raw) if not os.path.isabs(raw) else raw
    if not os.path.exists(target):
        return Result("exists", raw, FAIL, "path does not exist")
    pattern = check.get("matches")
    if pattern and os.path.isfile(target):
        try:
            body = Path(target).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Result("exists", raw, ERROR, str(exc))
        try:
            if not re.search(str(pattern), body):
                return Result("exists", raw, FAIL, f"does not match /{pattern}/")
        except re.error as exc:
            return Result("exists", raw, ERROR, f"bad pattern: {exc}")
    return Result("exists", raw, PASS)


def _check_symbol(check, cwd):
    """Interface freeze: every named signature must still appear verbatim.

    Crude on purpose. It catches the two dangerous cases — a signature renamed or
    deleted while a sibling unit is coding against it — without needing a parser
    per language, and it costs one file read.
    """
    raw = str(check.get("path") or "")
    names = [str(n) for n in (check.get("contains") or []) if str(n).strip()]
    if not raw or not names:
        return Result("symbol", label_of(check), ERROR, "needs `path` and `contains`")
    target = os.path.join(str(cwd), raw) if not os.path.isabs(raw) else raw
    if not os.path.exists(target):
        return Result("symbol", raw, FAIL, "file does not exist")
    try:
        body = Path(target).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Result("symbol", raw, ERROR, str(exc))
    missing = [name for name in names if name not in body]
    if missing:
        return Result(
            "symbol", raw, FAIL,
            "no longer provides: " + ", ".join(missing)
            + " — a sibling unit is coding against this, so changing it is a planning "
              "decision. Report it instead of adjusting the check.",
        )
    return Result("symbol", raw, PASS)


def _check_cmd(layout, check, cwd, key, timeout, head, tail, patterns=()):
    command = str(check.get("run") or "")
    if not command:
        return Result("cmd", "<none>", ERROR, "no command configured")
    try:
        completed = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # Infrastructure, not work: a hung command must not block forever.
        return Result("cmd", command, ERROR, f"timed out after {round(timeout)}s")
    except OSError as exc:
        return Result("cmd", command, ERROR, f"could not run: {exc}")

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        return Result("cmd", command, PASS)
    if completed.returncode == 127:
        return Result("cmd", command, ERROR, "command not found (exit 127)")
    missing = _missing_tool(output)
    if missing:
        # `python3 -m pytest` with pytest absent exits 1, not 127 — the
        # interpreter ran fine. Treating that as a work failure would block every
        # session in a project whose toolchain simply is not installed.
        return Result("cmd", command, ERROR, f"tool not available: {missing}")

    # The log stays raw: it is gitignored, machine-local, and redacting it would
    # hide the very line someone is debugging. The excerpt does not — it is
    # inlined into the model's context and from there into every transcript and
    # downstream log, which is exactly the path `redact` exists to guard.
    log_path = _write_log(layout, key, command, output)
    excerpt = redact.scrub(truncate(output, head, tail), patterns)
    message = f"exit {completed.returncode}\n{excerpt}"
    if log_path is not None:
        message += f"\n(full output: {log_path})"
    return Result("cmd", command, FAIL, message, log_path)


# Signatures of "the tool isn't installed", which exit non-zero without the work
# being wrong. Anchored to line starts where possible to avoid matching a test
# that legitimately asserts on one of these strings.
_MISSING_TOOL = (
    re.compile(r"No module named (\S+)"),
    re.compile(r"(?m)^.*?([\w.-]+): (?:command )?not found"),
    re.compile(r"npm (?:ERR!|error) Missing script: \"?([^\"\n]+)"),
    re.compile(r"executable file not found.*?([\w.-]+)"),
    re.compile(r"'([\w.-]+)' is not recognized as an internal or external command"),
    re.compile(r"(?m)^error: unrecognized subcommand '([^']+)'"),
    re.compile(r"npm (?:ERR!|error) could not determine executable to run"),
)


def _missing_tool(output):
    """The name of an absent tool, or "" when the failure is about the work."""
    text = output or ""
    for pattern in _MISSING_TOOL:
        match = pattern.search(text)
        if match:
            return (match.group(1) if match.groups() else "not installed").strip()
    return ""


def _check_judged(check, kind, recorded, judged):
    label = label_of(check)
    if kind in set(recorded or ()):
        return Result(kind, label, PASS, "recorded")
    if not judged:
        instruction = (
            "run /ctx:verify to have a verifier judge this"
            if kind == "rubric" else
            "needs explicit sign-off: /ctx:verify --sign-off"
        )
        return Result(kind, label, PENDING, instruction)
    return Result(kind, label, PENDING, "awaiting evaluation")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def changed_files(cwd):
    """Repo-relative paths with uncommitted changes. (paths, error_message)."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(cwd), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"git unavailable: {exc}"
    if completed.returncode != 0:
        return [], "not a git repository"
    paths = []
    for line in (completed.stdout or "").splitlines():
        entry = line[3:].strip() if len(line) > 3 else ""
        if " -> " in entry:  # renames report "old -> new"
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip('"')
        if entry:
            paths.append(entry)
    return paths, ""


def _within(path, scope):
    import fnmatch

    normalised = path.replace(os.sep, "/")
    for pattern in scope:
        cleaned = str(pattern).replace(os.sep, "/").rstrip("/")
        if not cleaned:
            continue
        if fnmatch.fnmatch(normalised, cleaned):
            return True
        if normalised == cleaned or normalised.startswith(cleaned + "/"):
            return True
    return False


def truncate(text, head, tail):
    """Head + tail lines. Gate failures retry up to 3×, so this is load-bearing."""
    lines = (text or "").strip().splitlines()
    if len(lines) <= head + tail:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head] + [f"… {omitted} lines omitted …"] + (lines[-tail:] if tail else [])
    )


def _write_log(layout, key, command, output):
    try:
        layout.verify_logs.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(key or "check")).strip("-") or "check"
        path = layout.verify_logs / f"{safe}.log"
        path.write_text(f"$ {command}\n\n{output}", encoding="utf-8")
        return layout.rel(path)
    except OSError:
        return None


def summarise(results, limit=3):
    """A compact block for the model. Only failures need detail."""
    failures = [r for r in results if r.status == FAIL]
    pending = [r for r in results if r.status == PENDING]
    lines = []
    for result in failures[:limit]:
        lines.append(f"{result.kind} failed — {result.label}")
        if result.message:
            lines.append(result.message)
    for result in pending[:limit]:
        lines.append(f"{result.kind} pending — {result.label}: {result.message}")
    warnings = [r for r in results if r.status == ERROR]
    if warnings:
        lines.append(
            "not blocking (configuration, not your work): "
            + "; ".join(f"{r.kind} {r.label} — {r.message}" for r in warnings[:3])
        )
    return "\n".join(lines)
