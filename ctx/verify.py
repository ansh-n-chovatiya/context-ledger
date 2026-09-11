"""The done-gate: eight verify kinds, cheapest first, short-circuiting.

A kind is one entry in `KIND_TABLE` — its cost, its label and its runner — and
nothing outside that dict knows which kinds exist. `KINDS`, `MECHANICAL`,
`JUDGED` and `COST` are all read off it.

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

**The `diff` kind reads history, not just the working tree.** `git status` sees
uncommitted work only, so a runner that committed an edit outside its `owns`
walked straight past the check meant to police it. The caller passes `since=` —
the commit recorded in the unit's dispatch seal — and the check diffs that
commit against HEAD as well. It also takes `wave=`: the paths a *concurrent
sibling* declared, which are that sibling's business and not this unit's scope
violation. Both are passed into `run` rather than looked up by it: `plan` and
`review` import this module, so anything that asks them a question must reach
for them late, inside the function. `gate_check` and `verify_plan`, at the foot
of this file, are the two places that do — they are the gate's entry points,
and they live here so that `ctx ci` and the done-gate cannot drift apart on
what a unit's scope is.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import MappingProxyType, ModuleType

from . import atomic, paths, redact, snapshot, trust

PASS, FAIL, ERROR, PENDING = "pass", "fail", "error", "pending"


class Kind:
    """One verify kind, entire: what it costs, what it is called, how it runs.

    Adding a kind used to cost four coordinated edits — the `MECHANICAL` or
    `JUDGED` tuple, the `COST` map, the if-ladder in `label_of` and the
    if-ladder in `run` — and forgetting one failed silently in both directions.
    Miss the kinds tuple and `ordered` drops the check on the floor, so the
    gate returns PASS having run nothing. Miss the ladder in `run` and the
    check falls through to the judged branch and reports PENDING for ever.
    Both of those look like an answer, which is the worst thing a gate can do.

    So a kind is one object in one dict, `KIND_TABLE`. `cost`, `label` and
    `run` are the three things the gate asks of every kind; `judged` is the one
    distinction between the two families — a judged kind needs a model or a
    person, so the hook only checks whether a recording exists.

    `run(check, ctx)` gets the check and the `_Run` context of the gate pass it
    belongs to: everything the old ladder could see from its enclosing scope,
    in one argument, so a new kind reaches what it needs without anyone
    changing a call shape. `label(check)` is one line for the terminal.
    """

    __slots__ = ("cost", "label", "run", "judged")

    def __init__(self, cost, label, run, judged=False):
        self.cost = cost
        self.label = label
        self.run = run
        self.judged = judged

    def __repr__(self):
        return f"<Kind cost={self.cost} judged={self.judged}>"


# `KINDS`, `MECHANICAL`, `JUDGED` and `COST` still exist and still read the
# same, but they are *derived* from `KIND_TABLE` rather than maintained beside
# it — see `__getattr__` at the foot of this module. The table itself is
# defined below the kind implementations it names, because it names them.


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
    valid = [c for c in (checks or [])
             if isinstance(c, dict) and c.get("kind") in KIND_TABLE]
    return sorted(valid, key=lambda check: KIND_TABLE[check["kind"]].cost)


def label_of(check):
    """One line describing what this check asserts.

    An unrecognised kind falls back to the sign-off wording rather than
    raising: `label_of` is called on checks that never reach `ordered` (the
    phase guard in `cli`, the skipped-checks summary in `worktree`), and a
    label is not the place to refuse a malformed check.
    """
    entry = KIND_TABLE.get(check.get("kind"))
    return (entry.label if entry is not None else _label_sign_off)(check)


def run(layout, config, checks, *, cwd, key, owns=(), recorded=(), judged=False,
        since=None, wave=(), share=None):
    """Run checks in cost order, stopping at the first blocking failure.

    `judged=False` (the hook's mode) does not evaluate `rubric`/`human`; it only
    reports them PENDING unless their kind appears in `recorded`.

    `since` is the commit the unit was dispatched at and `wave` the paths its
    concurrent siblings own; both belong to the `diff` kind and are documented
    on `_check_diff`. Defaulted, so a caller that has neither (the Stop hook,
    which gates a turn rather than a unit) gets exactly today's behaviour.

    `gate.timeout_seconds` is the budget for the **whole run**, not for each
    command. Per-command it was unenforceable: three commands at 240s each can
    run for twelve minutes against a Stop hook the harness kills at five, and a
    killed hook returns no decision at all — so an over-long suite silently
    stopped gating anything. Spending one shared budget makes that case an
    explicit ERROR instead.

    `share` is an optional `_SharedCmd` (see `sharing`): a `cmd` result cache
    keyed on tree state, so a wave's units can be gated against one suite run
    instead of N identical ones. Defaulted to None, which is exactly today's
    behaviour — every check runs, every time.

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

    context = _Run(
        layout=layout, config=config, cwd=cwd, key=key, owns=owns,
        recorded=recorded, judged=judged, since=since, wave=wave,
        deadline=deadline, budget=budget, head=head, tail=tail,
        patterns=patterns, accepted=accepted, share=share,
    )
    for check in ordered(checks):
        # `ordered` already dropped every kind the table does not know, so this
        # lookup cannot miss — and there is no `else` branch left for a
        # half-registered kind to fall into and quietly report PENDING from.
        result = KIND_TABLE[check["kind"]].run(check, context)
        results.append(result)
        if result.status == FAIL:
            break  # short-circuit: nothing more expensive needs to run

    return results, verdict_of(results)


class _Run:
    """The state one gate pass shares with every kind it dispatches.

    One argument rather than a per-kind argument list. The if-ladder this
    replaces could reach `layout`, `deadline` and the rest from the enclosing
    scope for free; a table of standalone functions cannot, and threading each
    one through as a parameter would put the call shape back in the business of
    knowing which kinds exist.
    """

    __slots__ = ("layout", "config", "cwd", "key", "owns", "recorded",
                 "judged", "since", "wave", "deadline", "budget", "head",
                 "tail", "patterns", "accepted", "share")

    def __init__(self, *, layout, config, cwd, key, owns, recorded, judged,
                 since, wave, deadline, budget, head, tail, patterns, accepted,
                 share=None):
        self.layout = layout
        self.config = config
        self.cwd = cwd
        self.key = key
        self.owns = owns
        self.recorded = recorded
        self.judged = judged
        self.since = since
        self.wave = wave
        self.deadline = deadline
        self.budget = budget
        self.head = head
        self.tail = tail
        self.patterns = patterns
        self.accepted = accepted
        self.share = share


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

# How a `diff` failure says "the dispatch point is gone", in a form a caller can
# recognise without re-deriving it. `Result.line` shows the first 120 characters
# of a message, which is a terminal width rather than an accident, so the whole
# diagnosis does not fit in one: the SHA and the way out go in the message, and
# the caller that wants to explain *why* keys off this prefix.
MISSING_COMMIT = "dispatch commit"


def is_ledger(path):
    """Ledger bookkeeping, which is never a scope violation.

    Every `ctx` command appends to the journal and flips a `status:` field, so
    the ledger's own files change constantly and are owned by no unit. Counting
    them made the scope check fail on work that was entirely in scope — and the
    merge preflight already knew this (`worktree._is_ledger`, found the hard way)
    while the gate did not.

    The prefix comes from `paths`, which is the only module entitled to know
    what the ledger directory is called. This function used to carry its own
    `".ctx/"` — one of three copies, in three modules, of a name that has to
    agree everywhere for the scope check to mean anything.
    """
    return str(path).replace("\\", "/").startswith(paths.LEDGER_PREFIX)


def _check_diff(check, cwd, owns, since=None, wave=()):
    """Every path this unit changed, against everything it was allowed to change.

    Two inputs widen what "changed" and "allowed" mean, and they pull in
    opposite directions on purpose.

    `since` — the commit recorded in the dispatch seal — is what stops a commit
    hiding an edit. `git status` sees the working tree and nothing else, so
    `git commit` used to erase an out-of-scope change from this check entirely;
    everything between that commit and HEAD now counts too. A `since` that is no
    longer reachable is a **failure**, never a pass: history moved under the
    running unit (an amend, a rebase, a reset), so what it changed can no longer
    be established, and a check that cannot establish that must not sign it off.

    `wave` — the `owns` of the siblings running alongside it — is what stops a
    wave deadlocking on itself. Concurrent `subagent` units share one working
    tree, so a sibling's write to its own declared path is sitting in `git
    status` while this unit's gate runs. Failing on it made the gate unusable
    for any wave of two: the standing workaround was to `git stash` the sibling's
    work, gate, and unstash. Note what is *not* widened: a path no unit in the
    wave declared is still a violation, a sibling that is already `done` excuses
    nothing, and a unit in another wave is not running now, so its `owns` is not
    an excuse either. `review.wave_scope` decides all three and hands the answer
    down; this function only applies it.
    """
    scope = [str(p) for p in (check.get("owns") or owns or [])]
    if not scope:
        return Result("diff", label_of(check), PASS, "no owned scope declared")
    changed, error = changed_files(cwd)
    if error:
        return Result("diff", label_of(check), ERROR, error)
    committed, error, missing = committed_since(cwd, since)
    if missing:
        return Result(
            "diff", label_of(check), FAIL,
            f"{MISSING_COMMIT} {since} is gone from this history — re-record "
            "it: ctx start --reseal",
        )
    if error:
        return Result("diff", label_of(check), ERROR, error)

    seen, everything = set(), []
    for path in list(changed) + list(committed):
        if path not in seen:
            seen.add(path)
            everything.append(path)
    excused = [str(p) for p in (wave or [])]
    stray = [path for path in everything
             if not is_ledger(path)
             and not _within(path, scope)
             and not (excused and _within(path, excused))]
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


# --------------------------------------------------------------------------- #
# one `cmd` run, shared across a wave's gates
# --------------------------------------------------------------------------- #
#
# Every `ctx unit --status done` runs the whole suite. For a wave of three
# units declaring the same `run:`, that is the same subprocess three times over
# bytes that did not move between them. Measured on this repository's own suite
# and its own three-unit wave: 243s gated one unit at a time (82 + 81 + 79),
# 78s with the result shared (78 + 0 + 0). Three spawns become one.
#
# What is *not* shared is everything else the gate does, and that is the point
# of doing this as a result cache rather than as a batched command: `gate_check`
# is untouched, so each unit still gets its own `owns`-scoped `diff`, its own
# contract-seal comparison, its own findings re-seal, its own all-ERROR refusal
# and its own journal line. The only thing reused is the exit status of a
# process that has already been observed to produce it, under three conditions
# that together make the reuse a fact rather than an assumption:
#
#   1. **Same question.** The entry key covers the command, where it runs, the
#      environment overrides on the check, `optional`, and the output-rendering
#      settings that shaped the stored message. Anything else is a different
#      check and gets a different entry.
#   2. **Same tree, to the byte.** `snapshot.tree_token` digests every file's
#      path, contents and executable bit, excluding the ledger (which ctx
#      itself writes on every command). A single changed byte anywhere in the
#      project is a miss.
#   3. **The command changed nothing.** The token is taken before *and* after
#      the run, and a command that left the tree different from how it found it
#      is never cached at all — its second run would start somewhere its first
#      did not.
#
# ERROR is never cached. An ERROR is a verdict about the machine — a missing
# tool, a timeout, a command this ledger has not accepted — not about the work,
# and the machine is the one thing the token cannot see.
#
# Off unless asked for, by `gate.share_cmd_results: true` in ctx.yaml or
# `CTX_GATE_SHARE=1` in the environment of the run that gates the wave. A gate
# that is wrong is worth more than a gate that is fast, so the faster path is
# the one you opt into.

SHARE_CACHE_NAME = "gate-cache.json"
SHARE_SCHEMA = 1

# How long an entry may be reused, and how many are kept. Time is not a
# correctness argument — the token is — but a `cmd` check can depend on
# something outside the tree (a service, a clock, a lockfile resolved from the
# network), and an hour-old verdict about that is worth less than a fresh one.
SHARE_MAX_AGE = 1800
SHARE_MAX_ENTRIES = 64

# Statuses worth reusing. See above: PASS and FAIL are what the command said
# about the tree; ERROR is what the machine said about itself.
SHARE_STATUSES = (PASS, FAIL)


def sharing(layout, config):
    """A `_SharedCmd` when this run may share `cmd` results, else None.

    Asked for by the project (`gate.share_cmd_results: true`) or by the one run
    that gates a wave (`CTX_GATE_SHARE=1`). Neither is the default: see the
    comment above for why the faster gate is the one you opt into.
    """
    gate = config.get("gate") or {}
    enabled = gate.get("share_cmd_results")
    if enabled is None:
        enabled = str(os.environ.get("CTX_GATE_SHARE", "")).strip().lower() \
            in ("1", "true", "yes", "on")
    if not enabled:
        return None
    return _SharedCmd(layout, gate)


class _SharedCmd:
    """`cmd` results already established for this exact tree.

    One instance per gate run, or one shared by a whole `ctx verify --plan`.
    The store is a file under `.ctx/runtime/`, so the saving survives the
    process boundary between two `ctx unit --status done` calls — which is the
    shape a wave is actually gated in.
    """

    def __init__(self, layout, gate=None):
        self.layout = layout
        gate = gate or {}
        self.max_age = max(0, int(gate.get("share_max_age_seconds",
                                           SHARE_MAX_AGE)))

    # -- the tree, as one value ------------------------------------------- #

    def token(self, root=None):
        """The current tree token, or None when the tree cannot be described.

        Recomputed on every call rather than memoised: the whole claim is that
        the tree has not changed, and a cached answer to that question is an
        assumption wearing the answer's clothes. It costs milliseconds against
        a check that costs minutes.
        """
        try:
            return snapshot.tree_token(root or self.layout.root.parent)
        except OSError:
            return None

    # -- the store --------------------------------------------------------- #

    @property
    def path(self):
        return self.layout.runtime / SHARE_CACHE_NAME

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("schema") != SHARE_SCHEMA:
            return {}
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}

    def _save(self, entries):
        if len(entries) > SHARE_MAX_ENTRIES:
            keep = sorted(entries.items(),
                          key=lambda item: item[1].get("at", 0),
                          reverse=True)[:SHARE_MAX_ENTRIES]
            entries = dict(keep)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(
                self.path,
                json.dumps({"schema": SHARE_SCHEMA, "entries": entries},
                           indent=2, sort_keys=True),
            )
        except OSError:
            # A cache that cannot be written is a cache that does not exist.
            pass

    @staticmethod
    def entry_key(check, cwd, head, tail, patterns):
        """One identity for "this exact command, asked this exact way"."""
        shape = json.dumps(
            [
                str(check.get("run") or ""),
                str(check.get("cwd") or ""),
                str(cwd),
                {str(k): str(v) for k, v in (check.get("env") or {}).items()}
                if isinstance(check.get("env"), dict) else {},
                bool(check.get("optional") is True),
                int(head), int(tail), [str(p) for p in (patterns or ())],
            ],
            sort_keys=True,
        )
        return hashlib.sha256(shape.encode("utf-8")).hexdigest()

    # -- the two operations ------------------------------------------------ #

    def lookup(self, key, token):
        """The result this command already produced against this tree, or None.

        Reused verbatim, message and all, so a unit reading a shared refusal
        reads exactly what it would have read had the command run for it. The
        `(full output: …)` pointer names the log the first run wrote — one
        command, one tree, one output, filed once.
        """
        if token is None:
            return None
        entry = self._load().get(key)
        if not isinstance(entry, dict) or entry.get("token") != token:
            return None
        if entry.get("status") not in SHARE_STATUSES:
            return None
        if self.max_age and (time.time() - float(entry.get("at") or 0)
                             > self.max_age):
            return None
        return Result("cmd", str(entry.get("label") or ""),
                      str(entry.get("status")), str(entry.get("message") or ""),
                      entry.get("log_path"))

    def store(self, key, token, result, root=None):
        """Keep a result, but only if the tree is still exactly what it was.

        The token passed in was taken *before* the command ran; this takes one
        after. A command that changed the tree is not cacheable at all: it did
        not answer a question about a tree it left behind.
        """
        if token is None or result.status not in SHARE_STATUSES:
            return False
        if self.token(root) != token:
            return False
        entries = self._load()
        entries[key] = {
            "token": token, "at": time.time(), "status": result.status,
            "label": result.label, "message": result.message,
            "log_path": str(result.log_path) if result.log_path else None,
        }
        self._save(entries)
        return True


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
# the table
# --------------------------------------------------------------------------- #
#
# Everything above implements a kind. Everything below is how the gate finds
# it. The `_label_*` and `_run_*` functions are deliberately thin: they adapt
# the `_check_*` functions, which take the arguments they actually need, to the
# one shape the table dispatches through.

def _label_diff(check):
    return "changed files within owned scope"


def _label_exists(check):
    return str(check.get("path") or "<no path>")


def _label_symbol(check):
    names = check.get("contains") or []
    return f"{check.get('path') or '?'} still provides {', '.join(map(str, names))}"


def _label_review(check):
    return "no unaddressed critical or important review findings"


def _label_test_first(check):
    names = [str(n) for n in (check.get("tests") or []) if str(n).strip()]
    return f"a failing run of {', '.join(names) or '<no tests>'} precedes the implementation"


def _label_cmd(check):
    return str(check.get("run") or "<no command>")


def _label_rubric(check):
    return str(check.get("about") or "criteria judged against the diff")


def _label_sign_off(check):
    return str(check.get("about") or "explicit sign-off")


def _run_diff(check, ctx):
    return _check_diff(check, ctx.cwd, ctx.owns, since=ctx.since, wave=ctx.wave)


def _run_exists(check, ctx):
    # The deadline, not just the `cmd` kind's: `matches` is a regex from a
    # committed file and can backtrack for ever.
    return _check_exists(check, ctx.cwd, ctx.deadline)


def _run_symbol(check, ctx):
    return _check_symbol(check, ctx.cwd)


def _run_review(check, ctx):
    return _check_review(ctx.layout, check, ctx.key)


def _run_test_first(check, ctx):
    return _check_test_first(ctx.layout, check, ctx.key)


def _run_cmd(check, ctx):
    remaining = ctx.deadline - time.monotonic()
    if not trust.is_accepted(check, ctx.accepted):
        return Result("cmd", label_of(check), ERROR, trust.REASON)
    if remaining <= 0:
        return Result(
            "cmd", label_of(check), ERROR,
            f"the gate's {ctx.budget}s budget was spent before this check ran "
            "— split the suite or raise gate.timeout_seconds",
        )
    # Everything above is per-run and stays per-run: trust is this machine's
    # answer and the budget is this gate's. Only the subprocess below is ever
    # shared, and only with a tree that is byte-identical to the one it ran on.
    share, entry, token = ctx.share, None, None
    if share is not None:
        entry = share.entry_key(check, ctx.cwd, ctx.head, ctx.tail, ctx.patterns)
        token = share.token()
        hit = share.lookup(entry, token)
        if hit is not None:
            return hit
    result = _check_cmd(
        ctx.layout, check, ctx.cwd, ctx.key, remaining,
        ctx.head, ctx.tail, ctx.patterns,
    )
    if share is not None:
        share.store(entry, token, result)
    return result


def _run_judged(check, ctx):
    return _check_judged(check, check.get("kind"), ctx.recorded, ctx.judged)


# Cheapest first: `diff` is a git call, `exists` is a stat, `review` and
# `test_first` are each one snapshot read, `cmd` is a whole subprocess, and the
# judged kinds cost a model call or a human's attention. `review` sits above
# `symbol` and below `cmd` on purpose — an open Critical finding should stop
# the gate before a test suite runs. `test_first` sits beside `review` for the
# same reason: it is a snapshot read, not a process spawn, so it belongs
# nowhere near `cmd`'s cost even though it is about tests.
#
# The weights are an ordering, not a budget, so they are only ever meaningful
# relative to each other — which is why changing one is changing which check
# runs first, and why the suite pins all eight by value.
KIND_TABLE = {
    "diff":       Kind(0, _label_diff, _run_diff),
    "exists":     Kind(1, _label_exists, _run_exists),
    "symbol":     Kind(2, _label_symbol, _run_symbol),
    "review":     Kind(3, _label_review, _run_review),
    "test_first": Kind(3, _label_test_first, _run_test_first),
    "cmd":        Kind(4, _label_cmd, _run_cmd),
    "rubric":     Kind(5, _label_rubric, _run_judged, judged=True),
    "human":      Kind(6, _label_sign_off, _run_judged, judged=True),
}

_DERIVED = ("KINDS", "MECHANICAL", "JUDGED", "COST")


def __getattr__(name):
    """`KINDS`, `MECHANICAL`, `JUDGED` and `COST`, read off `KIND_TABLE`.

    Computed on access rather than frozen next to the table, because a value
    frozen at import is a second place to edit — and a kind registered into the
    table afterwards would be costed by a map that had never heard of it. PEP
    562 module `__getattr__` runs only for names this module does not otherwise
    define, so none of these four may be assigned anywhere above.
    """
    if name == "KINDS":
        return tuple(KIND_TABLE)
    if name == "MECHANICAL":
        return tuple(k for k, kind in KIND_TABLE.items() if not kind.judged)
    if name == "JUDGED":
        return tuple(k for k, kind in KIND_TABLE.items() if kind.judged)
    if name == "COST":
        # A proxy, not a copy. `verify.COST["mine"] = 1` against a fresh dict
        # would look exactly like registering a kind and do nothing whatever;
        # this raises instead, and says where the entry belongs.
        return MappingProxyType({k: kind.cost for k, kind in KIND_TABLE.items()})
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_DERIVED))


class _Module(ModuleType):
    """This module, with the four derived names made read-only.

    Not decoration. `verify.KINDS = KINDS + ("mine",)` is the *old* way to add
    a kind, and it does something far worse than fail: it plants a real module
    global, which takes precedence over `__getattr__` for good, so `KINDS`
    freezes at whatever was assigned while `COST` and the table carry on
    moving. Every derived name would then be answering a different question.

    So the half-registration raises, and says where the entry goes. There is
    one way to add a kind and it is `KIND_TABLE`.
    """

    def __setattr__(self, name, value):
        if name in _DERIVED:
            raise AttributeError(
                f"{name} is derived from verify.KIND_TABLE and cannot be "
                f"assigned — register the kind in KIND_TABLE instead"
            )
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Module


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _git(args, cwd):
    """(returncode, stdout, error_message) for one git invocation."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"git unavailable: {exc}"
    return completed.returncode, completed.stdout or "", ""


def committed_since(cwd, commit):
    """Repo-relative paths changed by commits since `commit`.

    `(paths, error_message, missing)`. `missing` is the case worth naming: the
    commit was recorded at dispatch and is no longer part of this history, so it
    was amended, rebased or reset away while the unit ran. There is no honest
    diff to compute from there — the caller fails rather than treating "cannot
    tell" as "nothing changed".

    `missing` is *not* asked as "does this object exist". An amended commit
    still exists in the object database, reachable from the reflog for weeks,
    and `git diff` against it happily returns a tree diff that means nothing:
    after an amend that only reworded the message it is empty, so the check
    would have passed by default in exactly the case it was added for. The
    question that matters is whether the dispatch point is an ancestor of HEAD
    — whether the work still sits on top of what was recorded.

    `commit=None` means no dispatch commit was recorded (an older seal, or a
    dispatch outside git). That is not `missing`: there is nothing to look for.
    """
    if not commit:
        return [], "", False
    code, _out, error = _git(["merge-base", "--is-ancestor", commit, "HEAD"], cwd)
    if error:
        return [], error, False
    if code != 0:
        return [], "", True
    code, out, error = _git(["diff", "--name-only", commit, "HEAD"], cwd)
    if error:
        return [], error, False
    if code != 0:
        return [], f"could not diff against {commit}", False
    return [line.strip() for line in out.splitlines() if line.strip()], "", False


def changed_files(cwd):
    """Repo-relative paths with uncommitted changes. (paths, error_message).

    The working tree only — `committed_since` is the other half, and the `diff`
    check reads both. Kept as it was because `worktree.dirty_paths` wants
    exactly this question answered.
    """
    code, stdout, error = _git(
        ["status", "--porcelain", "--untracked-files=all"], cwd
    )
    if error:
        return [], error
    if code != 0:
        return [], "not a git repository"
    paths = []
    for line in stdout.splitlines():
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


# --------------------------------------------------------------------------- #
# the gate as a whole
# --------------------------------------------------------------------------- #
#
# `verify_plan` (what `ctx verify --plan` runs over every unit of a plan) and
# `gate_before_done` (what stands between a unit and `done`) used to live in
# `cli.py`. Both rebuild the same `run(...)` call — the same `cwd`, the same
# `since` out of the dispatch seal, the same `wave` out of `review.wave_scope`
# — and two copies of one call shape, in a module the gate's other callers
# cannot import, is how the two come to drift. A headless plan run and the
# done-gate disagreeing about what a unit's scope is would be a disagreement
# nobody sees until it excuses a real violation. They belong beside `run`.
#
# Nothing here imports `cli`: the direction of that dependency is the reason
# this module can be imported by `plan`, `hooks` and `worktree` at all. What
# `cli` contributed was `print` with a Windows fallback, which is `echo` below.


def echo(*parts):
    """`print`, but a console that cannot spell a character loses the character
    rather than the command.

    Windows resolves piped stdout to the legacy code page — cp1252 on the CI
    runners — and this CLI's output is full of `→`, `·` and `—`. Encoding one
    of those raises `UnicodeEncodeError` from inside `print`, which aborts the
    whole command: `ctx status` exited 1 on Windows for no reason worse than an
    arrow marking the active unit. Degrading the glyph is the right trade; a
    status board that cannot be read at all is not.

    It lives here rather than in `cli` because the two gate entry points below
    print, and this module may not import `cli`. `cli._echo` calls through to
    it, so there is still exactly one implementation.
    """
    try:
        print(*parts)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(*[str(part).encode(encoding, "replace").decode(encoding)
                for part in parts])


def verify_plan(layout, config, slug):
    """Every unit in a plan, headless. This is what CI runs.

    Mechanical checks only: `rubric` and `human` need a model or a person, so an
    unattended run reports them pending rather than pretending to judge them.
    """
    # Imported here, not at the top: `plan` imports this module, and
    # `contract` reaches it through `review` -> `plan`. By the time any of
    # these runs the packages are loaded, so the cost is a dict lookup.
    from . import (contract as contract_mod, journal,
                   plan as plan_mod, review as review_mod)

    grouped, problems = plan_mod.check(layout, slug)
    if problems:
        echo(f"plan {slug}: {len(problems)} problem(s) — not verifying units")
        for problem in problems:
            echo(f"  - {problem}")
        return 1

    failed, pending, passed, warned = [], [], [], []
    # One cache for the whole plan run, when the project asked for one: every
    # unit of a plan usually declares the same suite, and a CI run that spawns
    # it once per unit spends most of its wall clock proving the same thing.
    # Per-unit checks are unaffected — see `_SharedCmd`.
    share = sharing(layout, config)
    for level in sorted(grouped):
        for unit in grouped[level]:
            # A unit with no usable checks never gets here: plan_mod.check()
            # rejects it as a validation problem above.
            # Same scope the done-gate would give this unit: from where it was
            # dispatched, and forgiving of the siblings running beside it. A CI
            # run that judged a wave in flight by each unit's `owns` alone would
            # report N−1 scope violations for work that is entirely in scope.
            wave, _siblings = review_mod.wave_scope(layout, slug, unit)
            results, verdict = run(
                layout, config, unit.checks, cwd=layout.root.parent,
                key=f"ci-{unit.name}", owns=unit.owns, recorded=unit.recorded,
                judged=False,
                since=contract_mod.sealed_commit(layout, slug, unit.name),
                wave=wave, share=share,
            )
            flag = {
                PASS: "ok  ", FAIL: "FAIL",
                PENDING: "wait", ERROR: "warn",
            }[verdict]
            echo(f"  {flag} wave {level} {unit.name} ({unit.status})")
            if verdict == FAIL:
                failed.append(unit.name)
                echo("       " + summarise(results).replace("\n", "\n       "))
            elif verdict == PENDING:
                pending.append(unit.name)
            elif verdict == ERROR:
                # Not a pass: something in this unit's gate could not run at all.
                warned.append(unit.name)
            else:
                passed.append(unit.name)

    echo("")
    echo(
        f"{len(passed)} passed · {len(pending)} awaiting sign-off · "
        f"{len(warned)} could not run · {len(failed)} failed"
    )
    journal.append(
        layout, config, "verify", slug,
        f"plan run: {len(passed)}p/{len(pending)}w/{len(warned)}e/{len(failed)}f",
    )
    return 1 if failed else 0


def _missing_commit_advice(results, unit):
    """The long half of "the dispatch point is gone".

    A `Result` message is shown to one terminal width, which is the right size
    for a scope violation and too small for this: the SHA alone is 40 characters
    of it. So the check reports the SHA and the flag, and the explanation of
    *why* a gate can neither pass nor error here lives out here, where there is
    room for it.
    """
    if not any(result.kind == "diff" and result.status == FAIL
               and str(result.message).startswith(MISSING_COMMIT)
               for result in results):
        return []
    return [
        "",
        "The commit recorded when this unit was dispatched was amended, rebased or",
        "reset away while it ran. The gate cannot establish what the unit changed "
        "from a",
        "dispatch point that is no longer in the history, and it will not treat "
        "\"cannot",
        "tell\" as \"nothing changed\".",
        f"  ctx start --reseal {unit.name}   re-records the dispatch point over "
        "the current HEAD",
        "and re-seals the contract as it now stands — a deliberate act, journalled "
        "as one.",
    ]


def _untracked_within(cwd, owns):
    """Untracked repo-relative paths inside `owns`, ledger churn excluded.

    Advisory only, and this is why: the done-gate runs *before* the unit's
    work is committed, so any check that enumerates through `git ls-files` is
    blind to files the unit has just written — they pass such a check
    vacuously and go red on the next commit. The gate cannot see inside a
    `cmd` check to know it is scoped that way, but it can see that new files
    exist, and say so.

    `.ctx/` paths are excluded by reusing `is_ledger` rather than writing a
    second rule for the same thing — the ledger churns on every command and
    is owned by no unit.
    """
    scope = [str(p) for p in (owns or [])]
    if not scope:
        return []
    code, stdout, error = _git(
        ["status", "--porcelain", "--untracked-files=all"], cwd
    )
    if error or code != 0:
        return []
    found = []
    for line in stdout.splitlines():
        if not line.startswith("??"):
            continue
        entry = line[3:].strip().strip('"')
        if not entry or is_ledger(entry):
            continue
        if _within(entry, scope):
            found.append(entry)
    return found


def gate_check(layout, config, slug, unit):
    """(ok, reason, lines) — may this unit be marked `done`, and if not, why.

    Ordered by what invalidates what, and by cost. A forged contract makes every
    result below it meaningless, so it is answered first; it and the empty-checks
    case are both pure file reads, and neither spawns a process.

    Split out of `gate_before_done` so that `--force` can say *what* it is
    overriding. An escape hatch that records "forced" and nothing else leaves no
    trace of the thing it stepped over, which is exactly the trace that matters
    later.
    """
    from . import contract as contract_mod, review as review_mod

    lines = []

    # 0. Was this unit ever dispatched through ctx at all?
    #
    # The seal exists only if `ctx start` wrote it. A runner sent out by a
    # direct Task call has no seal, no `before` snapshot and no recorded
    # dispatch commit — so the contract check below has nothing to compare, the
    # `diff` check cannot see anything committed, and the review has no
    # baseline. Three of this gate's four guarantees are absent at once, and
    # nothing used to say so.
    #
    # Scoped to plans that seal at all. A plan where *no* unit has a seal
    # predates the seal or was never run through `ctx start` in the first
    # place; refusing there brings back the upgrade brick that `baseline`
    # returning None exists to avoid. A plan where the siblings are sealed and
    # this unit is not was dispatched around the ledger, and that is the case
    # worth refusing.
    if (contract_mod.load_seal(layout, slug, unit.name) is None
            and contract_mod.any_seal(layout, slug)):
        return False, "no dispatch seal", lines + [
            f"refusing to mark {unit.name} done — it has no dispatch seal, so it "
            "was never dispatched by ctx:",
            "  nothing recorded its contract, its review baseline or the commit "
            "it started from,",
            "  which is most of what this gate compares against.",
            "",
            "`ctx start` is what records a seal. Dispatch the wave through it "
            "(other units in",
            "this plan have one, so this unit was sent out around it), or pass "
            "--force to accept",
            "a unit nothing can be checked against.",
        ]

    # 1. Did the unit rewrite its own contract after it was dispatched?
    if contract_mod.baseline(layout, slug, unit) is None:
        # Not a violation. A plan dispatched by an older ctx, or one whose
        # snapshot failed, has nothing to compare against; refusing there would
        # brick every in-flight plan on upgrade.
        lines.append(
            f"note: no dispatch baseline for {unit.name}, so its contract was not "
            "checked for edits — /ctx:start records one from now on"
        )
    else:
        intact, changed = contract_mod.compare(layout, slug, unit)
        if not intact:
            return False, "contract edited after dispatch", lines + [
                f"refusing to mark {unit.name} done — its contract changed after "
                "it was dispatched:",
                *[f"  changed: {item}" for item in changed],
                "",
                "A unit does not get to rewrite the promise it is judged against.",
                "Restore what changed from the plan, or — if the change is a real",
                "planning decision — say so out loud and pass --force.",
            ]

    # 2. A gate with nothing in it holds nothing.
    if not ordered(unit.checks):
        return False, "no usable verify checks", lines + [
            f"refusing to mark {unit.name} done — it has no usable verify checks",
            "add a `verify` block to the unit file, or pass --force to override",
        ]

    # 3. The checks themselves.
    #
    # `since` is where the unit started, so the `diff` check sees what it
    # *committed* as well as what is still in the working tree; `wave` is what
    # its concurrent siblings own, so their in-flight writes are not counted as
    # this unit's scope violation. Both are resolved by the caller of `run`
    # rather than by `run` itself — `plan` and `review` import this module, so
    # only a function like this one, importing them late, may ask them anything.
    since = contract_mod.sealed_commit(layout, slug, unit.name)
    if since is None and contract_mod.load_seal(layout, slug, unit.name):
        lines.append(
            f"note: no dispatch commit recorded for {unit.name}, so anything it "
            "committed was not checked for scope — /ctx:start records one from now on"
        )
    wave, _siblings = review_mod.wave_scope(layout, slug, unit)
    # Step 3 is the only part of this gate a sibling can have done already, and
    # only the `cmd` checks inside it, and only against this exact tree. Steps 0
    # to 2 above, and the refusals below, are this unit's alone and ran here.
    results, verdict = run(
        layout, config, unit.checks, cwd=layout.root.parent,
        key=f"{slug}/{unit.name}", owns=unit.owns, recorded=unit.recorded,
        judged=False, since=since, wave=wave,
        share=sharing(layout, config),
    )
    if verdict in (FAIL, PENDING):
        return False, verdict, lines + [
            f"refusing to mark {unit.name} done — the gate did not pass",
            *[result.line() for result in results],
            *_missing_commit_advice(results, unit),
            "",
            "Fix the failing criterion, or pass --force if you are deliberately",
            "overriding the gate — which is a decision worth saying out loud.",
        ]
    if verdict == ERROR and not any(r.status == PASS for r in results):
        # Nothing ran. That is a configuration problem, not a work failure — and
        # it is still not `done`: with `PROFILES["code"]` carrying only `cmd`
        # checks, a freshly cloned repo that has never run `ctx trust` errors
        # *every* check, and this branch used to print a warning and mark the
        # unit done with zero checks executed. Name the configuration problem
        # (the results below say `ctx trust`, or the missing binary, by name)
        # and then refuse anyway.
        return False, "no check could run", lines + [
            f"refusing to mark {unit.name} done — not one check could run, so "
            "nothing about this unit was verified:",
            *[result.line() for result in results],
            "",
            "That is a configuration problem, not a work failure — often a "
            "command this machine has never accepted (`ctx trust`), or a missing "
            "tool. But an ungated unit is not a done unit: ungated is not done.",
            "Fix the configuration and re-run, or pass --force to override.",
        ]
    if verdict == ERROR:
        # Some check did pass, so the gate is not blind — this is today's
        # behaviour, kept deliberately. The refusal above is for *no check
        # reached PASS*, never for *any check errored*.
        lines += [
            f"warning: not every check could run for {unit.name} — configuration, "
            "not a work failure, so this is not blocking:",
            *[result.line() for result in results],
        ]
    untracked = _untracked_within(layout.root.parent, unit.owns)
    if untracked:
        lines.append(
            f"note: {len(untracked)} untracked file(s) in {unit.name}'s owns — "
            "a check that enumerates `git ls-files` will not see them until "
            "they are committed"
        )
    return True, "", lines


def gate_before_done(layout, config, slug, unit):
    """Non-zero exit code when this unit has not earned `done`, else None.

    Only the worktree `merge` path used to verify before completing a unit. For
    `subagent` — the default tier, and the one the dispatch brief pushes hardest
    — `done` was whatever the orchestrator typed after reading a report the unit
    had written about itself. The strongest guarantee in the system did not cover
    its most common path.
    """
    from . import (contract as contract_mod, findings as findings_mod,
                   journal)

    ok, reason, lines = gate_check(layout, config, slug, unit)
    for line in lines:
        echo(line)
    if ok:
        # A gate that ran and passed re-seals what it saw, so a finding recorded
        # by hand between dispatch and now cannot be deleted afterwards.
        contract_mod.seal_findings(
            layout, slug, unit.name,
            findings_mod.load(layout, slug, unit.name),
        )
        return None
    journal.append(layout, config, "unit", unit.name, f"done refused ({reason})")
    return 1

