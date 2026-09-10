"""The done-gate: seven verify kinds, cheapest first, short-circuiting.

Two distinctions carry the whole design.

**Work failure vs infrastructure failure.** A criterion that fails is a reason to
block. A verify command that *cannot run* — missing binary, exit 127, timeout —
is a configuration bug, and blocking on it would brick every session in the
project. Infrastructure failures warn and pass.

Which of the two a check is gets decided by *how the launcher failed*, never by
reading the child's output. Sniffing the output was a laundering machine: a unit
that deleted a module produced `No module named 'ctx.foo'` inside pytest's own
report, the gate read that as "the toolchain is missing", and the precise
regression the gate exists to catch became a non-blocking warning. Whether a
tool exists is a question about this machine, so it is answered by asking this
machine — before the command runs (`_launchable`) — and the exit code says the
rest. A project that genuinely wants the old leniency for one check asks for it
by name with `optional: true`.

**Mechanical vs judged.** `diff`, `exists`, `symbol`, `review`, `test_first` and
`cmd` are decidable by a script,
so the `Stop` hook runs them directly and they cost nothing. `rubric` and
`human` need a model or a person, so they are evaluated by `/ctx:verify` and
*recorded* in the work file; the hook only checks whether a recording exists.
Any subsequent edit clears those recordings, so a sign-off cannot outlive the
code it signed off on.

`test_first` is mechanical for the same reason `review` is: the evidence is
already on disk in `.ctx/runtime/snapshots/`, recorded by whatever ran the
tests, and the check only has to read it. A unit with no recorded run at all
fails rather than passing by default — the same call `config.PROFILES` makes
by shipping no default `exists` check, because a default that always passes
is worse than no default at all: it makes an unguarded unit look guarded.

Checks run in cost order and stop at the first failure, which is why a scope
violation never pays for a test run.
"""

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import redact, snapshot, trust

MECHANICAL = ("diff", "exists", "symbol", "review", "test_first", "cmd")
JUDGED = ("rubric", "human")
KINDS = MECHANICAL + JUDGED

# Cheapest first: `diff` is a git call, `exists` is a stat, `review` and
# `test_first` are each one snapshot read, `cmd` is a whole subprocess, and the
# judged kinds cost a model call or a human's attention. `review` sits above
# `symbol` and below `cmd` on purpose — an open Critical finding should stop
# the gate before a test suite runs. `test_first` sits beside `review` for the
# same reason: it is a snapshot read, not a process spawn, so it belongs
# nowhere near `cmd`'s cost even though it is about tests.
COST = {"diff": 0, "exists": 1, "symbol": 2, "review": 3, "test_first": 3,
        "cmd": 4, "rubric": 5, "human": 6}

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
    if kind == "review":
        return "no unaddressed critical or important review findings"
    if kind == "test_first":
        names = [str(n) for n in (check.get("tests") or []) if str(n).strip()]
        return f"a failing run of {', '.join(names) or '<no tests>'} precedes the implementation"
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
    # A committed ctx.yaml is executable shell run by a hook, which never sees a
    # permission prompt. Commands this machine has not accepted are reported, not
    # executed. ERROR rather than FAIL: an unaccepted ledger is ungated, never
    # broken.
    accepted = trust.load(layout)

    for check in ordered(checks):
        kind = check["kind"]
        if kind == "diff":
            result = _check_diff(check, cwd, owns)
        elif kind == "review":
            result = _check_review(layout, check, key)
        elif kind == "exists":
            # The deadline, not just the `cmd` branch's: `matches` is a regex
            # from a committed file and can backtrack for ever.
            result = _check_exists(check, cwd, deadline)
        elif kind == "symbol":
            result = _check_symbol(check, cwd)
        elif kind == "test_first":
            result = _check_test_first(layout, check, key)
        elif kind == "cmd":
            remaining = deadline - time.monotonic()
            if not trust.is_accepted(check, accepted):
                result = Result("cmd", label_of(check), ERROR, trust.REASON)
            elif remaining <= 0:
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
    """FAIL beats PENDING beats ERROR beats PASS.

    ERROR outranks PASS because an ERROR means *this check did not run*, not
    *this check was satisfied*. Folding it into a PASS is how the gate came to
    sign off on work it had never checked: outside a git repository the `diff`
    kind errors, and a single passing `cmd` beside it was enough to report the
    whole gate green — with the `owns` scope check silently absent. The Stop hook
    still declines to block on ERROR, because infrastructure is not the work's
    fault, but it no longer records it as a pass.
    """
    if any(r.status == FAIL for r in results):
        return FAIL
    if any(r.status == PENDING for r in results):
        return PENDING
    if any(r.status == ERROR for r in results):
        return ERROR
    return PASS


# --------------------------------------------------------------------------- #
# individual kinds
# --------------------------------------------------------------------------- #

LEDGER_PREFIX = ".ctx/"


def is_ledger(path):
    """Ledger bookkeeping, which is never a scope violation.

    Every `ctx` command appends to the journal and flips a `status:` field, so
    the ledger's own files change constantly and are owned by no unit. Counting
    them made the scope check fail on work that was entirely in scope — and the
    merge preflight already knew this (`worktree._is_ledger`, found the hard way)
    while the gate did not.
    """
    return str(path).replace("\\", "/").startswith(LEDGER_PREFIX)


def _check_diff(check, cwd, owns):
    scope = [str(p) for p in (check.get("owns") or owns or [])]
    if not scope:
        return Result("diff", label_of(check), PASS, "no owned scope declared")
    changed, error = changed_files(cwd)
    if error:
        return Result("diff", label_of(check), ERROR, error)
    stray = [path for path in changed
             if not is_ledger(path) and not _within(path, scope)]
    if stray:
        return Result(
            "diff", label_of(check), FAIL,
            "changed outside owned scope: " + ", ".join(sorted(stray)[:8]),
        )
    return Result("diff", label_of(check), PASS)


def _check_review(layout, check, key):
    """Blocking findings hold the gate shut.

    The findings store is the durable half of the review loop: a reviewer's
    verdict written to a file outlives the session that produced it, so a gate
    running days later still knows the change was never signed off. `minor`
    findings are recorded and deferred, never blocking — a loop that reruns for
    "coverage could be broader" is a loop people learn to bypass.

    `parked` and `disputed` do not block either, but neither is free: parking
    demands a ruling and disputing demands evidence, both enforced by
    `findings.set_status`. There is deliberately no "acknowledged" state.
    """
    from . import findings as findings_mod

    slug = str(check.get("plan") or "")
    unit = str(check.get("unit") or "")
    if not slug or not unit:
        # The gate resolves these from the active work; a check that names
        # neither and is not running against a unit has nothing to judge.
        slug, unit = _review_target(check, key, slug, unit)
    if not slug or not unit:
        return Result("review", label_of(check), ERROR,
                      "no unit to review — `review` applies to a plan unit")
    ledger = findings_mod.load(layout, slug, unit)
    blocking = ledger.blocking()
    if not blocking:
        return Result("review", label_of(check), PASS, ledger.summary())
    return Result(
        "review", label_of(check), FAIL,
        "%d finding(s) open: %s" % (
            len(blocking),
            "; ".join("[%s] %s — %s" % (f.id, f.severity, f.summary)
                      for f in blocking[:4]),
        ),
    )


def _review_target(check, key, slug, unit):
    """`key` is `<plan>/<unit>` for a unit gate, or a bare name for a task."""
    if slug and unit:
        return slug, unit
    text = str(key or "")
    for prefix in ("ci-", "merge-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if "/" in text:
        left, right = text.split("/", 1)
        return slug or left, unit or right
    return slug, unit or text


def _check_exists(check, cwd, deadline=None):
    raw = str(check.get("path") or "")
    if not raw:
        return Result("exists", label_of(check), ERROR, "no path configured")
    target, refusal = _confined(raw, check, cwd)
    if refusal:
        # Before the stat, so the verdict cannot describe a file we refused to
        # look at. ERROR, not FAIL: a refused path is a configuration bug.
        return Result("exists", raw, ERROR, refusal)
    if not os.path.exists(target):
        return Result("exists", raw, FAIL, "path does not exist")
    pattern = check.get("matches")
    if pattern and os.path.isfile(target):
        try:
            body = Path(target).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Result("exists", raw, ERROR, str(exc))
        found, problem = _match_within(str(pattern), body, _remaining(deadline))
        if problem:
            return Result("exists", raw, ERROR, problem)
        if not found:
            return Result("exists", raw, FAIL, f"does not match /{pattern}/")
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
    target, refusal = _confined(raw, check, cwd)
    if refusal:
        # `contains` is echoed back in this kind's failure message, so an
        # unconfined path turns the gate into a grep over the whole disk.
        return Result("symbol", raw, ERROR, refusal)
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


def _check_test_first(layout, check, key):
    """Red before green, checked from the same snapshot the reviewer already reads.

    "The implementation snapshot" is the manifest captured under this key — its
    `taken_at` is the moment the code was fixed in place. A recorded test run is
    evidence, not a claim: `ctx.snapshot.record_test_run` writes it next to that
    manifest, so nothing here re-executes a test or trusts a caller's say-so.

    Three ways to fail on purpose. No snapshot yet is a setup problem (ERROR):
    nothing has been captured to compare against. No run recorded that touches
    these test paths is a FAIL, not a pass — a unit nobody ever ran the tests
    for must not look done. And a run recorded, but only after the
    implementation snapshot, is also a FAIL: the failing test never came first,
    so nothing proves the code was written to satisfy it rather than around it.
    """
    label = label_of(check)
    tests = [str(p) for p in (check.get("tests") or []) if str(p).strip()]
    if not tests:
        return Result("test_first", label, ERROR, "needs `tests`: the paths whose failing run must precede the implementation")
    snap_key = str(check.get("key") or key or "")
    if not snap_key:
        return Result("test_first", label, ERROR, "no unit to check — `test_first` needs a snapshot key")
    manifest = snapshot.load(layout, snap_key)
    if manifest is None:
        return Result(
            "test_first", label, ERROR,
            f"no snapshot captured for {snap_key} — capture one before verifying test-first",
        )
    implemented_at = float(manifest.get("taken_at") or 0)
    runs = [r for r in snapshot.test_runs(layout, snap_key)
            if isinstance(r, dict) and _run_covers(r, tests)]
    if not runs:
        return Result(
            "test_first", label, FAIL,
            "no recorded test run touches " + ", ".join(tests)
            + " — an unrun test is not evidence; record a run before this can pass",
        )
    failing_before = [
        r for r in runs
        if int(r.get("exit_code", 0) or 0) != 0
        and float(r.get("at", 0) or 0) < implemented_at
    ]
    if not failing_before:
        return Result(
            "test_first", label, FAIL,
            "the implementation snapshot precedes every recorded run of "
            + ", ".join(tests)
            + " — no failing run was captured before the code; write the test red first",
        )
    return Result("test_first", label, PASS)


def _run_covers(run, tests):
    """Whether a recorded run's paths overlap the paths this check cares about."""
    paths = run.get("paths") if isinstance(run, dict) else None
    return any(snapshot.covers(p, tests) for p in (paths or ()))


def resolve_cwd(check, cwd):
    """Where a check runs. `(path, error)`; the error is a configuration bug.

    Without this every command ran at the ledger's parent, so a monorepo whose
    ledger sits at the root could not say "run `npm test` in `apps/web`" — which
    ruled out most large repositories outright.
    """
    raw = str(check.get("cwd") or "").strip()
    if not raw:
        return str(cwd), ""
    target = raw if os.path.isabs(raw) else os.path.join(str(cwd), raw)
    if not os.path.isdir(target):
        return str(cwd), f"cwd {raw!r} is not a directory"
    return target, ""


def _confined(raw, check, cwd):
    """A checked path resolved inside the project. `(target, refusal)`.

    `exists` and `symbol` execute nothing, so trust never enters this — what
    made them dangerous was reach, not execution. Both report a verdict about a
    file's *contents*, and both honoured absolute paths, so a committed
    `ctx.yaml` could say `{kind: exists, path: /home/victim/.ssh/id_rsa,
    matches: "BEGIN OPENSSH"}` and read PASS/FAIL as a one-bit oracle over any
    file the developer can read — one bit per gate run, with `symbol`'s message
    echoing the probe string back into the transcript. Confining the path is
    what removes the oracle, so it is done before the file is touched at all.

    `realpath` on both sides, not string surgery: `..` after `cwd:` is applied,
    and a symlink whose target leaves the project, both have to be caught, and
    only the resolved pair can catch them.
    """
    if raw.startswith("~") or os.path.isabs(raw) or (os.name == "nt" and ":" in raw):
        return "", ("path must be relative to the project — "
                    "the gate refuses to read outside it")
    base, problem = resolve_cwd(check, cwd)
    if problem:
        return "", problem
    root = os.path.realpath(str(cwd))
    target = os.path.realpath(os.path.join(base, raw))
    if not _inside(target, root):
        return "", "path must be inside the project — the gate refuses to read outside it"
    return target, ""


def _inside(target, root):
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:  # different drives on Windows, or a mix of abs and rel
        return False


def _remaining(deadline):
    """Seconds left of the gate's shared budget. `None` means no budget given."""
    if deadline is None:
        return None
    return deadline - time.monotonic()


# 0 matched, 3 did not; anything else is the child failing to answer.
_SEARCH_PROBE = (
    "import re, sys\n"
    "raw = sys.stdin.buffer.read().decode('utf-8', 'replace')\n"
    "pattern, _, body = raw.partition('\\0')\n"
    "sys.exit(0 if re.search(pattern, body) else 3)\n"
)


def _match_within(pattern, body, budget):
    """`matches`, bounded by the gate's clock. `(matched, problem)`.

    A pattern from a committed `ctx.yaml` is attacker-controlled input to a
    backtracking engine, and `re` has no timeout: `(a+)+$` against a few hundred
    characters ran for minutes *inside the Stop hook*, which is the one place in
    the product where hanging is worse than failing — a killed hook returns no
    decision at all. Python cannot interrupt a running `re.search` (it holds the
    GIL, so a worker thread cannot be timed out either), so the search runs in a
    child process that can actually be killed, and it gets whatever is left of
    the same budget the `cmd` kind spends.
    """
    try:
        re.compile(pattern)
    except re.error as exc:
        return False, f"bad pattern: {exc}"
    if "\0" in pattern:
        return False, "pattern may not contain a NUL byte"
    if budget is None:
        budget = 30.0
    if budget <= 0:
        return False, ("the gate's budget was spent before this check ran "
                       "— split the suite or raise gate.timeout_seconds")
    if not sys.executable:
        # No interpreter to fork: an unbounded search is still better than
        # silently skipping the check, and this path is not reachable from a
        # normal install.
        return bool(re.search(pattern, body)), ""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _SEARCH_PROBE],
            input=(pattern + "\0" + body).encode("utf-8", "replace"),
            capture_output=True, timeout=budget,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"pattern /{pattern}/ did not finish within the gate's remaining "
            f"{round(budget)}s — it backtracks; rewrite it or raise "
            "gate.timeout_seconds"
        )
    except OSError as exc:
        return False, f"could not evaluate pattern: {exc}"
    if completed.returncode == 0:
        return True, ""
    if completed.returncode == 3:
        return False, ""
    detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
    last = detail.splitlines()[-1] if detail else f"exit {completed.returncode}"
    return False, f"could not evaluate pattern: {last}"


def _check_env(check):
    """Extra environment for a check, layered over the session's own."""
    extra = check.get("env")
    if not isinstance(extra, dict) or not extra:
        return None
    merged = dict(os.environ)
    merged.update({str(k): str(v) for k, v in extra.items()})
    return merged


def _check_cmd(layout, check, cwd, key, timeout, head, tail, patterns=()):
    command = str(check.get("run") or "")
    if not command:
        return Result("cmd", "<none>", ERROR, "no command configured")
    where, problem = resolve_cwd(check, cwd)
    if problem:
        return Result("cmd", command, ERROR, problem)
    # Asked of the machine, before anything runs. `python3 -m pytest` with
    # pytest absent exits 1 from an interpreter that is very much present, so
    # the exit code alone cannot answer it — but the question ("can this
    # start?") is about the environment, and the environment can be asked
    # directly instead of guessed at from the child's output afterwards.
    launchable, why = _launchable(command, timeout, where, _check_env(check))
    if not launchable:
        return Result("cmd", command, ERROR, f"tool not available: {why}")
    try:
        completed = subprocess.run(
            command, shell=True, cwd=where, capture_output=True,
            text=True, timeout=timeout, env=_check_env(check),
        )
    except subprocess.TimeoutExpired:
        # Infrastructure, not work: a hung command must not block forever.
        return Result("cmd", command, ERROR, f"timed out after {round(timeout)}s")
    except OSError as exc:
        # The launcher never started: no shell, no such directory, fork failed.
        return Result("cmd", command, ERROR,
                      f"could not run: {exc} ({_program(command)})")

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        return Result("cmd", command, PASS)
    if completed.returncode in _NOT_FOUND_EXITS:
        # The shell's own verdict, not the child's: 127 from a POSIX shell and
        # 9009 from cmd.exe both mean nothing was ever launched.
        return Result("cmd", command, ERROR,
                      f"command not found (exit {completed.returncode}): "
                      f"{_program(command)}")
    if check.get("optional") is True:
        # The one place the old output-sniffing survives, and only because a
        # project asked for it by name on this check. Everywhere else it turned
        # a deleted module — `No module named 'ctx.foo'` in pytest's own output —
        # into a warning about the toolchain.
        missing = _missing_tool(output)
        if missing:
            return Result("cmd", command, ERROR,
                          f"tool not available: {missing} (optional check)")

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


# A POSIX shell reports "not found" as 127; cmd.exe reports it as 9009. Both are
# the launcher speaking, before any child of ours existed.
_NOT_FOUND_EXITS = (127, 9009)

# Shell syntax we cannot reason about statically. A command containing any of it
# is left alone: the pre-flight declines to answer rather than answering wrongly,
# and the exit code decides.
_SHELL_META = "|&;<>()$`\n"


def _argv(command):
    """The command's leading tokens, or `()` when the shell is doing more than
    running one program."""
    if any(ch in command for ch in _SHELL_META):
        return ()
    try:
        parts = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:  # an unbalanced quote; let the shell have its opinion
        return ()
    return tuple(part.strip('"') for part in parts if part)


def _program(command):
    """The name a user would recognise as "the tool", for a message."""
    parts = _argv(command)
    if parts:
        return parts[0]
    return str(command).strip().split(" ")[0].strip('"') or "<empty>"


def _launchable(command, budget, where=None, env=None):
    """Can this command start at all? `(launchable, why_not)`.

    Deliberately one-sided. It answers "no" only about the environment — an
    interpreter that cannot import the module it was asked to run — and says
    nothing about whether the command will pass. Every ambiguous case answers
    "yes", because a wrong "no" is the failure this replaced: it downgrades a
    real regression to a warning.

    It does not second-guess PATH. `shutil.which` returns nothing for `exit`,
    `cd` or `[`, which are shell builtins and run perfectly well; the shell
    already reports a name it cannot find as exit 127, and that verdict comes
    from the launcher rather than from a guess about it.
    """
    parts = _argv(command)
    if not parts:
        return True, ""
    program = parts[0]
    # `python -m module`: the interpreter exists, the module may not.
    if os.path.basename(program).lower().startswith("python") and "-m" in parts[1:3]:
        index = parts.index("-m")
        module = parts[index + 1] if len(parts) > index + 1 else ""
        if module and not module.startswith("-"):
            return _can_import(program, module, budget, where, env)
    return True, ""


_IMPORT_PROBE = (
    "import importlib.util, sys\n"
    "try:\n"
    "    found = importlib.util.find_spec(sys.argv[1]) is not None\n"
    "except Exception:\n"
    "    found = False\n"
    "sys.exit(0 if found else 3)\n"
)


def _can_import(interpreter, module, budget, where=None, env=None):
    """Whether `interpreter -m module` has a module to run.

    The module name goes in `argv`, never into the probe's source: this string
    comes from a committed `ctx.yaml`, and interpolating it would hand a pull
    request the ability to run code on the reviewer's machine merely by *not*
    running the check.
    """
    try:
        completed = subprocess.run(
            [interpreter, "-c", _IMPORT_PROBE, module],
            capture_output=True, timeout=max(1.0, min(20.0, float(budget or 20))),
            cwd=where, env=env,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return True, ""  # could not ask; let the command answer for itself
    if completed.returncode == 3:
        return False, f"{interpreter} cannot import {module}"
    return True, ""


# Signatures of "the tool isn't installed", which exit non-zero without the work
# being wrong. Consulted only for a check that declares `optional: true`: read
# against an arbitrary test run they match the work's own failures — a deleted
# module, a log line quoting a shell error — which is how a regression came to
# be reported as a missing toolchain.
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
        if fnmatch.fnmatchcase(normalised, cleaned):
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
