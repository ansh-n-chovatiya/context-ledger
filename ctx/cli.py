"""`python -m ctx …` — everything the slash commands and CI call.

Anything that can be decided without inference lives here rather than in a
prompt: status boards, collision checks, digests, scope checks and budget
measurement all cost zero tokens when they run as code.
"""

import argparse
import copy
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

from . import (
    __version__, atomic, briefing, bundle, complexity as complexity_mod,
    config as config_mod, contract as contract_mod, dispatch, frontmatter,
    journal, lock, migrate as migrate_mod, paths, phases as phases_mod,
    plan as plan_mod, spec as spec_mod,
    findings as findings_mod, review as review_mod, snapshot as snapshot_mod,
    state, telemetry, trust as trust_mod, verify, work, worktree,
)

# `journal/DIGEST.md` is a *derived* file — `journal.write_digest` regenerates it
# from the day files on every hook fire — and it is rewritten by every author on
# every session. Tracked, it is a guaranteed conflict on every merge of two
# branches that both worked; ignored, it costs nothing, because anything it
# contains can be rebuilt with `ctx digest`.
GITIGNORE = "runtime/\njournal/DIGEST.md\n"

# The line above, on its own, for the advisory `ctx doctor` prints at someone
# else's project — where nothing here may edit a `.gitignore`.
DIGEST_IGNORE_LINE = "journal/DIGEST.md"

TASK_TEMPLATE = """## Objective
{objective}

## Acceptance criteria
1. <replace with a criterion that can be checked>

## Notes
<optional context a fresh session would need>
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _layout(args=None):
    start = getattr(args, "cwd", None)
    return paths.Layout(paths.require_ctx(start))


def _loaded(args=None):
    layout = _layout(args)
    return layout, config_mod.load(layout)


def _echo(*parts):
    """`print`, but a console that cannot spell a character loses the character
    rather than the command.

    Windows resolves piped stdout to the legacy code page — cp1252 on the CI
    runners — and this CLI's output is full of `→`, `·` and `—`. Encoding one
    of those raises `UnicodeEncodeError` from inside `print`, which aborts the
    whole command: `ctx status` exited 1 on Windows for no reason worse than an
    arrow marking the active unit. Degrading the glyph is the right trade; a
    status board that cannot be read at all is not.
    """
    try:
        print(*parts)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(*[str(part).encode(encoding, "replace").decode(encoding)
                for part in parts])


def _warn(*parts):
    """`_echo`, onto stderr. Every refusal and every failure goes through here.

    Splitting the streams is what makes a refusal detectable: a caller reads
    stdout for the answer and stderr for the reason it did not get one.
    """
    try:
        print(*parts, file=sys.stderr)
    except UnicodeEncodeError:
        encoding = getattr(sys.stderr, "encoding", None) or "ascii"
        print(*[str(part).encode(encoding, "replace").decode(encoding)
                for part in parts], file=sys.stderr)


def _env_flag(name):
    """A boolean environment variable, read the way a script would set one.

    `CTX_STRICT=0` means off. Anything else truthy-looking means on, because a
    caller who exported the variable at all meant to turn it on.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# Slash commands splice whatever the user typed into a shell command line, so an
# argument reaches argparse either as one quoted token or as loose words,
# depending on the command file and on what the text contains. Both shapes have
# to mean the same thing, which is what the three helpers below are for.
_SEPARATORS = ("—", "–", "--", "-", ":", "|")


def _free_text(words):
    """Trailing words as one string, minus the dash a user puts before prose."""
    parts = list(words or [])
    while parts and parts[0] in _SEPARATORS:
        parts.pop(0)
    return " ".join(parts).strip()


def _split_name(args):
    """Separate the name from the prose that follows it, if any.

    Command files quote `$ARGUMENTS` so an apostrophe cannot split the shell
    command apart, which means everything the user typed can arrive as a single
    token. Three shapes have to be told apart, and only punctuation can do it:

      `add-auth let users log in`   → name `add-auth`, objective the rest
      `export-api — expose flows`   → name `export-api`, objective the rest
      `Fix Token Refresh`           → one title, no objective

    A kebab-case first word or an explicit dash means a name was given and prose
    follows. Plain words with neither are a title, and slicing one up would
    produce a task called `fix`.
    """
    words = (args.name or "").split() + list(getattr(args, "rest", None) or [])
    if not words:
        args.name, args.rest = None, []
        return None
    for index, word in enumerate(words):
        if index and word in _SEPARATORS:
            args.name, args.rest = " ".join(words[:index]), words[index + 1:]
            return args.name
    if len(words) > 1 and "-" in words[0]:
        args.name, args.rest = words[0], words[1:]
        return args.name
    args.name, args.rest = " ".join(words), []
    return args.name


def _named(args):
    """`name` plus any trailing words, for commands whose name is one token."""
    words = ([args.name] if getattr(args, "name", None) else []) + list(
        getattr(args, "rest", None) or []
    )
    return _free_text(words)


def _active_slug(layout, explicit, key):
    """The named or currently-active spec/plan slug, or "" when there is none.

    `slugify("")` returns "context", so slugifying an empty fallback invents a
    slug and makes every `if not slug` guard below unreachable.
    """
    name = explicit or state.load(layout).get(key)
    return bundle.slugify(name) if name else ""


# Advisory conditions: the command finished, and the notice it printed *is* the
# answer. Exiting 0 here is the designed outcome, not an oversight — which is
# why each one is listed by hand rather than inferred. A caller that would
# rather hear about them asks with `--strict` / `CTX_STRICT=1`, which turns the
# whole run into exit 1. Anything that is a refusal or a failure is not on this
# list: those exit 2 through `_warn` and `SystemExit`.
ADVISORY = frozenset({
    # No argument was typed. `_needs` names what is missing and the prompt body
    # of the slash command asks for it; a non-zero exit would make Claude Code
    # abandon the command before the question is ever put to the user.
    "missing-argument",
    # Nothing is active for this command to act on. "There is no plan yet" is a
    # true answer to "what is the plan", not a failure to answer it.
    "no-active-work",
    # `ctx snapshot` reached review.max_files. The snapshot was written and the
    # review can proceed on it; it just does not cover every file, and the cap
    # that cut it short is a setting the user chose.
    "snapshot-truncated",
})

# Advisory conditions hit by the command currently running. `main` clears it at
# the start of every run, so an in-process caller (the test suite, mainly) does
# not inherit the previous run's notices.
_ADVISED = []


def _advise(key, code=0):
    """Record an advisory condition and return the exit code to give back."""
    if key not in ADVISORY:
        raise KeyError(f"{key!r} is not an enumerated advisory condition")
    _ADVISED.append(key)
    return code


def _strict(args):
    """Is this run strict? The flag wins where it is given, the environment
    otherwise — `--strict` can only turn strictness on, never off."""
    return bool(getattr(args, "strict", False)) or _env_flag("CTX_STRICT")


def _needs(what, *hints):
    """No name was given. Say what is missing and let the prompt body ask.

    Exiting 0 is deliberate: a non-zero exit makes Claude Code abort the whole
    slash command, so the user sees an argparse dump instead of a question.
    That is a person's need, not a script's, so it is advisory rather than an
    error and `--strict` turns it into exit 1 for the caller that is a script.
    """
    _echo(f"no {what} given.")
    for hint in hints:
        _echo(hint)
    return _advise("missing-argument")


# (profile, marker, weight). Weight is how much evidence the marker really is.
# A build manifest at the root says what the project *is*; a directory named
# `docs` or `notebooks` says only that the project has some, which most projects
# of every kind do.
_PROFILE_MARKERS = (
    ("code", "package.json", 10), ("code", "pyproject.toml", 10),
    ("code", "go.mod", 10), ("code", "Cargo.toml", 10),
    ("code", "pom.xml", 10), ("code", "build.gradle", 10),
    ("code", "build.gradle.kts", 10), ("code", "Gemfile", 10),
    ("code", "composer.json", 10), ("code", "mix.exs", 10),
    ("code", "Package.swift", 10), ("code", "*.sln", 10),
    ("code", "*.csproj", 10), ("code", "setup.py", 8),
    ("code", "Makefile", 4),
    ("infra", "main.tf", 10), ("infra", "Chart.yaml", 10),
    ("infra", "terraform", 3),
    ("data", "dbt_project.yml", 10), ("data", "notebooks", 2),
    ("docs", "mkdocs.yml", 10), ("docs", "docusaurus.config.js", 10),
    ("docs", "docs", 2),
)

# Ties break toward the profile that has real commands to propose.
_PROFILE_ORDER = ("code", "infra", "data", "docs")


def _detect_profile(root):
    """Score every marker rather than returning on the first one that matches.

    First-match tested `docs` before `code`, and its markers included a bare
    `docs` directory — so a Python project that documented itself came out as a
    documentation project, which has no command candidates at all and fell back
    to a judged check. Most repositories have a `docs/`, so most repositories
    were mis-profiled into an ungated ledger.
    """
    scores = {}
    for profile, marker, weight in _PROFILE_MARKERS:
        if next(root.glob(marker), None) is not None:
            scores[profile] = scores.get(profile, 0) + weight
    if not scores:
        return "code"
    best = max(scores.values())
    for profile in _PROFILE_ORDER:
        if scores.get(profile) == best:
            return profile
    return "code"


def _python_exe():
    """An interpreter name that will still resolve when the gate runs.

    `python` is absent from Homebrew and python.org installs, so proposing
    `python -m pytest` had `_runnable` reject it and `init` wrote `verify: []` —
    an ungated ledger, from the feature whose whole job is to configure the gate.
    A bare name rather than `sys.executable` because ctx.yaml is committed and
    shared; an absolute path from one machine is wrong on every other.
    """
    for candidate in ("python3", "python"):
        if shutil.which(candidate):
            return candidate
    return sys.executable or "python3"


# (marker, commands). Data rather than an if-ladder so adding an ecosystem is a
# line here — and so `ctx.yaml` can extend it without a code change. Ordered by
# how commonly the marker is the project's real entry point.
_ECOSYSTEMS = (
    ("go.mod", ("go build ./...", "go test ./...")),
    ("Cargo.toml", ("cargo check",)),
    ("pom.xml", ("mvn -q -B test-compile",)),
    ("build.gradle", ("./gradlew --console=plain compileJava",)),
    ("build.gradle.kts", ("./gradlew --console=plain compileKotlin",)),
    ("Gemfile", ("bundle exec rake test",)),
    ("composer.json", ("composer run-script test",)),
    ("mix.exs", ("mix compile --warnings-as-errors",)),
    ("Package.swift", ("swift build",)),
    ("*.sln", ("dotnet build --nologo",)),
    ("*.csproj", ("dotnet build --nologo",)),
    ("dbt_project.yml", ("dbt compile",)),
    ("main.tf", ("terraform validate",)),
    ("Chart.yaml", ("helm lint .",)),
)

# Node is special-cased because what to run is inside package.json, not implied
# by its presence — and the workspace tools each front the same scripts.
_NODE_RUNNERS = (
    ("pnpm-workspace.yaml", "pnpm"), ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
)


def _node_candidates(root):
    manifest = root / "package.json"
    if not manifest.is_file():
        return []
    text = manifest.read_text(encoding="utf-8", errors="replace")
    runner = "npm"
    for marker, name in _NODE_RUNNERS:
        if (root / marker).exists():
            runner = name
            break
    run = f"{runner} run" if runner == "npm" else runner
    out = []
    if '"typecheck"' in text:
        out.append(f"{run} typecheck")
    elif '"tsc"' in text or (root / "tsconfig.json").exists():
        out.append("npx tsc --noEmit")
    if '"test"' in text:
        out.append(f"{runner} test")
    if '"lint"' in text:
        out.append(f"{run} lint")
    return out


def _verify_candidates(root, profile, extra=()):
    """Commands we would propose. Availability is checked; passing is not.

    `extra` comes from `verify_candidates` in ctx.yaml, so a house toolchain no
    table could anticipate — bazel, a wrapper script, a Makefile target — is a
    config line rather than a fork.
    """
    out = []
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        out.append(f"{_python_exe()} -m pytest -q")
    out.extend(_node_candidates(root))
    for marker, commands in _ECOSYSTEMS:
        if next(root.glob(marker), None) is not None:
            out.extend(commands)
    if (root / "Makefile").exists():
        text = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
        for target in ("test", "check", "build"):
            if re.search(rf"(?m)^{target}\s*:", text):
                out.append(f"make {target}")
                break
    for entry in extra or ():
        if str(entry).strip():
            out.append(str(entry).strip())

    seen, unique = set(), []
    for command in out:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique


# A module name, not a path and not an expression. Anything else is refused
# rather than resolved: the string reaches here from a committed ctx.yaml.
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Asked as a question about the *machine*, which means it must read nothing
# from the repository. The name under test arrives in a cloned ctx.yaml, and
# this probe was once the shortest path to arbitrary code execution in the
# whole tool, so the defences are layered rather than singular:
#
#   * the name is passed as `argv[1]`, never interpolated into source;
#   * only its top-level component is resolved by the caller — `find_spec`
#     on a dotted name *imports the parent package* to read its `__path__`,
#     so `find_spec("evilpkg.sub")` executed `evilpkg/__init__.py`;
#   * `sys.path[0]` is dropped, because `python -c` prepends the working
#     directory there, and any other entry naming that directory goes too;
#   * and the caller runs it in an empty scratch directory, so `sys.path[0]`
#     was never the repository to begin with.
#
# `find_spec` locates a module without executing it, which is the entire point
# of using it over an import — but only once the search path is trustworthy.
_PROBE = (
    "import importlib.util, os, sys; "
    "here = os.getcwd(); "
    "sys.path = [p for p in sys.path[1:] if p and os.path.abspath(p) != here]; "
    "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
)


def _availability(command):
    """(can_it_start, why_not). The reason is user-facing, so it must be true.

    PATH alone is not enough for the interpreter forms: `python3 -m pytest` with
    pytest absent exits 1 from an interpreter that is very much present, so a
    PATH check accepts a command the gate can never actually run — and reports
    the wrong reason when it does reject one.

    Availability is decided *before* the trust store is consulted — `ctx doctor`
    prints MISS for a command it will never run — so everything this function
    does happens to strings the user has not reviewed yet. It therefore reads
    the module name and runs nothing that the repository supplied.

    A dotted name is answered for its top-level package only. `python3 -m
    json.tool` on a machine with `json` is reported available even though
    nothing checked `tool`; the alternative is importing `json` to ask, and the
    cost of that trade is a command that fails at the gate instead of at the
    probe, one layer later and with the trust store already passed.
    """
    parts = command.split()
    if not parts:
        return False, "empty command"
    if shutil.which(parts[0]) is None:
        return False, f"{parts[0]} is not on PATH"
    if os.path.basename(parts[0]).startswith("python") and "-m" in parts[:3]:
        module = parts[parts.index("-m") + 1:parts.index("-m") + 2]
        if module:
            top = module[0].split(".", 1)[0]
            if not _MODULE_NAME.match(top):
                return False, f"{parts[0]} cannot import {module[0]}"
            try:
                with tempfile.TemporaryDirectory() as elsewhere:
                    ok = subprocess.run(
                        [parts[0], "-c", _PROBE, top], capture_output=True,
                        timeout=20, cwd=elsewhere,
                    ).returncode == 0
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                return False, f"could not probe {module[0]}: {exc}"
            if not ok:
                return False, f"{parts[0]} cannot import {module[0]}"
    return True, ""


def _runnable(command):
    return _availability(command)[0]


def _run(command, cwd, timeout):
    try:
        completed = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout,
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_init(args):
    root = paths.project_root(args.cwd)
    layout = paths.Layout(root / paths.CTX_DIRNAME)
    fresh = not layout.root.exists()
    for directory in layout.dirs():
        directory.mkdir(parents=True, exist_ok=True)

    profile = args.profile or _detect_profile(root)
    # Re-running init keeps any house commands already declared in ctx.yaml.
    existing = config_mod.load(layout) if layout.config.is_file() else {}
    candidates = _verify_candidates(
        root, profile, existing.get("verify_candidates") or []
    )
    # Candidates the ecosystem table produced are ours; candidates the ledger's
    # own `verify_candidates` supplied are the cloned file's. `--verify-now`
    # *executes* a candidate to see whether it passes on a clean tree, so running
    # the second kind hands an attacker-supplied string to the shell before it
    # has even been printed. They are proposed, never probed, and never
    # auto-accepted — `ctx trust` is the only way in.
    from_ledger = {str(entry).strip() for entry in
                   (existing.get("verify_candidates") or []) if str(entry).strip()}
    accepted, rejected, needs_review = [], [], []
    for command in candidates:
        available, why = _availability(command)
        if not available:
            rejected.append((command, why))
            continue
        if command in from_ledger:
            needs_review.append(command)
            continue
        if args.verify_now:
            code, output = _run(command, root, args.timeout)
            if code != 0:
                rejected.append((command, f"exit {code} on a clean tree"))
                continue
        accepted.append({"kind": "cmd", "run": command})

    if not layout.config.exists() or args.force:
        # Start from every key `config.DEFAULTS` knows about, not a hand-picked
        # subset. The subset used to live here as a literal dict, which meant a
        # new top-level default (like `complexity`) had to be remembered and
        # re-typed at this exact call site or it silently never reached a
        # generated ctx.yaml — a class of bug, not an instance, since nothing
        # forced the two lists to stay in sync. `copy.deepcopy` rather than
        # `dict(...)`: several of these defaults (`complexity`, `models`) nest
        # a further dict, and a shallow copy would hand this project's
        # ctx.yaml a live reference to that inner mapping — editing the copy
        # in place would then mutate the process-wide `DEFAULTS` for every
        # other project this process touches afterwards.
        settings = copy.deepcopy(config_mod.DEFAULTS)
        # Everything below is a genuine override, not a default: it is what
        # *this run* detected or carried over, and it must win even though the
        # line above already populated the same key from DEFAULTS.
        settings["schema"] = config_mod.SCHEMA
        settings["profile"] = profile
        settings["level"] = "0"
        settings["auto_load"] = []
        settings["redact"] = []
        settings["verify_candidates"] = list(existing.get("verify_candidates") or [])
        settings["verify"] = accepted or config_mod.PROFILES.get(profile, [])
        atomic.write_text(layout.config, config_mod.render(settings))

    atomic.write_text(layout.root / ".gitignore", GITIGNORE)
    config = config_mod.load(layout)
    # Accept only what *this run* detected and is about to print, never
    # `config["verify"]`. On a pre-existing ledger those are different lists: the
    # config's block came with the repository, and accepting it here silently
    # granted a cloned file the shell access `ctx trust` exists to gate — while
    # the report below listed something else entirely.
    trust_mod.accept(layout, accepted)
    journal.write_digest(layout, config)
    bundle.reindex(layout)
    if not layout.state.exists():
        state.save(layout, dict(state.EMPTY))

    _echo(f"{'initialised' if fresh else 'updated'} {layout.rel(layout.root)}  profile={profile}  level=L0")
    if accepted:
        for entry in accepted:
            suffix = "" if args.verify_now else "  (available; not yet run)"
            _echo(f"  verify  {entry['run']}{suffix}")
    if needs_review:
        for command in needs_review:
            _echo(f"  review  {command}")
        _echo("  those came from `verify_candidates` in this ledger, not from your")
        _echo("  toolchain — read them and run `ctx trust` to allow them")
    if rejected:
        for command, why in rejected:
            _echo(f"  skipped {command} — {why}")
    if not accepted:
        fallback = config.get("verify") or []
        judged = [str(e.get("kind")) for e in fallback if isinstance(e, dict)]
        if judged:
            _echo(f"  no runnable command detected; falling back to {', '.join(judged)}")
            _echo("  those need a model or a person — add a `cmd` check to ctx.yaml for")
            _echo("  a gate that decides objectively")
        else:
            _echo("  no verify commands configured — add them to ctx.yaml before L1/L2")
    measured = briefing.measure(layout, config, state.load(layout))
    _echo(f"L0 is active: work is journalled to disk, and the hook briefing costs "
          f"~{measured['approx_tokens']} tokens per session "
          f"(cap ~{round(measured['cap'] / 3.6)}).")
    _echo("`ctx budget` reports that as it changes. The plugin's own always-on")
    _echo("footprint is separate and larger: `claude plugin details ctx`.")
    return 0


def cmd_status(args):
    layout, config = _loaded(args)
    current = state.load(layout)
    level = config_mod.normalise_level(current.get("level"))
    measured = briefing.measure(layout, config, current)

    _echo(f"level    L{level} ({config_mod.LEVEL_NAMES[level]})   profile {config.get('profile')}")
    _echo(f"task     {current.get('task') or '—'}")
    _echo(f"plan     {current.get('plan') or '—'}   unit {current.get('unit') or '—'}")
    _echo(f"briefing {measured['chars']}/{measured['cap']} chars (~{measured['approx_tokens']} tokens)")

    attempts = {k: v for k, v in (current.get("attempts") or {}).items() if v}
    if attempts:
        _echo("attempts " + ", ".join(f"{k}×{v}" for k, v in sorted(attempts.items())))

    if current.get("plan"):
        rows, problems = plan_mod.board(layout, current["plan"])
        _echo("")
        _echo(f"wave board — plan {current['plan']}:")
        wave = None
        unsealed = _unsealed_units(
            layout, current["plan"], plan_mod.load_units(layout, current["plan"])
        )
        for level, name, tier, status, owns in rows:
            if level != wave:
                wave, marker = level, ""
                _echo(f"  wave {level}")
            flag = "→" if name == current.get("unit") else " "
            _echo(f"   {flag} {name:<24} {tier:<9} "
                  f"{_status_cell(status, name in unsealed)}")
        if not rows:
            _echo("   (no units yet)")
        if unsealed:
            _echo(f"   ! unsealed: {', '.join(sorted(unsealed))} — dispatched "
                  "around `ctx start`, so nothing recorded the contract, the "
                  "review baseline or")
            _echo("     the commit they started from. `ctx start` seals; the "
                  "done-gate refuses a unit it cannot check.")
        for problem in problems:
            _echo(f"   ! {problem}")
        if not problems:
            nxt = plan_mod.next_wave(layout, current["plan"])
            dispatched, waiting = _wave_in_flight(layout, current["plan"], nxt)
            if nxt and dispatched and not waiting:
                _echo(f"   next: wave {nxt} is in flight — {len(dispatched)} unit(s) "
                      "dispatched, none left to start; review what comes back")
            else:
                _echo(f"   next: {'wave %d — /ctx:start' % nxt if nxt else 'plan complete'}")

    if current.get("plan") and not (work.claim()[0] or current.get("unit")):
        stray = _orchestrator_edits(layout, current["plan"])
        if stray:
            _echo("")
            _echo("orchestrator discipline:")
            _echo(f"  {len(stray)} file(s) edited from this session during an active")
            _echo("  wave, owned by no unit. The orchestrator dispatches and reads")
            _echo("  reports; editing source here is what makes its context grow.")
            for path in stray[:5]:
                _echo(f"    {path}")

    entries, earlier = journal.tail(layout, 8)
    _echo("")
    _echo("recent journal:")
    for entry in entries or ["  (none)"]:
        _echo(f"  {entry}")
    if earlier:
        _echo(f"  … {earlier} earlier entries")
    return 0


def _orchestrator_edits(layout, slug):
    """Files this session edited during a wave that belong to no unit.

    Reads are the discipline that actually matters, and they are not observable
    without a PostToolUse hook on every `Read` — a process spawn per file read,
    which is too much to charge everyone for a diagnostic. Edits are free: they
    are already journalled, and an orchestrator editing source is the same
    mistake showing through.
    """
    owned = []
    for unit in plan_mod.load_units(layout, slug):
        owned.extend(unit.owns)
    stray = []
    for path in journal.recent_paths(layout, 20):
        if path.startswith(".ctx/"):
            continue
        if not plan_mod.covers_any(path, owned) and path not in stray:
            stray.append(path)
    return stray


def cmd_briefing(args):
    layout, config = _loaded(args)
    sys.stdout.write(briefing.build(layout, config, state.load(layout)))
    return 0


def cmd_resume(args):
    """The on-demand expansion of L0. Prints more than a briefing may inject."""
    layout, config = _loaded(args)
    current = state.load(layout)
    level = config_mod.normalise_level(current.get("level"))
    _echo(f"# Resume — L{level} ({config_mod.LEVEL_NAMES[level]})")
    _echo("")
    if current.get("task"):
        doc = frontmatter.read(layout.task_file(current["task"]))
        if doc:
            _echo(f"## Active task: {current['task']}")
            _echo(doc.body.strip())
            _echo("")
    if layout.digest.is_file():
        _echo(layout.digest.read_text(encoding="utf-8").strip())
        _echo("")
    rows = bundle.listing(layout)
    if rows:
        _echo("## Saved contexts")
        for scope, name, _path, summary in rows:
            _echo(f"- {name} ({scope})" + (f" — {summary}" if summary else ""))
    return 0


def cmd_level(args):
    layout, config = _loaded(args)
    level = config_mod.normalise_level(args.level)
    state.update(layout, level=level)
    journal.append(layout, config, "level", f"L{level}", "")
    _echo(f"level L{level} ({config_mod.LEVEL_NAMES[level]})")
    return 0


def cmd_task(args):
    """Escalate to L1: exactly one file, no spec directory, no plan."""
    layout, config = _loaded(args)
    _split_name(args)
    if not args.name:
        return _needs(
            "task name",
            "Ask what this change should be called — a short kebab-case name —",
            "then run: ctx task «name» --objective \"«one sentence»\"",
        )
    slug = bundle.slugify(args.name)
    if not args.objective:
        args.objective = _free_text(args.rest) or None
    path = layout.task_file(slug)
    if not path.exists() or args.force:
        meta = {
            "ctx_schema": config_mod.SCHEMA,
            "task": slug,
            "level": 1,
            "status": "active",
            "created": datetime.date.today().isoformat(),
            "verify": config.get("verify") or [],
        }
        body = TASK_TEMPLATE.format(objective=args.objective or "<one sentence>")
        frontmatter.Document(meta, body).write(path)
    state.update(layout, level="1", task=slug)
    state.clear_attempts(layout, slug)
    journal.append(layout, config, "task", slug, "opened")
    _echo(f"L1 tracked · task {slug}")
    _echo(f"file {layout.rel(path)}")
    # Splitting a name from an objective is punctuation-guessing on the command
    # people type most. Showing what it decided turns a silent wrong guess into
    # a visible one, which is the difference between a bug and a prompt.
    _echo(f"read as · name: {slug} · objective: {args.objective or '(none given)'}")
    _echo("If that split is wrong: ctx task «name» --objective \"…\" --force")
    _echo("Fill in Objective and Acceptance criteria, then work normally.")
    return 0


def cmd_drop(args):
    layout, config = _loaded(args)
    current = state.load(layout)
    previous = current.get("task") or current.get("unit")
    state.update(layout, level="0", task=None, plan=None, unit=None)
    state.clear_attempts(layout)
    journal.append(layout, config, "level", "L0", f"dropped {previous or 'ceremony'}")
    _echo("L0 trace · no gates, journalling only")
    return 0


def cmd_save(args):
    layout, config = _loaded(args)
    args.name = _named(args)
    if not args.name:
        return _needs(
            "bundle name",
            "Ask what to call this context, then run: ctx save «name» --stdin",
        )
    if args.stdin:
        body = sys.stdin.read()
    elif args.file:
        body = open(args.file, encoding="utf-8").read()
    else:
        body = bundle.template(args.name, project=layout.root.parent.name).render()
    path = bundle.save(
        layout, args.name, body, tags=args.tag, project=layout.root.parent.name,
        config=config,
    )
    journal.append(layout, config, "save", layout.rel(path), "context bundle")
    _echo(f"saved {layout.rel(path)}")
    if not (args.stdin or args.file):
        _echo("template written — fill in each section, it is meant to be readable alone")
    return 0


def cmd_load(args):
    layout, _config = _loaded(args)
    name = _named(args)
    if not name:
        _advise("missing-argument")
        _echo("no bundle name given. Saved contexts:")
        _list_bundles(layout)
        _echo("Ask which one to load, then run: ctx load «name»")
        return 0
    path = bundle.resolve(layout, name)
    if path is None:
        # A name that resolves to nothing is a refusal, not an answer: the
        # caller asked for a context and did not get one. It used to exit 0
        # with the list of what does exist on *stdout*, which is what a caller
        # redirecting `ctx load x > brief.md` reads as the context itself.
        raise SystemExit("\n".join(
            [f"no context named {name!r}. Saved contexts:"]
            + _bundle_lines(layout)
        ))
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def cmd_promote(args):
    layout, config = _loaded(args)
    name = _named(args)
    if not name:
        _advise("missing-argument")
        _echo("no bundle name given. Saved contexts:")
        _list_bundles(layout)
        _echo("Ask which one to promote, then run: ctx promote «name»")
        return 0
    target = bundle.promote(layout, name)
    if target is None:
        _echo(f"no context named {name!r}. Saved contexts:")
        _list_bundles(layout)
        return 0
    journal.append(layout, config, "promote", name, "to global store")
    _echo(f"promoted to {target}")
    return 0


def _wave_in_flight(layout, slug, wave):
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


def _status_cell(status, unsealed=False):
    """How a unit's status reads on a board.

    `running` is the one status a reader has never seen before and the one that
    changes what they should do next — it means dispatched and not yet back, so
    the useful thing is a review, not another `ctx start`. Spelled out rather
    than marked with a glyph: `ctx status` already lost a run to a `\\u2192` that
    cp1252 could not encode.

    `unsealed` replaces the status outright rather than annotating it. A unit
    whose wave went out without it has no dispatch seal, and its `status:` is
    then whatever it happens to say — usually `pending`, which reads as "not
    started yet" for a unit somebody may well be running right now. What the
    reader needs to know is not the status, it is that nothing was recorded to
    hold this unit to, and they need it here rather than at the gate.
    """
    if unsealed:
        return f"unsealed ({status} — no ctx start, nothing recorded to judge it)"
    return "running (in flight)" if status == "running" else status


def _unsealed_units(layout, slug, units):
    """Names of units whose wave was dispatched without them.

    A seal is written by `ctx start` and by nothing else, so no seal means no
    dispatch through ctx. On its own that is unremarkable — every unit is
    unsealed before its wave goes out. What is remarkable is a unit with no seal
    whose *wave* has them: that wave was dispatched, and this unit was sent out
    around the ledger, or not at all.
    """
    sealed = {unit.name: contract_mod.load_seal(layout, slug, unit.name) is not None
              for unit in units}
    waves = {}
    for unit in units:
        waves.setdefault(unit.wave, []).append(unit)
    flagged = set()
    for members in waves.values():
        if not any(sealed[unit.name] for unit in members):
            continue  # this wave has not been dispatched at all
        flagged |= {unit.name for unit in members
                    if not sealed[unit.name] and unit.status != "done"}
    return flagged


def _list_units(layout, slug):
    units = plan_mod.load_units(layout, slug)
    if not units:
        _echo("  none yet — ctx plan-unit «NN-name»")
        return units
    unsealed = _unsealed_units(layout, slug, units)
    for unit in units:
        _echo(f"  {unit.name:<24}{_status_cell(unit.status, unit.name in unsealed)}")
    return units


def _bundle_lines(layout):
    """The saved-context listing as lines, for a caller that has to put it
    somewhere other than stdout — a refusal carries it on stderr."""
    rows = bundle.listing(layout)
    if not rows:
        return ["  none saved yet — /ctx:save «name»"]
    width = max(len(name) for _s, name, _p, _sum in rows)
    return [f"  {scope:<8} {name:<{width}}  {summary}"
            for scope, name, _path, summary in rows]


def _list_bundles(layout):
    for line in _bundle_lines(layout):
        _echo(line)
    return bundle.listing(layout)


def cmd_list(args):
    layout, _config = _loaded(args)
    _list_bundles(layout)
    return 0


def cmd_digest(args):
    layout, config = _loaded(args)
    path = journal.write_digest(layout, config)
    _echo(f"regenerated {layout.rel(path)}")
    return 0


def cmd_journal(args):
    layout, config = _loaded(args)
    line = journal.append(layout, config, args.kind, args.target, args.note or "")
    _echo(line or "journalling disabled")
    return 0


# A hook failure is worth blocking on while it is still happening. One from
# March is history: `hook-errors.log` never rotated and doctor counted its mere
# existence as a problem, so a single transient failure left the command exiting
# 1 forever — a red build nobody could turn green without knowing to delete a
# file by hand.
RECENT_ERROR_HOURS = 24
_ERROR_HEADER = re.compile(r"^--- (\d{4}-\d{2}-\d{2}T[\d:.]+) (.+) ---$")


def _recent_hook_errors(text, hours=RECENT_ERROR_HOURS):
    """Events that failed inside the window. Entries written before stamping
    existed carry no timestamp and are never counted as live."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    found = []
    for line in text.splitlines():
        match = _ERROR_HEADER.match(line.strip())
        if not match:
            continue
        try:
            when = datetime.datetime.fromisoformat(match.group(1))
        except ValueError:
            continue
        if when >= cutoff:
            found.append(match.group(2))
    return found


def cmd_prune(args):
    """Fold old journal day-files into monthly archives."""
    layout, config = _loaded(args)
    before = None
    if args.before:
        try:
            before = datetime.date.fromisoformat(args.before)
        except ValueError:
            _echo(f"--before must be YYYY-MM-DD, got {args.before!r}")
            return 1
    folded, archives = journal.prune(layout, config, before=before,
                                     archive=not args.discard)
    if not folded:
        keep = (config.get("journal") or {}).get("keep_days", 0)
        _echo("nothing to prune"
              + ("" if before or keep else
                 " — set journal.keep_days in ctx.yaml, or pass --before"))
        return 0
    journal.write_digest(layout, config)
    verb = "discarded" if args.discard else "archived"
    _echo(f"{verb} {len(folded)} day file(s) into {len(archives)} archive(s)")
    for path in archives:
        _echo(f"  {layout.rel(path)}")
    return 0


def _verify_drift(layout, config):
    """Work files whose `verify` block no longer matches the project default.

    The block is snapshotted into each task and unit when the file is written,
    which is defensible — a unit should be judged by the contract it was given.
    The silence was not: fixing a broken command in ctx.yaml left every existing
    task still carrying the old one, with nothing saying so.
    """
    default = [trust_mod.command_id(c) for c in (config.get("verify") or [])
               if isinstance(c, dict) and c.get("kind") == "cmd"]
    drifted = []
    paths = list(layout.tasks.glob("*.md")) if layout.tasks.is_dir() else []
    paths += list(layout.plans.glob("*/units/*.md")) if layout.plans.is_dir() else []
    for path in sorted(paths):
        doc = frontmatter.read(path)
        if doc is None:
            continue
        theirs = [trust_mod.command_id(c) for c in (doc.meta.get("verify") or [])
                  if isinstance(c, dict) and c.get("kind") == "cmd"]
        if theirs != default:
            drifted.append(path)
    return drifted


def _home_relative(path):
    """`~/...` where that is shorter and less identifying than the absolute."""
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home):] if home and text.startswith(home) else text


_ADR_NUMBER = re.compile(r"^(\d{4})-")


def duplicate_adrs(layout):
    """`[(number, [names])]` for every ADR id that more than one file claims.

    ADR numbers are allocated local-max+1 (`spec.next_adr_number`), which is the
    right trade — a padded sequence is what makes `0007-…` citable in prose —
    and it is not collision-free across branches. Two branches each write
    `0003-*.md` under different titles, git sees two new files and no conflict,
    and the merge lands two ADR 0003s. Nothing downstream errors; the numbering
    just quietly stops meaning anything.

    Detection, therefore, rather than prevention: the allocator is unchanged and
    the duplicate is named the moment `doctor` or `ci` next runs.
    """
    by_number = {}
    try:
        found = sorted(layout.decisions.glob("*.md"))
    except OSError:
        return []
    for path in found:
        match = _ADR_NUMBER.match(path.name)
        if match:
            by_number.setdefault(int(match.group(1)), []).append(path.name)
    return [(number, names) for number, names in sorted(by_number.items())
            if len(names) > 1]


def _git_tracked(layout, path):
    """Whether git has `path` in its index. Reads; never writes.

    `--error-unmatch` is the only form that answers for one exact path: a bare
    `git ls-files <path>` exits 0 and prints nothing for an untracked file,
    which is indistinguishable from a failure at the exit code alone.
    """
    root = layout.root.parent
    if not (root / ".git").exists():
        return False
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(path)],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # no git, or it would not answer: not a claim either way
    return completed.returncode == 0


def _digest_advisory(layout):
    """Lines advising that a tracked `DIGEST.md` will conflict, or `[]`.

    Advisory, and deliberately not a failure. `DIGEST.md` is derived — every
    session regenerates it from the day files — so two branches that both did
    any work conflict on it every single time, and the conflict carries no
    information because `ctx digest` rebuilds either side.

    **Nothing here runs `git rm`.** Untracking a file changes what a commit in
    somebody else's repository contains, and a diagnostic command that quietly
    stages a deletion is a worse surprise than the conflict it saves. The tool
    says what to type; the human types it.
    """
    if not _git_tracked(layout, layout.digest):
        return []
    return [
        f"  warn {layout.rel(layout.digest)} is tracked by git — it is "
        "regenerated on every",
        "       session, so every merge of two working branches conflicts on "
        "it, and the",
        "       conflict says nothing `ctx digest` could not rebuild. To stop "
        "that, run:",
        f"         git rm --cached {layout.rel(layout.digest)}",
        f"         echo {DIGEST_IGNORE_LINE} >> {layout.rel(layout.root)}/.gitignore",
        "       Advisory, not a failure: nothing here will edit your repository "
        "for you.",
    ]


def _doctor_policy(layout):
    """Where the active settings came from, and anything a lock refused.

    The point of the section is to make "why is this setting what it is"
    answerable in one command instead of by opening three files in two
    directories a developer has never had reason to look in.
    """
    _echo("## policy")
    problems = 0
    try:
        policy = config_mod.resolve_policy(layout)
    except SystemExit as exc:
        _echo(f"  BAD  {exc}")
        return 1

    for source, path, present in policy.layers:
        state_word = "ok  " if present else "none"
        _echo(f"  {state_word} {source:<10} {_home_relative(path)}"
              + ("" if present else "  (absent)"))

    above = sorted(key for key, src in policy.origin.items() if src != "repo")
    for key in above[:12]:
        holder = policy.locked_by(key)
        _echo(f"       {key} = {policy.value_of(key)!r} "
              f"(from {policy.origin[key]}{', locked' if holder else ''})")
    if len(above) > 12:
        _echo(f"       … and {len(above) - 12} more setting(s) from policy")
    for key in sorted(policy.locks):
        if key not in policy.origin:
            _echo(f"       {key} locked by {policy.locks[key]} (no value set)")
    if not above and not policy.locks:
        _echo("       no policy above the repository — ctx.yaml decides everything")

    for source, key, attempted, held, holder in policy.refusals:
        _echo(f"  BAD  {source} sets {key}={attempted!r}; {holder} policy locks it "
              f"to {held!r} — the locked value holds")
        problems += 1
    for note in policy.notes:
        _echo(f"  warn {note}")
    return problems


def cmd_doctor(args):
    layout, config = _loaded(args)
    problems = 0

    if args.clear:
        if layout.errors.is_file():
            layout.errors.unlink()
            _echo(f"cleared {layout.rel(layout.errors)}")
        else:
            _echo("no hook errors to clear")
        _echo("")

    _echo("## layout")
    for directory in layout.dirs():
        ok = directory.is_dir()
        problems += 0 if ok else 1
        _echo(f"  {'ok  ' if ok else 'MISS'} {layout.rel(directory)}")

    _echo("## briefing budget")
    current = state.load(layout)
    for level in config_mod.LEVELS:
        probe = dict(current, level=level)
        measured = briefing.measure(layout, config, probe)
        problems += 1 if measured["truncated"] else 0
        _echo(
            f"  {'CUT ' if measured['truncated'] else 'ok  '} L{level} "
            f"{measured['chars']}/{measured['cap']} chars "
            f"(~{measured['approx_tokens']} tokens)"
            + (" — content dropped to fit" if measured["truncated"] else "")
        )

    _echo("## verify commands")
    doctor_accepted = trust_mod.load(layout)
    entries = config.get("verify") or []
    if not entries:
        _echo("  none configured (fine at L0; required for L1/L2 gates)")
    for entry in entries:
        if not isinstance(entry, dict):
            _echo(f"  BAD  {entry!r} is not a mapping")
            problems += 1
            continue
        kind = entry.get("kind")
        if kind != "cmd":
            _echo(f"  ok   {kind} (no command to probe)")
            continue
        command = str(entry.get("run") or "")
        available, why = _availability(command)
        if not available:
            _echo(f"  MISS {command} — {why}")
            problems += 1
        elif args.verify and not trust_mod.is_accepted(entry, doctor_accepted):
            # `--verify` used to shell out here with no trust check, so `doctor`
            # executed a command and then, four lines later, reported that the
            # same command "will not run until you review it". Running is the
            # thing trust gates; reporting is not a substitute for asking.
            _echo(f"  SKIP {command} — not accepted on this machine; run `ctx trust`")
            problems += 1
        elif args.verify:
            code, output = _run(command, layout.root.parent, args.timeout)
            tail = output.strip().splitlines()[-1:] or [""]
            _echo(f"  {'ok  ' if code == 0 else 'FAIL'} {command} (exit {code}) {tail[0][:80]}")
            problems += 0 if code == 0 else 1
        else:
            _echo(f"  ok   {command} (available; pass --verify to run it)")

    _echo("## command trust")
    declared = trust_mod.declared(layout, config)
    accepted = trust_mod.load(layout)
    pending = [c for c, _s in declared if not trust_mod.is_accepted(c, accepted)]
    if not declared:
        _echo("  no shell commands declared — nothing to accept")
    elif pending:
        _echo(f"  MISS {len(pending)} of {len(declared)} command(s) not accepted "
              "on this machine")
        for check in pending[:4]:
            _echo(f"       {check.get('run')}")
        _echo("       these will not run until you review them: ctx trust")
        problems += 1
    else:
        _echo(f"  ok   {len(declared)} command(s) accepted on this machine")
    legacy = trust_mod.legacy_path_for(layout)
    if legacy.is_file():
        # Explain the silence rather than leaving it mysterious: acceptances
        # recorded before 0.7 lived in the repo and are deliberately ignored.
        _echo(f"  note {layout.rel(legacy)} is a pre-0.7 in-repo trust store and is")
        _echo("       ignored — acceptances now live outside the repository. Delete it.")

    drifted = _verify_drift(layout, config)
    if drifted:
        _echo("## verify drift")
        _echo(f"  warn {len(drifted)} work file(s) carry a `verify` block that no "
              "longer matches ctx.yaml")
        for path in drifted[:4]:
            _echo(f"       {layout.rel(path)}")
        _echo("       that is expected for finished work; re-scaffold if it is not")

    _echo("## decisions")
    duplicates = duplicate_adrs(layout)
    if duplicates:
        for number, names in duplicates:
            _echo(f"  BAD  {len(names)} files claim ADR {number:04d}: "
                  + ", ".join(names))
            problems += 1
        _echo("       Two branches each allocated the next free number and "
              "merged without a")
        _echo("       conflict. Renumber all but one — the id is what every "
              "reference cites.")
    else:
        _echo("  ok   every ADR id is claimed by exactly one file")

    advisory = _digest_advisory(layout)
    if advisory:
        _echo("## journal digest")
        for line in advisory:
            _echo(line)

    _echo("## plugin footprint")
    _echo("  the briefing above is the hook cost only; the plugin's own always-on")
    _echo("  context is separate — measure it with: claude plugin details ctx")

    problems += _doctor_policy(layout)

    _echo("## gate")
    override = config_mod.gate_override(layout, config, "doctor")
    _echo(f"  enabled={bool((config.get('gate') or {}).get('enabled')) and not override.disabled}"
          f"  max_attempts={(config.get('gate') or {}).get('max_attempts')}"
          + (f"  (CTX_GATE={override.value} in this environment)" if override.disabled else "")
          + (f"  (CTX_GATE={override.value} refused by {override.lock} policy — "
             "the gate ran)" if override.refused else ""))
    if override.requested and not override.recorded:
        # The bypass happened; the journal did not take the line. Reported here
        # as well as on stderr because stderr scrolls away and this does not.
        _echo("  BAD  that override could not be journalled — see "
              f"{layout.rel(layout.errors)}")
        problems += 1

    if layout.errors.is_file():
        text = layout.errors.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        recent = _recent_hook_errors(text)
        _echo(f"## hook errors ({len(lines)} lines in {layout.rel(layout.errors)})")
        for line in lines[-6:]:
            _echo(f"  {line}")
        if recent:
            _echo(f"  FAIL {len(recent)} failure(s) in the last {RECENT_ERROR_HOURS}h: "
                  + ", ".join(sorted(set(recent))))
            problems += 1
        else:
            _echo(f"  ok   none in the last {RECENT_ERROR_HOURS}h — stale log; "
                  "clear it with `ctx doctor --clear`")

    _echo("")
    _echo(f"{problems} problem(s)" if problems else "all checks passed")
    return 1 if problems else 0


# --------------------------------------------------------------------------- #
# phase 3 — the ambiguity gate
# --------------------------------------------------------------------------- #

def cmd_spec(args):
    """Escalate to L2 and scaffold the spec. Gate 1 lives in its questions file."""
    layout, config = _loaded(args)
    _split_name(args)
    if not args.name:
        return _needs(
            "spec name",
            "Ask what to call this piece of work, then run:",
            "ctx spec «short-name» «what you want»",
        )
    # Capped here as well as inside `spec_dir`, so the slug written to
    # `state.json` and echoed below is the one the directory is actually named.
    # Every lookup re-caps, so an uncapped slug resolved correctly — it just
    # printed a name no `ls` would ever show.
    slug = spec_mod.normalise_slug(bundle.slugify(args.name))
    intent = args.intent or _free_text(args.rest)
    path, qpath = spec_mod.create(layout, slug, intent, config.get("verify"))
    state.update(layout, level="2", spec=slug)
    journal.append(layout, config, "spec", slug, "opened")
    _echo(f"L2 planned · spec {slug}")
    _echo(f"spec      {layout.rel(path)}")
    _echo(f"questions {layout.rel(qpath)}")
    return 0


def cmd_question(args):
    layout, config = _loaded(args)
    slug = bundle.slugify(args.name)
    added = spec_mod.add_questions(layout, slug, args.text, blocking=not args.non_blocking)
    kind = "non-blocking" if args.non_blocking else "blocking"
    journal.append(layout, config, "spec", slug, f"+{added} {kind} question(s)")
    _echo(f"added {added} {kind} question(s) to {layout.rel(spec_mod.questions_path(layout, slug))}")
    return 0


def cmd_ask(args):
    """List what still has to be answered before anything gets built."""
    layout, _config = _loaded(args)
    slug = _active_slug(layout, args.name, "spec")
    if not slug:
        _echo("no active spec — /ctx:spec «intent» first")
        return _advise("no-active-work")
    blocking, non_blocking, resolved = spec_mod.questions(layout, slug)
    if blocking:
        _echo(f"BLOCKING ({len(blocking)}) — these must be answered before planning:")
        for index, item in enumerate(blocking, 1):
            _echo(f"  {index}. {item}")
    if non_blocking:
        _echo(f"non-blocking ({len(non_blocking)}) — proceed without if needed:")
        for index, item in enumerate(non_blocking, 1):
            _echo(f"  {index}. {item}")
    if resolved:
        _echo(f"resolved ({len(resolved)}):")
        for item in resolved[-5:]:
            _echo(f"  · {item}")
    if not blocking:
        _echo("no blocking questions — spec is ready to plan")
    return 0


def cmd_resolve(args):
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.name, "spec")
    if not slug:
        _echo("no active spec — /ctx:spec «intent» first")
        return _advise("no-active-work")
    if not spec_mod.resolve(layout, slug, args.question, args.answer):
        _echo(f"no open question matching {args.question!r}")
        return 1
    journal.append(layout, config, "spec", slug, f"resolved: {args.question[:60]}")
    ready, blocking = spec_mod.ready(layout, slug)
    if ready:
        spec_mod.mark(layout, slug, "ready")
        _echo(f"resolved · spec {slug} is now ready to plan")
    else:
        _echo(f"resolved · {len(blocking)} blocking question(s) remain")
    return 0


def cmd_spec_ready(args):
    """Gate 1 as an exit code, so CI can enforce it too."""
    layout, _config = _loaded(args)
    slug = _active_slug(layout, args.name, "spec")
    if not slug:
        # A gate, so this stays non-zero: "no spec" is not "spec is ready".
        _echo("no active spec — nothing to gate")
        return 1
    ready, blocking = spec_mod.ready(layout, slug)
    if ready:
        _echo(f"spec {slug}: ready")
        return 0
    _echo(f"spec {slug}: BLOCKED on {len(blocking)} question(s)")
    for item in blocking:
        _echo(f"  - {item}")
    return 1


def cmd_decide(args):
    layout, config = _loaded(args)
    # Taking the title as loose words means an apostrophe or a quote in it can
    # no longer split the shell command apart.
    title = _free_text(args.title)
    if not title:
        return _needs(
            "decision title",
            "Ask what was decided in one line, then run: ctx decide «title»",
        )
    slug = bundle.slugify(title)
    path = spec_mod.write_decision(
        layout, title, slug, args.context or "", args.decision or "",
        args.consequences or "",
    )
    journal.append(layout, config, "decide", layout.rel(path), title[:60])
    _echo(f"wrote {layout.rel(path)}")
    return 0


# --------------------------------------------------------------------------- #
# phase 4 — the done-gate
# --------------------------------------------------------------------------- #

def _unit_diff_scope(layout, item):
    """`(since, wave)` for active work — what the `diff` kind needs to be fair.

    `(None, ())` for anything that is not a unit inside a plan: an L1 task has
    no wave to be concurrent with and no dispatch seal to have recorded a
    commit, and the check then behaves exactly as it always did.
    """
    slug = str((item.doc.meta or {}).get("plan") or "").strip()
    if item.level != "2" or not slug:
        return None, ()
    unit = plan_mod.find_unit(layout, slug, item.key)
    if unit is None:
        return None, ()
    wave, _siblings = review_mod.wave_scope(layout, slug, unit)
    return contract_mod.sealed_commit(layout, slug, unit.name), wave


def cmd_verify(args):
    """Run the gate by hand. Same code path the Stop hook uses."""
    layout, config = _loaded(args)

    if args.plan:
        return _verify_plan(layout, config, bundle.slugify(args.plan))

    item = work.active(layout)
    if item is None:
        _echo("nothing active to verify — /ctx:task or /ctx:spec first")
        return _advise("no-active-work")

    if args.sign_off:
        item.record(args.sign_off, args.note or "")
        journal.append(layout, config, "gate", item.key, f"signed off {args.sign_off}")
        _echo(f"recorded {args.sign_off} sign-off for {item.key}")
        _echo("note: any subsequent edit clears this — a sign-off cannot outlive the code")
        return 0

    checks = verify.ordered(item.checks)
    if not checks:
        _echo(f"{item.key}: no verify checks configured — the gate cannot hold")
        return 1

    # The same two inputs the done-gate gives the `diff` kind, so running the
    # gate by hand answers the same question `ctx unit --status done` will.
    # Empty for L1 task work, which has no plan, no wave and no seal.
    since, wave = _unit_diff_scope(layout, item)
    results, verdict = verify.run(
        layout, config, item.checks, cwd=layout.root.parent, key=item.key,
        owns=item.owns, recorded=item.recorded, judged=True,
        since=since, wave=wave,
    )
    _echo(f"{item.key} — {verdict.upper()}")
    for result in results:
        _echo(result.line())

    pending = [r for r in results if r.status == verify.PENDING]
    if pending:
        _echo("")
        _echo("judged checks need evaluation before the gate can pass:")
        for result in pending:
            _echo(f"  {result.kind}: {result.message}")

    if verdict == verify.PASS:
        state.clear_attempts(layout, item.attempt_key)
    journal.append(layout, config, "gate", item.key, f"manual {verdict}")
    # Distinct exit codes so CI can tell "the work is wrong" from "the checks are
    # broken": 0 pass, 1 a criterion failed, 2 nothing could be run.
    if verdict == verify.PASS:
        return 0
    if verdict == verify.ERROR:
        _echo("")
        _echo("no check could run — this is a ctx.yaml problem, not a work failure")
        return 2
    return 1



# --------------------------------------------------------------------------- #
# phase 5 — plans, waves and dispatch
# --------------------------------------------------------------------------- #

def cmd_plan(args):
    """Scaffold a plan. Gate 1 is enforced here: an ambiguous spec cannot be planned."""
    layout, config = _loaded(args)
    if not args.name:
        return _needs(
            "plan name",
            "Ask what to call the plan, then run: ctx plan «name»",
        )
    slug = bundle.slugify(args.name)
    # The plan rarely shares the spec's name, so fall back to the spec that is
    # actually active before assuming they match.
    active_spec = state.load(layout).get("spec")
    spec_slug = bundle.slugify(args.spec or active_spec or slug)

    if spec_mod.spec_path(layout, spec_slug).is_file():
        ready, blocking = spec_mod.ready(layout, spec_slug)
        if not ready:
            _echo(f"refusing to plan: spec {spec_slug} has {len(blocking)} unanswered")
            _echo("blocking question(s). Answer them first — /ctx:ask")
            for item in blocking:
                _echo(f"  - {item}")
            return 1
    elif not args.no_spec:
        _echo(f"no spec at {layout.rel(spec_mod.spec_path(layout, spec_slug))}")
        _echo("run /ctx:spec first, or pass --no-spec to plan without one")
        return 0

    plan_mod.create(layout, slug, spec_slug)
    created = []
    for index, name in enumerate(args.unit or [], 1):
        unit_name = name if name[:2].isdigit() else f"{index:02d}-{bundle.slugify(name)}"
        path, fresh = plan_mod.scaffold_unit(
            layout, slug, unit_name, verify_checks=config.get("verify") or []
        )
        created.append((unit_name, fresh, path))

    state.update(layout, level="2", plan=slug, spec=spec_slug, unit=None)
    journal.append(layout, config, "plan", slug, f"opened ({len(created)} unit stubs)")

    _echo(f"L2 planned · plan {slug}")
    _echo(f"readme {layout.rel(plan_mod.readme_path(layout, slug))}")
    _echo(f"units  {layout.rel(plan_mod.units_dir(layout, slug))}/")
    for unit_name, fresh, path in created:
        _echo(f"  {'created' if fresh else 'exists '} {layout.rel(path)}")
    if not created:
        _echo("  no units yet — `ctx plan-unit` or write NN-name.md files directly")
    _echo("then run `ctx plan-check` to compute waves and check for collisions")
    return 0


def cmd_plan_unit(args):
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return _advise("no-active-work")
    path, fresh = plan_mod.scaffold_unit(
        layout, slug, args.name, objective=args.objective or "",
        tier=args.tier, owns=args.owns or [],
        verify_checks=config.get("verify") or [],
    )
    _echo(f"{'created' if fresh else 'exists'} {layout.rel(path)}")
    return 0


def cmd_plan_check(args):
    """Validate, compute waves from depends_on, and derive plan.json."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.name, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return _advise("no-active-work")

    grouped, problems = plan_mod.check(layout, slug)
    if problems:
        _echo(f"plan {slug}: {len(problems)} problem(s) — nothing was written")
        for problem in problems:
            _echo(f"  - {problem}")
        _echo("")
        _echo("Collision problems name the `depends_on` line that fixes them. They are")
        _echo("not auto-repaired: rewriting a dependency graph is a planning decision.")
        return 1

    plan_mod.apply_waves(grouped)
    path, revision = plan_mod.write_graph(layout, slug, grouped)
    plan_mod.render_readme_units(layout, slug, grouped)
    journal.append(layout, config, "plan", slug, f"checked, graph r{revision}")

    total = sum(len(units) for units in grouped.values())
    _echo(f"plan {slug}: {total} unit(s) in {len(grouped)} wave(s) · graph r{revision}")
    for level in sorted(grouped):
        names = ", ".join(u.name for u in grouped[level])
        _echo(f"  wave {level}: {names}")
    sessions = [u.name for units in grouped.values() for u in units if u.tier == "session"]
    if sessions:
        problem = worktree.check_repo(layout)
        _echo("")
        if problem:
            _echo(f"note: {', '.join(sessions)} use tier `session`, but {problem}")
        else:
            _echo(
                f"note: {', '.join(sessions)} "
                + ("is tier `session` — it runs" if len(sessions) == 1
                   else "are tier `session` — they run")
                + " in the main tree unless you dispatch with `ctx start --worktree`"
            )
    _echo(f"wrote {layout.rel(path)}")
    return 0


def _adopt_disk_state(layout, slug, units):
    """Replace each unit's parsed document with what is on disk *now*.

    A `Unit` is a whole document held in memory, and `Unit.set` writes that
    whole document back. So the value of one is only as fresh as the moment it
    was parsed, and every field it is not being asked to change is carried
    along for the ride — including fields another writer has since added.

    Units not found on disk are left exactly as they are: a unit file that was
    deleted mid-command is not a reason to lose the write that was about to be
    made to it.
    """
    fresh = {unit.name: unit for unit in plan_mod.load_units(layout, slug)}
    for unit in units:
        found = fresh.get(unit.name)
        if found is not None:
            unit.doc = found.doc
    return units


def _set_unit_status(layout, slug, units, status):
    """Write `status` onto each unit, over the document that is on disk now.

    The read-modify-write this closes is not a narrow one. Between the moment a
    unit file is parsed and the moment `unit.set` writes it back whole, this
    process may have spent four minutes running a verify command (`ctx unit
    --status done`) or created a worktree (`ctx start --worktree`) — and
    `worktree._record_fork_point` writes `base_branch` into that same file
    through a *separate* read. Writing the stale document back erases it, which
    silently disarms the merge-target guard the whole of `worktree.py` is built
    around: `merge` reads `base_branch`, finds nothing, and merges into
    whatever HEAD happens to be on.

    The lock spans the re-read and the write and nothing else. The expensive
    part — the gate, the worktree creation — deliberately runs outside it:
    holding `plan-<slug>` for the length of the slowest verify command would
    block every sibling in a concurrent wave, which is a worse defect than the
    one being fixed. `lock.held` is not re-entrant and the findings and phases
    mutators take this same lock internally, so nothing that can reach one of
    them may be called from inside this span.

    The caller's verdict wins over the disk for `status` and only for `status`.
    A sibling process that wrote `status: pending` while the gate was deciding
    `done` does not get to overturn the gate; it does get to keep every other
    field it wrote.
    """
    with lock.held(layout, f"plan-{slug}"):
        _adopt_disk_state(layout, slug, units)
        for unit in units:
            # Compared against the raw frontmatter value, not `unit.status`,
            # which normalises anything unrecognised to `pending` — a file
            # reading `status: half-done` must be corrected, not skipped.
            if str(unit.doc.meta.get("status") or "") != status:
                unit.set(status=status)
    return units


def _already_dispatched(layout, slug, unit):
    """Whether this unit has already been sent out once.

    Three signals, any of which is enough. A sealed contract or a stored
    `before` snapshot *is* the evidence a later `ctx start` would destroy, so
    their existence is the guard — not the unit's `status`, which the runner
    holds `Write` over and could set back to `pending` to launder a forged
    contract through a re-seal. `status: running` is checked last, for the unit
    whose snapshot failed at dispatch: capturing one now would fingerprint the
    work instead of what preceded it, which is exactly the lie this prevents.
    """
    if contract_mod.load_seal(layout, slug, unit.name) is not None:
        return True
    if snapshot_mod.load(layout, review_mod.before_key(slug, unit.name)) is not None:
        return True
    return unit.status == "running"


def _asked_for(args, flag, known, level):
    """The units named by `--rebaseline` / `--reseal`, minus the ones that are
    not in this wave — which are reported rather than ignored in silence."""
    asked = list(dict.fromkeys(getattr(args, flag, None) or []))
    for name in asked:
        if name not in known:
            _echo(f"  ! --{flag} {name}: not a unit of wave {level} — ignored")
    return {name for name in asked if name in known}


def cmd_start(args):
    """Print the dispatch brief for a wave. Spawns nothing itself."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.name, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return _advise("no-active-work")

    level, units, problems, budget = dispatch.prepare(layout, config, slug, args.wave)
    if problems:
        # Nothing was dispatched, so this is a refusal and it exits like one.
        # It used to print the reason and return 0 — a refusal reported as
        # success, which no script and no hook could tell from a wave going
        # out. The reason is not thrown away by the non-zero exit: it travels
        # in the message, which `main` puts on stderr, and every `/ctx:` command
        # file already runs `ctx` with `|| true` so the prompt still renders.
        raise SystemExit("\n".join(
            [f"plan {slug}: not ready to dispatch — nothing was started"]
            + [f"  - {problem}" for problem in problems]
        ))
    if not units:
        _echo(f"wave {level}: nothing left to dispatch")
        return 0

    # `--rebaseline` and `--reseal` are deliberate escape hatches, so a name
    # that matches nothing is said out loud rather than silently doing nothing:
    # the whole point of typing one is that something gets retaken.
    known = {unit.name: unit for unit in units}
    rebaseline = _asked_for(args, "rebaseline", known, level)
    reseal = _asked_for(args, "reseal", known, level)
    # A unit named to both gets the stronger one. `--reseal` retakes the
    # baseline as well: accepting a new contract and then judging the work
    # against a snapshot of the old one is not a coherent state to leave behind.
    rebaseline -= reseal

    # What each named unit's contract has done since it was sealed. Read before
    # anything is written, because the re-seal below is what erases the answer.
    moved = {name: contract_mod.changed_fields(layout, slug, known[name])
             for name in sorted(rebaseline | reseal)}

    # A baseline retake is not a re-seal, and a runner holding `Write` over its
    # own unit file is exactly who would like it to be. `--rebaseline` re-records
    # the *review* baseline over the current tree; if the promise itself moved
    # since dispatch, retaking it would quietly bless the new promise, which is
    # the forgery the seal exists to catch. So it refuses and names the door.
    refused = [(name, fields) for name, fields in moved.items()
               if name in rebaseline and fields]
    if refused:
        for name, fields in refused:
            journal.append(
                layout, config, "start", name,
                f"--rebaseline refused: contract changed ({', '.join(fields)})",
            )
        raise SystemExit("\n".join(
            [f"ctx start --rebaseline {name}\n"
             f"  contract changed: {', '.join(fields)}\n"
             "  REFUSED - baseline retake will not re-seal a changed contract\n"
             f"  to accept the new contract: ctx start --reseal {name}"
             for name, fields in refused]
            + ["",
               "A retake re-captures the tree the work is judged against. It does "
               "not, and will",
               "not, re-record the promise: that is a planning decision, it is "
               "journalled as one,",
               "and `--reseal` is where it is made. Nothing was dispatched."]
        ))

    # Opt-in, not opt-out. A worktree holds its branch exclusively: while one
    # exists, `git checkout` of that branch in the main tree is refused, so
    # creating them by default quietly took away the tree the user tests in.
    worktrees, wt_problems = ([], [])
    if args.worktree:
        worktrees, wt_problems = dispatch.prepare_worktrees(layout, slug, units)
    for problem in wt_problems:
        _echo(f"  ! {problem}")
    if wt_problems:
        _echo("")

    # Which of these have been out once already. Computed before anything is
    # written, because flipping `status` below is itself one of the signals.
    in_flight = [unit for unit in units
                 if unit.name not in rebaseline
                 and unit.name not in reseal
                 and _already_dispatched(layout, slug, unit)]
    flying = {unit.name for unit in in_flight}

    # A dispatched unit is `running`. Nothing used to set it — a unit stayed
    # `pending` until an explicit `--status done` — so a re-run of `ctx start`
    # could not tell work that had never been sent out from work that was
    # halfway through, and treated both as fresh.
    #
    # Re-read first. `prepare_worktrees` above has already written `base_branch`
    # into some of these very files, through its own fresh read — so `units`,
    # parsed by `dispatch.prepare` before any of that happened, is stale, and
    # writing it back whole erased the fork point on the same line that recorded
    # the dispatch. That was not a race: it happened every time `ctx start
    # --worktree` ran, in one process.
    _set_unit_status(layout, slug, units, "running")

    # The snapshot has to exist before the work does, and dispatch is the only
    # moment we know that is true. Taking it here is what lets `ctx review` need
    # no commits and no ceremony from the user later.
    captured, rebaselined, resealed = 0, [], []
    for unit in units:
        if unit.name in flying:
            continue  # its baseline is the evidence; leave it exactly alone
        force = unit.name in rebaseline or unit.name in reseal
        try:
            review_mod.capture_before(layout, config, unit, slug,
                                      layout.root.parent, force=force)
            captured += 1
        except OSError as exc:  # a snapshot must never block a dispatch
            _echo(f"  ! could not snapshot {unit.name} for review: {exc}")
        # Dispatch is also the only moment at which the unit's contract is known
        # to predate the work. Sealed here so the done-gate can tell a unit that
        # met its promise from one that edited the promise — the runner holds
        # `Write`, and its own unit file is inside the `.ctx/` scope exemption.
        # Skipped for an in-flight unit above for the same reason the snapshot
        # is: re-sealing would re-record a contract edited *after* dispatch as
        # the legitimate one, which is the forgery the seal exists to catch.
        contract_mod.seal(layout, config, slug, unit, layout.root.parent)
        if unit.name in reseal:
            # The loud half of the pair. A re-seal accepts a contract that
            # *moved* — the fields it moved are the whole reason anyone would
            # want this entry later, so they are in it by name, and the entry is
            # written whether or not anything moved, because "nothing had
            # changed" is also worth being able to prove.
            resealed.append(unit.name)
            fields = moved.get(unit.name) or []
            journal.append(
                layout, config, "start", unit.name,
                "re-sealed deliberately (--reseal): "
                + (f"contract changed ({', '.join(fields)}) and the new contract "
                   "is now the promise the done-gate holds it to"
                   if fields else
                   "the contract had not changed; the seal and review baseline "
                   "were retaken over the current tree")
                + " (recorded findings kept)",
            )
        elif force:
            rebaselined.append(unit.name)
            journal.append(
                layout, config, "start", unit.name,
                "re-baselined deliberately (--rebaseline): the review baseline "
                "was re-captured over the current tree and the contract "
                "re-sealed, so this unit's contract as it now stands is the "
                "promise it will be judged against (recorded findings kept)",
            )

    for unit in units:
        # One record per dispatched unit, regardless of tier: `model` and
        # `score` are exactly what the dispatch line above already prints for
        # `subagent` units, recorded here too so `ctx telemetry`'s `by_role`
        # breakdown has something to group spend on for every wave, not just
        # the ones a human happened to be reading when it ran.
        score, _breakdown = complexity_mod.score(config, unit)
        telemetry.record(
            layout, "dispatch", 0,
            model=dispatch.model_for(config, unit), role="runner", score=score,
        )

    if resealed:
        for name in resealed:
            fields = moved.get(name) or []
            _echo(f"  re-sealed {name}: "
                  + (f"its contract changed ({', '.join(fields)}) and that change "
                     "is now the promise" if fields else
                     "its contract had not changed; the seal was retaken")
                  + " — recorded in the journal")
        _echo("")
    if rebaselined:
        for name in rebaselined:
            _echo(f"  re-baselined {name}: its `before` snapshot was retaken and "
                  "its contract re-sealed — the unit file as it stands now is "
                  "the promise the done-gate will hold it to")
        _echo("")
    if in_flight:
        _echo(f"{len(in_flight)} unit(s) already in flight — dispatched earlier "
              "and not yet done:")
        for unit in in_flight:
            _echo(f"  - {unit.name}")
        _echo("Their review baseline and sealed contract were left untouched: "
              "re-capturing now would diff the finished work against itself and "
              "review as approved. Re-dispatch them from the brief below.")
        _echo(f"To retake one on purpose: ctx start --rebaseline {in_flight[0].name}")
        _echo("")

    journal.append(
        layout, config, "start", slug,
        f"wave {level}, {len(units)} unit(s), {len(worktrees)} worktree(s), "
        f"{captured} snapshot(s), {len(in_flight)} already in flight",
    )
    _echo(dispatch.instructions(
        layout, config, slug, level, units, budget, worktrees,
        worktree_requested=bool(args.worktree),
    ))
    return 0


# --------------------------------------------------------------------------- #
# phase 6 — the worktree tier
# --------------------------------------------------------------------------- #

def _unit_or_report(layout, args, verb):
    """(slug, unit) for a unit-scoped command, or (None, None) having said why."""
    slug = _active_slug(layout, getattr(args, "plan", None), "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        _advise("no-active-work")
        return None, None
    if not args.name:
        _echo(f"no unit name given. Units in plan {slug}:")
        _list_units(layout, slug)
        _echo(f"Ask which unit to {verb}, then run: ctx {verb} «unit-name»")
        _advise("missing-argument")
        return None, None
    unit = plan_mod.find_unit(layout, slug, args.name)
    if unit is None:
        _echo(f"no unit {args.name!r} in plan {slug}")
        return None, None
    return slug, unit


def cmd_snapshot(args):
    """Capture a content snapshot of the tree for one unit.

    `ctx start` does the `before` phase for every unit it dispatches, so this is
    for work that reached a unit by some other route.
    """
    layout, config = _loaded(args)
    slug, unit = _unit_or_report(layout, args, "snapshot")
    if unit is None:
        return 0
    root = layout.root.parent
    # `force=True` on both phases: naming a unit on the command line *is* the
    # deliberate act `snapshot.capture` refuses without. What it guards against
    # is `ctx start` silently retaking a baseline as a side effect of being run
    # twice, not a person asking for one by name.
    if args.phase == "before":
        replaced = snapshot_mod.load(layout, review_mod.before_key(slug, unit.name))
        manifest = review_mod.capture_before(layout, config, unit, slug, root,
                                             force=True)
        key = review_mod.before_key(slug, unit.name)
        if replaced is not None:
            _echo(f"  ! replaced the existing before snapshot for {unit.name} — "
                  "`ctx review` now diffs against the tree as it is now")
    else:
        key = review_mod.after_key(slug, unit.name, args.round)
        manifest = snapshot_mod.capture(
            layout, config, key, root,
            content_paths=list(unit.owns) + list(unit.reads), force=True,
        )
    _echo(f"{args.phase} snapshot for {unit.name}: {len(manifest['files'])} file(s) "
          f"fingerprinted, {len(manifest['stored'])} stored")
    if manifest.get("truncated"):
        _echo("  warning: the file cap was reached, so this snapshot is incomplete")
        _echo("  — raise review.max_files or widen review.ignore in ctx.yaml")
        _advise("snapshot-truncated")
    journal.append(layout, config, "snapshot", unit.name, args.phase)
    return 0


def cmd_review(args):
    """Build the review package for a unit. Spawns no reviewer itself."""
    layout, config = _loaded(args)
    slug, unit = _unit_or_report(layout, args, "review")
    if unit is None:
        return 0

    ledger = findings_mod.load(layout, slug, unit.name)
    if args.round:
        round_number = args.round
    else:
        # A second `ctx review` means a fix round, not the same review again.
        # The round only advances when the previous one actually raised
        # something — re-running against a clean review should not burn a round.
        raised = [f for f in ledger.findings if f.round == ledger.round]
        if raised:
            # `model` is what the round just ending actually ran on — the same
            # value `ctx start` would have dispatched it at — so `bump_round`
            # escalates from where the round genuinely was, not from a guess.
            # It is a no-op unless `models.escalate_on_failed_round` is on, in
            # which case a real escalation gets a line here and in the journal
            # rather than only living inside the ledger file.
            failed_round_model = dispatch.model_for(
                config, unit, role="runner", round=ledger.round,
            )
            before = len(ledger.escalations)
            round_number = ledger.bump_round(config, failed_round_model)
            if len(ledger.escalations) > before:
                escalation = ledger.escalations[-1]
                _echo(f"  escalated: {escalation.line()}")
                journal.append(layout, config, "escalate", unit.name, escalation.line())
        else:
            round_number = ledger.round
    if round_number > findings_mod.MAX_ROUNDS:
        # Past the cap the loop does not converge; the failure is structural and
        # more rounds only spend tokens on it. Every remaining finding becomes a
        # decision taken on the user's behalf, which is what a ruling is for.
        _echo(f"round cap reached ({findings_mod.MAX_ROUNDS}) — stop dispatching fixes")
        for finding in ledger.blocking():
            _echo("  " + finding.line())
        _echo("")
        _echo("Rule on each one and park it:")
        _echo(f"  ctx findings {unit.name} --set «id» --status parked --ruling «why»")
        _echo("A parked finding is a decision made for the user, so record it with")
        _echo("/ctx:decide as an ADR — that is what keeps it reviewable.")
        journal.append(layout, config, "review", unit.name, "round cap reached")
        return 1
    previous = (review_mod.after_key(slug, unit.name, round_number - 1)
                if round_number > 1 else None)
    path, stats, problem = review_mod.build(
        layout, config, unit, slug, layout.root.parent, round_number, previous
    )
    if problem:
        _echo(problem)
        return 1

    _echo(f"review package  {layout.rel(path)}")
    _echo(f"  round {round_number} · +{stats['added']} ~{stats['modified']} "
          f"-{stats['deleted']} · {stats['bytes']:,} bytes")
    if stats["out_of_scope"]:
        _echo(f"  {stats['out_of_scope']} path(s) changed outside `owns` — that is a "
              "Critical finding, already decided")
    if ledger.findings:
        _echo(f"  findings: {ledger.summary()}")
        # Observed, not authoritative (see `contract.seal_findings`): building a
        # package for round N+1 is also the moment ctx last saw round N's
        # findings intact.
        contract_mod.seal_findings(layout, slug, unit.name, ledger)
    _echo("")
    # Sized off the package that was just built, not the flat `models.reviewer`
    # floor: a small, in-scope package earns the cheaper tier one below it (see
    # `dispatch.model_for`'s `stats` branch), so naming it here — the same way
    # every dispatch line already names a model — is what keeps a Task call
    # from defaulting to the orchestrator's own, pricier model out of habit.
    reviewer_model = dispatch.model_for(
        config, role="reviewer", stats=stats, round=round_number,
    )
    _echo(f"Dispatch the `reviewer` agent against {layout.rel(path)}, "
          f"on **{reviewer_model}**.")
    _echo("Do not read the package yourself — that is the reviewer's context, not")
    _echo("yours, and reading it here defeats the point of the separate seat.")
    # The audit asked for this to be measurable rather than asserted: package
    # bytes are the context the orchestrator did *not* spend re-deriving a diff.
    # `model`/`role` are ordinary fields (see `telemetry.record`'s docstring),
    # not dedicated parameters — added here so `ctx telemetry`'s per-role
    # breakdown has review spend to report, same as dispatch's runner spend.
    telemetry.record(layout, "review", 0, bytes=stats["bytes"],
                     round=round_number, out_of_scope=stats["out_of_scope"],
                     model=reviewer_model, role="reviewer")
    journal.append(layout, config, "review", unit.name,
                   f"round {round_number}, {stats['bytes']} bytes")
    return 0


def cmd_findings(args):
    """List or update the findings recorded against a unit."""
    layout, config = _loaded(args)
    slug, unit = _unit_or_report(layout, args, "findings")
    if unit is None:
        return 0
    ledger = findings_mod.load(layout, slug, unit.name)

    if args.add:
        finding = ledger.add(args.add, args.summary or "(no summary given)",
                             where=args.where or "", evidence=args.evidence or "")
        # Authoritative: this *is* the legitimate way to record a finding, so
        # the seal takes it verbatim. Once sealed, deleting the finding from the
        # file by hand is visible to the done-gate.
        contract_mod.seal_findings(
            layout, slug, unit.name, ledger, authoritative=True
        )
        _echo(f"recorded [{finding.id}] {finding.severity}: {finding.summary}")
        journal.append(layout, config, "finding", unit.name,
                       f"{finding.severity} #{finding.id}")
        return 0

    if args.set is not None:
        ok, problem = ledger.set_status(
            args.set, args.status or "", ruling=args.ruling or "",
            evidence=args.evidence or "",
        )
        if not ok:
            _echo(problem)
            return 1
        # Also authoritative: `--set` is how a finding is *meant* to move, so a
        # legitimate close re-seals rather than tripping the gate.
        contract_mod.seal_findings(
            layout, slug, unit.name, ledger, authoritative=True
        )
        _echo(f"[{args.set}] is now {args.status}")
        journal.append(layout, config, "finding", unit.name,
                       f"#{args.set} -> {args.status}")
        return 0

    # Listing only observes: it may add a finding to the seal or strengthen one,
    # never drop or soften one. That is what stops a hand-edit of the findings
    # file from laundering itself by running a read-only ctx command afterwards.
    contract_mod.seal_findings(layout, slug, unit.name, ledger)

    if not ledger.findings and not ledger.escalations:
        _echo(f"no findings recorded for {unit.name}")
        return 0
    if ledger.findings:
        _echo(ledger.summary())
        for finding in ledger.findings:
            _echo("  " + finding.line())
    if ledger.escalations:
        # A fact about a round, never a finding's status (see `findings`'s
        # module docstring) — shown separately so "what got escalated and
        # why" stays visible even once every finding that caused it is closed.
        _echo("")
        _echo("escalations:")
        for escalation in ledger.escalations:
            _echo("  " + escalation.line())
    blocking = ledger.blocking()
    if blocking:
        _echo("")
        _echo(f"{len(blocking)} blocking finding(s) — the `review` gate refuses "
              "while any critical or important finding is open.")
        return 1
    return 0


def cmd_phase(args):
    """Advance or inspect a unit's phase gate.

    A `kind: bug` unit gates through `reproduce` → `locate` → `fix` → `guard`
    in that order (`phases.BUG`); any other unit that declares its own
    `phases: […]` gates through exactly that list, with no bug semantics
    attached. Either way the door is `phases.can_enter`, and this command
    never opens one it refuses: `phases.record` checks the gate itself before
    it writes anything, so a caller here cannot skip the check by forgetting
    to call it first.
    """
    layout, config = _loaded(args)
    slug, unit = _unit_or_report(layout, args, "phase")
    if unit is None:
        return 0

    declared = phases_mod.for_unit(unit)
    if not declared:
        _echo(f"{unit.name} declares no phases — nothing to gate. Set `kind: bug` "
              "or add a `phases:` list to its frontmatter to turn this on.")
        return 0

    ledger = phases_mod.load(layout, slug, unit.name)

    if not args.phase:
        # Inspection only — nothing is written. This is what to run before
        # recording anything: it names what is already there and whether the
        # next phase is open, rather than the caller guessing from the unit
        # file what the gate currently wants.
        _echo(f"phases for {unit.name}: " + " -> ".join(p.name for p in declared))
        for phase in declared:
            entries = ledger.for_phase(phase.name)
            ok, why = phases_mod.can_enter(unit, ledger, phase.name)
            state_desc = "open" if ok else f"locked — {why}"
            plural = "entry" if len(entries) == 1 else "entries"
            _echo(f"  {phase.name}: {len(entries)} {plural} recorded — {state_desc}")
            if phase.check:
                # `guard`'s check, handed to `verify.label_of` exactly as
                # `phases.for_unit` built it — unreworded, because this text is
                # what a verifier is meant to judge, and a paraphrase here
                # could quietly ask a different question than `verify.run`
                # would when the same check is used elsewhere.
                _echo(f"    judged on: {verify.label_of(phase.check)}")
        return 0

    ok, result = phases_mod.record(
        layout, slug, unit, args.phase,
        command=args.command or "", exit_code=args.exit_code,
        evidence=args.evidence or "", note=args.note or "",
    )
    if not ok:
        # `result` is `can_enter`'s `why`, unmodified — the refusal a `bug`
        # unit gets when it reaches for `fix` with no failing reproduction
        # recorded has to name that specific missing prerequisite. Rewording
        # it into "not allowed" would throw away the only thing that tells
        # the caller what to do next.
        _echo(result)
        return 1
    journal.append(layout, config, "phase", unit.name, f"{args.phase} recorded")
    _echo(f"recorded {args.phase} for {unit.name}"
          + (f" (exit {args.exit_code})" if args.exit_code is not None else ""))
    return 0


def _merge_note(ok, messages):
    """The journal note for a merge — and the second half of the override trail.

    `ctx unit --status done --force` records *what it overrode*, not merely that
    it was forced. `--skip-gate` is the other door to the same place, and until
    now it recorded only `ok`: `worktree.merge` returns the override text, the
    terminal prints it, and it scrolls away. An override nobody can find later
    is not meaningfully different from no gate at all.

    Same words as the forced-done entry — "overrode the gate" — so one grep over
    the journal finds every override of the done-gate, whichever door it used.
    """
    if not ok:
        return "refused"
    override = next((m for m in messages if m and "--skip-gate overrode" in m), "")
    if not override:
        return "ok"
    # What `merge` already worked out about the skipped checks, kept verbatim
    # rather than recomputed: the count, and as many of them as it named.
    detail = override.split(" — ", 1)[-1].strip()
    detail = detail.replace(
        ", so nothing about this unit was verified before merging", ""
    )
    return f"ok (--skip-gate overrode the gate: {detail or 'the gate did not run'})"


def cmd_merge(args):
    """Land a unit's worktree branch. Refuses past a failed gate or stray writes."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return _advise("no-active-work")
    if not args.name:
        _echo(f"no unit name given. Units in plan {slug}:")
        _list_units(layout, slug)
        _echo("Ask which unit to merge, then run: ctx merge «unit-name»")
        return _advise("missing-argument")

    ok, messages = worktree.merge(
        layout, config, slug, args.name, skip_gate=args.skip_gate
    )
    for message in messages:
        if message:
            _echo(f"  {message}")
    journal.append(layout, config, "merge", args.name, _merge_note(ok, messages))
    if ok:
        state.update(layout, unit=None)
        state.clear_attempts(layout, args.name)
        remaining = plan_mod.next_wave(layout, slug)
        _echo(
            f"  next: wave {remaining} — /ctx:start" if remaining
            else f"  plan {slug} is complete"
        )
        return 0
    _echo("  nothing was merged")
    return 1


def _verify_plan(layout, config, slug):
    """Every unit in a plan, headless. This is what CI runs.

    Mechanical checks only: `rubric` and `human` need a model or a person, so an
    unattended run reports them pending rather than pretending to judge them.
    """
    grouped, problems = plan_mod.check(layout, slug)
    if problems:
        _echo(f"plan {slug}: {len(problems)} problem(s) — not verifying units")
        for problem in problems:
            _echo(f"  - {problem}")
        return 1

    failed, pending, passed, warned = [], [], [], []
    for level in sorted(grouped):
        for unit in grouped[level]:
            # A unit with no usable checks never gets here: plan_mod.check()
            # rejects it as a validation problem above.
            # Same scope the done-gate would give this unit: from where it was
            # dispatched, and forgiving of the siblings running beside it. A CI
            # run that judged a wave in flight by each unit's `owns` alone would
            # report N−1 scope violations for work that is entirely in scope.
            wave, _siblings = review_mod.wave_scope(layout, slug, unit)
            results, verdict = verify.run(
                layout, config, unit.checks, cwd=layout.root.parent,
                key=f"ci-{unit.name}", owns=unit.owns, recorded=unit.recorded,
                judged=False,
                since=contract_mod.sealed_commit(layout, slug, unit.name),
                wave=wave,
            )
            flag = {
                verify.PASS: "ok  ", verify.FAIL: "FAIL",
                verify.PENDING: "wait", verify.ERROR: "warn",
            }[verdict]
            _echo(f"  {flag} wave {level} {unit.name} ({unit.status})")
            if verdict == verify.FAIL:
                failed.append(unit.name)
                _echo("       " + verify.summarise(results).replace("\n", "\n       "))
            elif verdict == verify.PENDING:
                pending.append(unit.name)
            elif verdict == verify.ERROR:
                # Not a pass: something in this unit's gate could not run at all.
                warned.append(unit.name)
            else:
                passed.append(unit.name)

    _echo("")
    _echo(
        f"{len(passed)} passed · {len(pending)} awaiting sign-off · "
        f"{len(warned)} could not run · {len(failed)} failed"
    )
    journal.append(
        layout, config, "verify", slug,
        f"plan run: {len(passed)}p/{len(pending)}w/{len(warned)}e/{len(failed)}f",
    )
    return 1 if failed else 0


def _worktree_plan(layout, args):
    """Which plan's worktree `remove` should act on, or None to let it resolve.

    `--plan` is explicit and always wins. Otherwise the active plan answers —
    but only when it actually has a tree by that name. That guard is the whole
    point: handing `remove` a plan that has no such worktree would turn the
    ordinary "I am working on plan-a, discard 03-rotate" into a path that does
    not exist, where the honest answer is the one `worktree._resolve` already
    gives. So the common case resolves without the ambiguity scan, and the scan
    stays reachable — and stays the thing that refuses — for every case the
    active plan cannot answer.
    """
    if getattr(args, "plan", None):
        return args.plan
    slug = (state.load(layout) or {}).get("plan")
    if slug and args.name and worktree.path_for(layout, slug, args.name).is_dir():
        return slug
    return None


def cmd_worktree(args):
    layout, config = _loaded(args)
    if args.action == "list":
        rows = worktree.listing(layout)
        if not rows:
            _echo("no ctx worktrees")
            return 0
        for name, path, branch in rows:
            _echo(f"{name:<24} {branch:<36} {path}")
        return 0

    error = worktree.remove(layout, args.name, plan_slug=_worktree_plan(layout, args),
                            force=args.force)
    if error:
        _echo(f"could not remove: {error}")
        _echo("pass --force to discard uncommitted work in the worktree")
        return 1
    journal.append(layout, config, "worktree", args.name, "removed")
    _echo(f"removed worktree and branch for {args.name}")
    return 0


def _missing_commit_advice(results, unit):
    """The long half of "the dispatch point is gone".

    A `Result` message is shown to one terminal width, which is the right size
    for a scope violation and too small for this: the SHA alone is 40 characters
    of it. So the check reports the SHA and the flag, and the explanation of
    *why* a gate can neither pass nor error here lives out here, where there is
    room for it.
    """
    if not any(result.kind == "diff" and result.status == verify.FAIL
               and str(result.message).startswith(verify.MISSING_COMMIT)
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


def _gate_check(layout, config, slug, unit):
    """(ok, reason, lines) — may this unit be marked `done`, and if not, why.

    Ordered by what invalidates what, and by cost. A forged contract makes every
    result below it meaningless, so it is answered first; it and the empty-checks
    case are both pure file reads, and neither spawns a process.

    Split out of `_gate_before_done` so that `--force` can say *what* it is
    overriding. An escape hatch that records "forced" and nothing else leaves no
    trace of the thing it stepped over, which is exactly the trace that matters
    later.
    """
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
            f"this plan have one, so this unit was sent out around it), or pass "
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
    if not verify.ordered(unit.checks):
        return False, "no usable verify checks", lines + [
            f"refusing to mark {unit.name} done — it has no usable verify checks",
            "add a `verify` block to the unit file, or pass --force to override",
        ]

    # 3. The checks themselves.
    #
    # `since` is where the unit started, so the `diff` check sees what it
    # *committed* as well as what is still in the working tree; `wave` is what
    # its concurrent siblings own, so their in-flight writes are not counted as
    # this unit's scope violation. Both are read here rather than inside
    # `verify` because `plan` imports `verify`, and the reverse import would
    # close the loop.
    since = contract_mod.sealed_commit(layout, slug, unit.name)
    if since is None and contract_mod.load_seal(layout, slug, unit.name):
        lines.append(
            f"note: no dispatch commit recorded for {unit.name}, so anything it "
            "committed was not checked for scope — /ctx:start records one from now on"
        )
    wave, _siblings = review_mod.wave_scope(layout, slug, unit)
    results, verdict = verify.run(
        layout, config, unit.checks, cwd=layout.root.parent,
        key=f"{slug}/{unit.name}", owns=unit.owns, recorded=unit.recorded,
        judged=False, since=since, wave=wave,
    )
    if verdict in (verify.FAIL, verify.PENDING):
        return False, verdict, lines + [
            f"refusing to mark {unit.name} done — the gate did not pass",
            *[result.line() for result in results],
            *_missing_commit_advice(results, unit),
            "",
            "Fix the failing criterion, or pass --force if you are deliberately",
            "overriding the gate — which is a decision worth saying out loud.",
        ]
    if verdict == verify.ERROR and not any(r.status == verify.PASS for r in results):
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
    if verdict == verify.ERROR:
        # Some check did pass, so the gate is not blind — this is today's
        # behaviour, kept deliberately. The refusal above is for *no check
        # reached PASS*, never for *any check errored*.
        lines += [
            f"warning: not every check could run for {unit.name} — configuration, "
            "not a work failure, so this is not blocking:",
            *[result.line() for result in results],
        ]
    return True, "", lines


def _gate_before_done(layout, config, slug, unit):
    """Non-zero exit code when this unit has not earned `done`, else None.

    Only the worktree `merge` path used to verify before completing a unit. For
    `subagent` — the default tier, and the one the dispatch brief pushes hardest
    — `done` was whatever the orchestrator typed after reading a report the unit
    had written about itself. The strongest guarantee in the system did not cover
    its most common path.
    """
    ok, reason, lines = _gate_check(layout, config, slug, unit)
    for line in lines:
        _echo(line)
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


def cmd_unit(args):
    """Focus a unit so the done-gate applies to it, or record its outcome."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return _advise("no-active-work")

    if not args.name:
        _echo(f"no unit name given. Units in plan {slug}:")
        _list_units(layout, slug)
        return _advise("missing-argument")

    unit = plan_mod.find_unit(layout, slug, args.name)
    if unit is None:
        _echo(f"no unit {args.name!r} in plan {slug}. Units in plan {slug}:")
        _list_units(layout, slug)
        return 1

    note = args.status
    if args.status == "done":
        if not args.force:
            refusal = _gate_before_done(layout, config, slug, unit)
            if refusal:
                return refusal
        else:
            # `--force` still runs the gate — it just does not obey it. Recording
            # "forced" without recording *what was overridden* throws away the
            # only fact anyone will want later, and the reason is knowable only
            # by asking. Loud, in the journal and on the terminal.
            ok, reason, lines = _gate_check(layout, config, slug, unit)
            if ok:
                note = "done (--force; the gate passed anyway)"
            else:
                note = f"done (--force overrode the gate: {reason})"
                _echo(f"warning: --force overrode the done-gate for {unit.name} "
                      f"({reason}). What it refused:")
                for line in lines:
                    _echo(line)

    # The gate above ran outside any lock and may have taken minutes. Re-read
    # under `plan-<slug>` before writing, so the verdict lands on the document
    # as it is now rather than as it was before the checks started.
    _set_unit_status(layout, slug, [unit], args.status)
    if args.status == "done":
        state.update(layout, unit=None)
        state.clear_attempts(layout, unit.name)
    else:
        state.update(layout, level="2", plan=slug, unit=unit.name)
    journal.append(layout, config, "unit", unit.name, note)

    _echo(f"{unit.name}: {args.status}")
    if args.status == "running":
        _echo(f"file  {layout.rel(unit.path)}")
        if unit.owns:
            _echo(f"owns  {', '.join(unit.owns)}")
        if unit.forbid:
            _echo(f"never {', '.join(unit.forbid)}")
    remaining = plan_mod.next_wave(layout, slug)
    if remaining is None:
        _echo(f"plan {slug} is complete")
    return 0


def cmd_handoff(args):
    """A resume packet: everything a fresh session or another person needs."""
    layout, config = _loaded(args)
    current = state.load(layout)
    level = config_mod.normalise_level(current.get("level"))
    slug = current.get("plan")

    lines = [f"# Context — {args.name or 'handoff'}", "", "## Situation"]
    lines.append(
        f"Level L{level} ({config_mod.LEVEL_NAMES[level]}). "
        f"Spec: {current.get('spec') or 'none'}. Plan: {slug or 'none'}. "
        f"Active task/unit: {current.get('task') or current.get('unit') or 'none'}."
    )
    lines += ["", "## Established facts"]
    if slug:
        rows, problems = plan_mod.board(layout, slug)
        for level_no, name, tier, status, owns in rows:
            scope = f" owns {', '.join(owns)}" if owns else ""
            lines.append(f"- wave {level_no} `{name}` ({tier}) — {status}{scope}")
        for problem in problems:
            lines.append(f"- PLAN PROBLEM: {problem}")
    touched = journal.recent_paths(layout, 8)
    lines += [f"- touched `{path}`" for path in touched] or ["- no file changes recorded"]

    lines += ["", "## Decisions made", "_see .ctx/decisions/_", "", "## Open questions"]
    if current.get("spec"):
        _ready, blocking = spec_mod.ready(layout, current["spec"])
        lines += [f"- [ ] {item}" for item in blocking] or ["_none blocking_"]

    lines += ["", "## Constraints", "", "## Artifacts"]
    if slug:
        lines.append(f"- plan: `{layout.rel(plan_mod.readme_path(layout, slug))}`")
    lines.append(f"- journal digest: `{layout.rel(layout.digest)}`")

    lines += ["", "## Resume here"]
    if slug:
        wave = plan_mod.next_wave(layout, slug)
        lines.append(
            f"_run `/ctx:start` to dispatch wave {wave}_" if wave
            else "_plan is complete_"
        )
    else:
        lines.append("_run /ctx:resume_")

    path = bundle.save(
        layout, args.name or "handoff", "\n".join(lines),
        project=layout.root.parent.name, config=config,
    )
    journal.append(layout, config, "handoff", layout.rel(path), "")
    _echo(f"wrote {layout.rel(path)}")
    _echo("mechanical only — add what the state cannot show: why, and what to avoid")
    return 0


# --------------------------------------------------------------------------- #
# phase 7 — hardening
# --------------------------------------------------------------------------- #

def _next_action(layout, config):
    """(command, why). The one thing worth doing, from state alone.

    Everything this reads was already on disk and already decidable; it was just
    spread across `status`, `ask`, `plan-check` and `start`, so using the tool
    meant knowing which level you were at and which command that level implied.
    """
    current = state.load(layout)
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
                dispatched, waiting = _wave_in_flight(layout, plan, wave)
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


def cmd_next(args):
    """Name the single most useful next action, and why."""
    layout, config = _loaded(args)
    command, why = _next_action(layout, config)
    _echo(f"next: {command}")
    _echo(f"      {why}")
    return 0


def cmd_escalate(args):
    """L1 -> L2, carrying the task file's objective and criteria into a spec.

    Escalation used to mean abandoning the task and starting a spec from scratch,
    at exactly the moment — work turning out larger than expected — when throwing
    away the objective and criteria already written costs the most.
    """
    layout, config = _loaded(args)
    current = state.load(layout)
    slug = args.name or current.get("task")
    if not slug:
        return _needs(
            "task name",
            "Nothing is active at L1. Start one with /ctx:task, or name the task",
            "to escalate: ctx escalate «task-name»",
        )
    slug = bundle.slugify(slug)
    doc = frontmatter.read(layout.task_file(slug))
    if doc is None:
        _echo(f"no task file at {layout.rel(layout.task_file(slug))}")
        return 1

    objective = doc.section("objective", "goal").strip()
    criteria = doc.list_items("acceptance criteria", "criteria")
    spec_slug = bundle.slugify(args.spec or slug)
    path, qpath = spec_mod.create(layout, spec_slug, objective, config.get("verify"))

    # Only seed a spec we just created; never overwrite one already being worked.
    spec_doc = frontmatter.read(path)
    if criteria and "<checkable" in spec_doc.body:
        spec_doc.body = spec_doc.body.replace(
            "1. <checkable — name the observable, not the implementation>",
            "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1)),
        )
        spec_doc.meta["escalated_from"] = f"tasks/{slug}.md"
        spec_doc.write(path)

    doc.meta["status"] = "escalated"
    doc.meta["escalated_to"] = f"specs/{spec_slug}/spec.md"
    doc.write(layout.task_file(slug))

    state.update(layout, level="2", spec=spec_slug, task=None)
    journal.append(layout, config, "level", "L2", f"escalated {slug} -> {spec_slug}")
    _echo(f"L2 planned · spec {spec_slug} (from task {slug})")
    _echo(f"spec      {layout.rel(path)}")
    _echo(f"questions {layout.rel(qpath)}")
    _echo(f"carried over: objective and {len(criteria)} criterion/criteria")
    _echo("The task file is kept and marked `escalated` — it is the record of why.")
    return 0


def _trust_write_lock(layout, config, declared):
    """Write the committed lockfile. Accepts nothing — this is a review artefact.

    Deliberately separate from `--yes`: accepting is a statement about this
    machine, locking is a statement about this repository, and one command doing
    both would make every `ctx trust --yes` quietly commit a file.
    """
    entries = trust_mod.lock_write(layout, [check for check, _s in declared])
    journal.append(layout, config, "trust", trust_mod.LOCK_FILENAME,
                   f"locked {len(entries)} command(s)")
    _echo(f"wrote {layout.rel(trust_mod.lock_path(layout))} with "
          f"{len(entries)} command(s):")
    for entry in entries:
        _echo(f"  {entry['id']}  {entry['run']}")
    _echo("")
    _echo("Commit it. The diff is the review — CI checks against it with "
          "`ctx trust --verify-lock`.")
    return 0


def _trust_verify_lock(layout, config, declared):
    """Check every declared command against the lockfile. Accepts nothing.

    This is what CI runs instead of `ctx trust --yes`. `--yes` on an ephemeral
    runner accepts whatever the branch under test declares, which makes the
    control vacuous exactly where it is most needed; this one can only pass on
    commands somebody already reviewed into the lockfile on the default branch.
    """
    missing, unused, problems = trust_mod.lock_verify(layout, config, declared)
    for problem in problems:
        _warn(f"ctx trust: {problem}")
    if missing:
        _warn(f"{len(missing)} command(s) not in "
              f"{layout.rel(trust_mod.lock_path(layout))}:")
        for check, source in missing:
            _warn(f"  {check.get('run')}")
            _warn(f"      from {source} · id {trust_mod.command_id(check)}")
        _warn("")
        _warn("Each is a command this ledger would run that nobody has reviewed. "
              "Run `ctx trust --lock` and commit the diff.")
    if missing or problems:
        return 1
    for entry in unused:
        _echo(f"  note {entry['id']} ({entry['run']}) is locked but no longer "
              "declared anywhere")
    _echo(f"all {len(declared)} declared command(s) are in "
          f"{layout.rel(trust_mod.lock_path(layout))}")
    return 0


def cmd_trust(args):
    """Review and accept the verify commands this machine will execute.

    `ctx.yaml` is committed and its commands run with `shell=True` from a hook,
    which never sees a permission prompt. Acceptance is machine-local, so a
    cloned ledger is ungated until someone here has looked at what it runs.
    """
    layout, config = _loaded(args)
    declared = trust_mod.declared(layout, config)
    if args.verify_lock:
        return _trust_verify_lock(layout, config, declared)
    if args.lock:
        return _trust_write_lock(layout, config, declared)
    accepted = trust_mod.load(layout)

    pending = [(c, s) for c, s in declared if not trust_mod.is_accepted(c, accepted)]
    if not declared:
        _echo("no verify commands declared anywhere in this ledger")
        return 0
    if not pending:
        _echo(f"all {len(declared)} verify command(s) already accepted on this machine")
        return 0

    _echo(f"{len(pending)} command(s) not yet accepted on this machine:")
    for check, source in pending:
        where = f"  (cwd {check['cwd']})" if check.get("cwd") else ""
        _echo(f"  {check.get('run')}{where}")
        _echo(f"      from {source}")
    if not args.yes:
        _echo("")
        _echo("These run with a shell, from a hook, without a permission prompt.")
        _echo("Read them, then accept with: ctx trust --yes")
        return 1

    added = trust_mod.accept(layout, [c for c, _s in pending])
    journal.append(layout, config, "trust", f"{len(added)} command(s)", "accepted")
    _echo(f"accepted {len(added)} command(s) — recorded in "
          f"{layout.rel(trust_mod.path_for(layout))}")
    return 0


def cmd_migrate(args):
    """Upgrade ledger files to the plugin's schema. `--check` never writes."""
    layout = _layout(args)
    changed, problems = migrate_mod.upgrade(layout, dry_run=args.check)

    for problem in problems:
        _echo(f"  ! {problem}")
    if not changed and not problems:
        _echo(f"ledger is at schema v{config_mod.SCHEMA} — nothing to migrate")
        return 0

    verb = "would migrate" if args.check else "migrated"
    for item, steps in changed:
        path = ", ".join(f"v{v}" for v in steps)
        _echo(f"  {verb} {layout.rel(item.path)} ({item.kind}) → {path}")
    if problems:
        return 1
    if args.check:
        _echo(f"{len(changed)} file(s) need migration — run `ctx migrate` to apply")
        return 1
    config = config_mod.load(layout)
    journal.append(layout, config, "migrate", f"v{config_mod.SCHEMA}",
                   f"{len(changed)} file(s)")
    _echo(f"{len(changed)} file(s) migrated to v{config_mod.SCHEMA}")
    return 0


def cmd_budget(args):
    """What the ledger costs: predicted per level, and what sessions actually paid."""
    layout, config = _loaded(args)
    current = state.load(layout)

    _echo("## briefing budget (predicted)")
    for level in config_mod.LEVELS:
        measured = briefing.measure(layout, config, dict(current, level=level))
        flag = "CUT " if measured["truncated"] else "ok  "
        _echo(f"  {flag} L{level} {measured['chars']}/{measured['cap']} chars "
              f"(~{measured['approx_tokens']} tok)"
              + (" — truncated" if measured["truncated"] else ""))

    rows = telemetry.summarise(layout)
    briefings = [r for r in rows if r["event"] == "SessionStart" and r["median_chars"]]
    _echo("")
    _echo("## briefing actually injected (measured)")
    if briefings:
        row = briefings[0]
        _echo(f"  {row['count']} session(s) recorded · "
              f"median {round(row['median_chars'])} chars "
              f"(~{round(row['median_chars'] / 3.6)} tok)")
    else:
        _echo("  no sessions recorded yet")
    _echo("  the plugin's own always-on cost is separate and larger:")
    _echo("  measure it with `claude plugin details ctx`")

    plan_slug = args.plan or current.get("plan")
    if plan_slug:
        grouped, problems = plan_mod.check(layout, plan_slug)
        cap = int((config.get("plan") or {}).get("wave_budget_tokens", 0) or 0)
        _echo("")
        _echo(f"## declared unit budgets — plan {plan_slug}")
        if problems:
            _echo("  plan has problems; budgets may be incomplete")
        for level in sorted(grouped):
            total = sum(u.budget for u in grouped[level])
            flag = "OVER" if cap and total > cap else "ok  "
            _echo(f"  {flag} wave {level}: {total:,} of {cap:,} tokens"
                  if cap else f"  wave {level}: {total:,} tokens (no cap set)")
    return 0


def _record_spend(layout, config, args):
    """`ctx telemetry --spend «tokens» --unit «name»`.

    Spend arrives by report, not by observation: this process cannot see what
    a model was billed, so the only honest source is the session that ran the
    unit. That is why this is a command and not a measurement, and why every
    display of the result is labelled partial.

    Exits 0 even when the record could not be written. Telemetry never breaks
    a session — that guarantee is older than this command and this command
    does not get to be the exception — so a runtime directory that cannot be
    written costs a data point and says so on stderr, rather than failing a
    wave that already happened.
    """
    name = (args.unit or "").strip()
    if not name:
        _warn("--spend needs --unit «name»: a token count attached to nothing "
              "cannot be compared with anything, which is the only reason to "
              "record it")
        return 2
    try:
        tokens = int(str(args.spend).replace(",", "").replace("_", "").strip())
    except ValueError:
        _warn(f"--spend {args.spend!r} is not a number of tokens")
        return 2
    if tokens < 0:
        _warn("--spend cannot be negative — a negative spend is not a smaller "
              "measurement, it is an absent one")
        return 2
    if not telemetry.enabled(config):
        _echo("telemetry is off (telemetry.enabled: false in ctx.yaml) — "
              "nothing recorded")
        return 0
    if not telemetry.record_spend(layout, name, tokens):
        _warn("spend not recorded — .ctx/runtime/ could not be written. This is "
              "machine-local measurement and never fails a run, so the number "
              "is lost and nothing else is.")
        return 0
    _echo(f"recorded {tokens:,} tokens against {name}")
    for line in telemetry.SPEND_NOTICE:
        _echo(f"  {line}")
    return 0


def _print_reported_spend(layout, config, args):
    """Reported spend beside the predicted complexity score, unit by unit.

    The pairing is the whole point. A complexity weight is currently checked
    only against the reasoning that produced it; putting what a unit was
    reported to cost next to what its score predicted is the first thing that
    can disagree with that reasoning. It is deliberately not a verdict — the
    sample is partial by construction, which is what `SPEND_NOTICE` says
    directly above every one of these tables.

    Absent and zero are rendered differently on purpose: "unreported" is not a
    small number, and a zero printed for a unit nobody reported would be read
    as one the next time somebody tunes the weights.
    """
    spend = telemetry.spend_by_unit(layout)
    plan_slug = args.plan or state.load(layout).get("plan")
    scored = {}
    if plan_slug:
        grouped, _problems = plan_mod.check(layout, plan_slug)
        for level in sorted(grouped):
            for unit in grouped[level]:
                scored[unit.name], _breakdown = complexity_mod.score(config, unit)
    if not scored and not spend:
        return

    _echo("")
    _echo("## reported spend vs predicted score"
          + (f" — plan {plan_slug}" if plan_slug else ""))
    for line in telemetry.SPEND_NOTICE:
        _echo(f"  {line}")
    if not plan_slug:
        _echo("  no active plan — predicted scores unavailable; name one with "
              "`ctx telemetry --plan «slug»`")
    _echo(f"  {'unit':<32}{'score':>7}{'reported tokens':>18}{'reports':>9}")
    for name in sorted(set(scored) | set(spend)):
        score = scored.get(name)
        entry = spend.get(name)
        score_text = f"{score:.1f}" if score is not None else "-"
        tokens_text = f"{entry['tokens']:,}" if entry else "unreported"
        reports_text = str(entry["reports"]) if entry else "-"
        _echo(f"  {name:<32}{score_text:>7}{tokens_text:>18}{reports_text:>9}")


def cmd_telemetry(args):
    layout, config = _loaded(args)
    if args.spend is not None:
        return _record_spend(layout, config, args)
    rows = telemetry.summarise(layout)
    if not rows:
        _echo("no telemetry recorded yet — hooks write it as they run")
        _print_reported_spend(layout, config, args)
        return 0
    _echo(f"{'event':<20}{'n':>5}{'median ms':>11}{'max ms':>9}{'median chars':>14}")
    for row in rows:
        chars = "" if row["median_chars"] is None else str(round(row["median_chars"]))
        _echo(f"{row['event']:<20}{row['count']:>5}{row['median_ms']:>11.1f}"
              f"{row['max_ms']:>9.1f}{chars:>14}")
        # `by_role` exists because an event's overall median hides the number
        # that actually drives model choice: "review took 400ms on average"
        # says nothing about whether the `reviewer` role in particular is
        # slow, or which role is actually spending the tokens. Indented under
        # its event rather than a separate table, since a role's numbers are
        # only meaningful next to the event they were spent on.
        for role in sorted(row["by_role"]):
            info = row["by_role"][role]
            _echo(f"    {role:<16}{info['count']:>5}{info['median_ms']:>11.1f}")
    slow = [r for r in rows if r["max_ms"] > 1000]
    if slow:
        _echo("")
        _echo("slow hooks (>1s worst case): " + ", ".join(r["event"] for r in slow))
        _echo("SessionStart and UserPromptSubmit sit in front of every turn — a slow")
        _echo("one is felt directly. Check hook-errors.log and the verify commands.")
    _print_reported_spend(layout, config, args)
    return 0


def cmd_ci(args):
    """Everything checkable, headless, in one exit code. Written for pipelines."""
    layout, config = _loaded(args)
    current = state.load(layout)
    failures = []

    def report(name, ok, detail=""):
        # Detail explains a failure. Printed next to `ok` it reads as advice to
        # act on something that is fine.
        if ok:
            _echo(f"  ok   {name}")
        else:
            _echo(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            failures.append(name)

    _echo("## ledger")
    missing = [d for d in layout.dirs() if not d.is_dir()]
    report("layout complete", not missing,
           ", ".join(layout.rel(d) for d in missing))
    behind, ahead = migrate_mod.pending(layout)
    report("schema current", not behind and not ahead,
           f"{len(behind)} behind, {len(ahead)} ahead — run `ctx migrate`")

    _echo("## budgets")
    for level in config_mod.LEVELS:
        measured = briefing.measure(layout, config, dict(current, level=level))
        report(f"L{level} briefing fits without truncation",
               not measured["truncated"],
               f"{measured['chars']}/{measured['cap']} chars — shorten the objective "
               "and criteria on disk rather than raising the cap")

    _echo("## verify commands")
    entries = [e for e in (config.get("verify") or []) if isinstance(e, dict)]
    for entry in entries:
        if entry.get("kind") != "cmd":
            continue
        command = str(entry.get("run") or "")
        available, why = _availability(command)
        report(f"available: {command}", available, why)
    if not entries:
        _echo("  none configured")

    _echo("## command trust")
    declared = trust_mod.declared(layout, config)
    accepted = trust_mod.load(layout)
    pending = [c for c, _s in declared if not trust_mod.is_accepted(c, accepted)]
    if trust_mod.lock_path(layout).is_file():
        # A committed lockfile is the check that means something on an ephemeral
        # runner: the store is empty there, so machine acceptance could only ever
        # be satisfied by `ctx trust --yes`, which accepts whatever the branch
        # under test declares. Where a lockfile exists it is the gating check and
        # local acceptance becomes a note — it says nothing about CI either way.
        missing, _unused, problems = trust_mod.lock_verify(layout, config, declared)
        report(f"every verify command is in {trust_mod.LOCK_FILENAME}",
               not missing and not problems,
               "; ".join(problems) or f"{len(missing)} not locked")
        for check, source in missing[:4]:
            _echo(f"       {check.get('run')} — from {source}")
        if pending:
            _echo(f"  note {len(pending)} of these are not accepted on this machine "
                  "— that gates the local gate, not this pipeline")
    else:
        report("every verify command is accepted on this machine", not pending,
               f"{len(pending)} awaiting review — run `ctx trust`")

    _echo("## decisions")
    duplicates = duplicate_adrs(layout)
    report("every ADR id is claimed by exactly one file", not duplicates,
           "; ".join(f"{len(names)} files claim ADR {number:04d}: "
                     + ", ".join(names) for number, names in duplicates))

    advisory = _digest_advisory(layout)
    if advisory:
        # Reported, never failed. An existing project that upgrades ctx must not
        # find its pipeline red over a file that has been tracked since the day
        # it ran `ctx init`.
        _echo("## journal digest")
        for line in advisory:
            _echo(line)

    if current.get("spec"):
        _echo("## spec")
        ready, blocking = spec_mod.ready(layout, current["spec"])
        report(f"{current['spec']} has no open blocking questions", ready,
               f"{len(blocking)} unanswered")

    plans = args.plan or ([current["plan"]] if current.get("plan") else [])
    for slug in plans if isinstance(plans, list) else [plans]:
        _echo(f"## plan {slug}")
        _grouped, problems = plan_mod.check(layout, slug)
        report("graph is valid and collision-free", not problems,
               f"{len(problems)} problem(s)")
        for problem in problems:
            _echo(f"       {problem}")

    _echo("")
    if failures:
        _echo(f"{len(failures)} check(s) failed: " + ", ".join(failures))
        return 1
    _echo("all checks passed")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(prog="ctx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ctx {__version__}")
    parser.add_argument("--cwd", default=None, help="resolve the ledger from here")
    parser.add_argument("--strict", action="store_true",
                        help="escalate advisory conditions to exit 1")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold .ctx/ and propose verify commands")
    p.add_argument("--profile", choices=sorted(config_mod.PROFILES))
    p.add_argument("--verify-now", action="store_true",
                   help="run each proposed command and keep only those that pass")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--force", action="store_true", help="rewrite ctx.yaml")
    p.set_defaults(func=cmd_init)

    sub.add_parser("status", help="level, active work, budget, recent journal").set_defaults(func=cmd_status)
    sub.add_parser("briefing", help="print exactly what SessionStart would inject").set_defaults(func=cmd_briefing)
    sub.add_parser("resume", help="expanded state for on-demand recall").set_defaults(func=cmd_resume)
    sub.add_parser("digest", help="regenerate journal/DIGEST.md").set_defaults(func=cmd_digest)
    sub.add_parser("drop", help="return to L0 trace").set_defaults(func=cmd_drop)
    sub.add_parser("list", help="saved contexts, project and global").set_defaults(func=cmd_list)

    p = sub.add_parser("level", help="set the engagement level")
    p.add_argument("level", choices=list(config_mod.LEVELS))
    p.set_defaults(func=cmd_level)

    p = sub.add_parser("task", help="escalate to L1 with a single task file")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("rest", nargs="*", help="objective, as loose words")
    p.add_argument("--objective", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_task)

    p = sub.add_parser("save", help="write a portable context bundle")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("rest", nargs="*", help="more of the name, as loose words")
    p.add_argument("--stdin", action="store_true", help="read the bundle body from stdin")
    p.add_argument("--file", default=None)
    p.add_argument("--tag", action="append", default=[])
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("load", help="print a bundle: project, then global, then path")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("rest", nargs="*", help="more of the name, as loose words")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("promote", help="copy a bundle into the global store")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("rest", nargs="*", help="more of the name, as loose words")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("journal", help="append one entry")
    p.add_argument("kind")
    p.add_argument("target")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_journal)

    p = sub.add_parser("prune", help="fold old journal days into monthly archives")
    p.add_argument("--before", default=None, help="YYYY-MM-DD; defaults to journal.keep_days")
    p.add_argument("--discard", action="store_true", help="delete rather than archive")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("doctor", help="check layout, budgets, verify commands, gate")
    p.add_argument("--verify", action="store_true", help="actually run verify commands")
    p.add_argument("--clear", action="store_true",
                   help="delete the hook error log before checking")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("spec", help="escalate to L2 and scaffold a spec")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("rest", nargs="*", help="intent, as loose words")
    p.add_argument("--intent", default=None)
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("question", help="add questions to a spec")
    p.add_argument("name")
    p.add_argument("text", nargs="+")
    p.add_argument("--non-blocking", action="store_true")
    p.set_defaults(func=cmd_question)

    p = sub.add_parser("ask", help="list questions still open on a spec")
    p.add_argument("name", nargs="?", default=None)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("resolve", help="answer a question and record it")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--question", required=True, help="substring of the question")
    p.add_argument("--answer", required=True)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("spec-ready", help="Gate 1 as an exit code (0 = ready)")
    p.add_argument("name", nargs="?", default=None)
    p.set_defaults(func=cmd_spec_ready)

    p = sub.add_parser("decide", help="record an ADR")
    p.add_argument("title", nargs="*", help="the decision, as loose words")
    p.add_argument("--context", default=None)
    p.add_argument("--decision", default=None)
    p.add_argument("--consequences", default=None)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("verify", help="run the done-gate for the active work")
    p.add_argument("--sign-off", choices=list(verify.JUDGED), default=None,
                   help="record a judged check as passed")
    p.add_argument("--note", default=None)
    p.add_argument("--plan", default=None,
                   help="verify every unit in a plan headlessly (for CI)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("plan", help="scaffold a plan (refuses if the spec is ambiguous)")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--spec", default=None)
    p.add_argument("--unit", action="append", default=[])
    p.add_argument("--no-spec", action="store_true", help="plan without a spec")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("plan-unit", help="scaffold one unit file")
    p.add_argument("name")
    p.add_argument("--plan", default=None)
    p.add_argument("--objective", default=None)
    p.add_argument("--tier", choices=list(plan_mod.TIERS), default="subagent")
    p.add_argument("--owns", action="append", default=[])
    p.set_defaults(func=cmd_plan_unit)

    p = sub.add_parser("plan-check", help="compute waves and check for collisions")
    p.add_argument("name", nargs="?", default=None)
    p.set_defaults(func=cmd_plan_check)

    p = sub.add_parser("start", help="dispatch brief for the next (or given) wave")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--wave", type=int, default=None)
    p.add_argument("--worktree", action="store_true",
                   help="isolate each session-tier unit in its own temporary "
                        "worktree; off by default, because a worktree holds its "
                        "branch exclusively and the main tree can no longer "
                        "check it out")
    p.add_argument("--rebaseline", action="append", default=[], metavar="UNIT",
                   help="re-capture this unit's review baseline over the current "
                        "tree, replacing what dispatch recorded. Repeatable. "
                        "Refuses if the contract itself changed — that is what "
                        "--reseal is for. For the real crash case; everything "
                        "else keeps the baseline it was dispatched with")
    p.add_argument("--reseal", action="append", default=[], metavar="UNIT",
                   help="accept this unit's contract as it now stands: re-record "
                        "the promise the done-gate holds it to, and retake the "
                        "baseline with it. Repeatable, journalled by name and by "
                        "changed field. A planning decision, never implied by "
                        "--rebaseline")
    # Accepted and ignored: this was the opt-out before worktrees became opt-in,
    # and it still reads correctly in older docs and scripts.
    p.add_argument("--no-worktree", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("snapshot", help="capture a content snapshot for a unit")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--phase", choices=["before", "after"], default="before")
    p.add_argument("--round", type=int, default=1)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("review", help="build the review package for a unit")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--round", type=int, default=None)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("findings", help="list or update a unit's review findings")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--add", choices=list(findings_mod.SEVERITIES), default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--where", default=None)
    p.add_argument("--evidence", default=None)
    p.add_argument("--set", type=int, default=None, metavar="ID")
    p.add_argument("--status", choices=list(findings_mod.STATUSES), default=None)
    p.add_argument("--ruling", default=None)
    p.set_defaults(func=cmd_findings)

    p = sub.add_parser(
        "phase",
        help="advance or inspect a unit's phase gate (kind: bug, or a declared `phases:` list)",
    )
    p.add_argument("name", nargs="?", default=None, help="unit name")
    p.add_argument("phase", nargs="?", default=None,
                    help="phase to record; omit to list status instead")
    p.add_argument("--plan", default=None)
    p.add_argument("--command", default=None)
    p.add_argument("--exit-code", type=int, default=None)
    p.add_argument("--evidence", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("merge", help="land a unit's worktree branch after its gate passes")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--skip-gate", action="store_true",
                   help="merge without running the unit's verify checks")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("worktree", help="list or discard ctx worktrees")
    p.add_argument("action", choices=["list", "remove"])
    p.add_argument("name", nargs="?", default=None)
    # Spelled as `ctx merge --plan` is. Without it the ambiguity refusal that
    # `worktree.remove` raises — "pass --plan to say which one to discard" —
    # named a flag the subparser did not define, so the one message that told
    # the user what to do could not be acted on.
    p.add_argument("--plan", default=None,
                   help="which plan's worktree to discard, when two share a name")
    p.add_argument("--force", action="store_true",
                   help="discard uncommitted work in the worktree")
    p.set_defaults(func=cmd_worktree)

    sub.add_parser("next", help="the single most useful next action, from state").set_defaults(func=cmd_next)

    p = sub.add_parser("escalate", help="L1 to L2, carrying the task into a spec")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--spec", default=None, help="name the spec differently")
    p.set_defaults(func=cmd_escalate)

    p = sub.add_parser("trust", help="review and accept the verify commands to run")
    p.add_argument("--yes", action="store_true", help="accept the listed commands")
    p.add_argument("--lock", action="store_true",
                   help="write .ctx/trust.lock from the declared commands, to commit")
    p.add_argument("--verify-lock", dest="verify_lock", action="store_true",
                   help="check the ledger against .ctx/trust.lock; accepts nothing")
    p.set_defaults(func=cmd_trust)

    p = sub.add_parser("migrate", help="upgrade ledger files to this plugin's schema")
    p.add_argument("--check", action="store_true",
                   help="report what needs migrating and exit 1; writes nothing")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("budget", help="predicted and measured context cost")
    p.add_argument("--plan", default=None)
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("telemetry",
                       help="hook durations, briefing sizes, reported spend")
    # Spend is reported, never observed: this process cannot see a model's
    # token usage, so the orchestrator that ran the wave hands the number over
    # afterwards. Kept as a flag on `telemetry` rather than its own command
    # because it writes to, and is read back out of, exactly the same file.
    p.add_argument("--spend", default=None, metavar="TOKENS",
                   help="record what --unit actually cost, as reported by its "
                        "runner (partial data by construction)")
    p.add_argument("--unit", default=None, metavar="NAME",
                   help="the unit --spend refers to")
    p.add_argument("--plan", default=None,
                   help="pair reported spend with this plan's predicted scores")
    p.set_defaults(func=cmd_telemetry)

    p = sub.add_parser("ci", help="every headless check in one exit code")
    p.add_argument("--plan", action="append", default=[])
    p.set_defaults(func=cmd_ci)

    p = sub.add_parser("unit", help="focus a unit, or record its outcome")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--status", choices=list(plan_mod.STATUSES), default="running")
    p.add_argument("--force", action="store_true",
                   help="mark done even though the unit's gate did not pass")
    p.set_defaults(func=cmd_unit)

    p = sub.add_parser("handoff", help="write a resume packet for a session or person")
    p.add_argument("name", nargs="?", default=None)
    p.set_defaults(func=cmd_handoff)

    # `--strict` belongs to every command, not to a list of them, because the
    # caller who needs it is a script and it must not have to remember which
    # subcommands accept it. SUPPRESS keeps the subparser from writing its own
    # default over a `--strict` that was given before the subcommand name.
    for subparser in sub.choices.values():
        subparser.add_argument("--strict", action="store_true",
                               default=argparse.SUPPRESS,
                               help="escalate advisory conditions to exit 1")

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
    try:
        code = args.func(args)
    except SystemExit as exc:
        # How a command refuses: `raise SystemExit("why")`. It used to reach
        # the shell as exit 0 with the reason on stdout for all but seven
        # commands, which left a script unable to tell a refusal from a result.
        message = str(exc)
        if message and not message.isdigit():
            _warn(message)
            return 2
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
        _warn(f"ctx {args.command} failed: {type(exc).__name__}: {exc}")
        return 2
    if _ADVISED and _strict(args) and code in (0, None):
        # Advisory, and the caller asked to hear about it. The notice itself is
        # already on stdout; this names the condition so a log says which one.
        _warn("strict: " + ", ".join(sorted(set(_ADVISED)))
              + " — advisory, escalated by --strict/CTX_STRICT")
        return 1
    return code
