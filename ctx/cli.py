"""`python -m ctx …` — everything the slash commands and CI call.

Anything that can be decided without inference lives here rather than in a
prompt: status boards, collision checks, digests, scope checks and budget
measurement all cost zero tokens when they run as code.
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

from . import (
    __version__, briefing, bundle, config as config_mod, dispatch, frontmatter,
    journal, migrate as migrate_mod, paths, plan as plan_mod, spec as spec_mod,
    state, telemetry, trust as trust_mod, verify, work, worktree,
)

GITIGNORE = "runtime/\n"

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
    print(*parts)


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


def _needs(what, *hints):
    """No name was given. Say what is missing and let the prompt body ask.

    Exiting 0 is deliberate: a non-zero exit makes Claude Code abort the whole
    slash command, so the user sees an argparse dump instead of a question.
    """
    _echo(f"no {what} given.")
    for hint in hints:
        _echo(hint)
    return 0


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


def _availability(command):
    """(can_it_start, why_not). The reason is user-facing, so it must be true.

    PATH alone is not enough for the interpreter forms: `python3 -m pytest` with
    pytest absent exits 1 from an interpreter that is very much present, so a
    PATH check accepts a command the gate can never actually run — and reports
    the wrong reason when it does reject one.
    """
    parts = command.split()
    if not parts:
        return False, "empty command"
    if shutil.which(parts[0]) is None:
        return False, f"{parts[0]} is not on PATH"
    if os.path.basename(parts[0]).startswith("python") and "-m" in parts[:3]:
        module = parts[parts.index("-m") + 1:parts.index("-m") + 2]
        if module:
            probe = (
                "import importlib.util, sys; "
                f"sys.exit(0 if importlib.util.find_spec({module[0]!r}) else 1)"
            )
            try:
                ok = subprocess.run(
                    [parts[0], "-c", probe], capture_output=True, timeout=20
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
    accepted, rejected = [], []
    for command in candidates:
        available, why = _availability(command)
        if not available:
            rejected.append((command, why))
            continue
        if args.verify_now:
            code, output = _run(command, root, args.timeout)
            if code != 0:
                rejected.append((command, f"exit {code} on a clean tree"))
                continue
        accepted.append({"kind": "cmd", "run": command})

    if not layout.config.exists() or args.force:
        settings = {
            "schema": config_mod.SCHEMA,
            "profile": profile,
            "level": "0",
            "briefing_chars": dict(config_mod.DEFAULTS["briefing_chars"]),
            "journal": dict(config_mod.DEFAULTS["journal"]),
            "gate": dict(config_mod.DEFAULTS["gate"]),
            "plan": dict(config_mod.DEFAULTS["plan"]),
            "telemetry": dict(config_mod.DEFAULTS["telemetry"]),
            "auto_load": [],
            "redact": [],
            "verify_candidates": list(existing.get("verify_candidates") or []),
            "verify": accepted or config_mod.PROFILES.get(profile, []),
        }
        layout.config.write_text(config_mod.render(settings), encoding="utf-8")

    (layout.root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    config = config_mod.load(layout)
    # Accept what init configured. You watched these being proposed and printed,
    # which is the review `ctx trust` exists to force on a ledger that arrived
    # from somewhere else.
    trust_mod.accept(layout, config.get("verify") or [])
    journal.write_digest(layout, config)
    bundle.reindex(layout)
    if not layout.state.exists():
        state.save(layout, dict(state.EMPTY))

    _echo(f"{'initialised' if fresh else 'updated'} {layout.rel(layout.root)}  profile={profile}  level=L0")
    if accepted:
        for entry in accepted:
            suffix = "" if args.verify_now else "  (available; not yet run)"
            _echo(f"  verify  {entry['run']}{suffix}")
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
        for level, name, tier, status, owns in rows:
            if level != wave:
                wave, marker = level, ""
                _echo(f"  wave {level}")
            flag = "→" if name == current.get("unit") else " "
            _echo(f"   {flag} {name:<24} {tier:<9} {status}")
        if not rows:
            _echo("   (no units yet)")
        for problem in problems:
            _echo(f"   ! {problem}")
        if not problems:
            nxt = plan_mod.next_wave(layout, current["plan"])
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
        _echo("no bundle name given. Saved contexts:")
        _list_bundles(layout)
        _echo("Ask which one to load, then run: ctx load «name»")
        return 0
    path = bundle.resolve(layout, name)
    if path is None:
        _echo(f"no context named {name!r}. Saved contexts:")
        _list_bundles(layout)
        return 0
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def cmd_promote(args):
    layout, config = _loaded(args)
    name = _named(args)
    if not name:
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


def _list_units(layout, slug):
    units = plan_mod.load_units(layout, slug)
    if not units:
        _echo("  none yet — ctx plan-unit «NN-name»")
        return units
    for unit in units:
        _echo(f"  {unit.name:<24}{unit.status}")
    return units


def _list_bundles(layout):
    rows = bundle.listing(layout)
    if not rows:
        _echo("  none saved yet — /ctx:save «name»")
        return rows
    width = max(len(name) for _s, name, _p, _sum in rows)
    for scope, name, _path, summary in rows:
        _echo(f"  {scope:<8} {name:<{width}}  {summary}")
    return rows


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

    drifted = _verify_drift(layout, config)
    if drifted:
        _echo("## verify drift")
        _echo(f"  warn {len(drifted)} work file(s) carry a `verify` block that no "
              "longer matches ctx.yaml")
        for path in drifted[:4]:
            _echo(f"       {layout.rel(path)}")
        _echo("       that is expected for finished work; re-scaffold if it is not")

    _echo("## plugin footprint")
    _echo("  the briefing above is the hook cost only; the plugin's own always-on")
    _echo("  context is separate — measure it with: claude plugin details ctx")

    _echo("## gate")
    disabled = os.environ.get("CTX_GATE", "").lower() in ("off", "0", "false")
    _echo(f"  enabled={bool((config.get('gate') or {}).get('enabled')) and not disabled}"
          f"  max_attempts={(config.get('gate') or {}).get('max_attempts')}"
          + ("  (CTX_GATE=off in this environment)" if disabled else ""))

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
    slug = bundle.slugify(args.name)
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
        return 0
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
        return 0
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

def cmd_verify(args):
    """Run the gate by hand. Same code path the Stop hook uses."""
    layout, config = _loaded(args)

    if args.plan:
        return _verify_plan(layout, config, bundle.slugify(args.plan))

    item = work.active(layout)
    if item is None:
        _echo("nothing active to verify — /ctx:task or /ctx:spec first")
        return 0

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

    results, verdict = verify.run(
        layout, config, item.checks, cwd=layout.root.parent, key=item.key,
        owns=item.owns, recorded=item.recorded, judged=True,
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
    _echo("then run /ctx:plan-check to compute waves and check for collisions")
    return 0


def cmd_plan_unit(args):
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return 0
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
        return 0

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
            _echo(f"note: {', '.join(sessions)} will each get a git worktree on dispatch")
    _echo(f"wrote {layout.rel(path)}")
    return 0


def cmd_start(args):
    """Print the dispatch brief for a wave. Spawns nothing itself."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.name, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return 0

    level, units, problems, budget = dispatch.prepare(layout, config, slug, args.wave)
    if problems:
        # Nothing is dispatched, and the reason is the output. Exiting non-zero
        # would abort `/ctx:start` and throw that reason away.
        _echo(f"plan {slug}: not ready to dispatch — nothing was started")
        for problem in problems:
            _echo(f"  - {problem}")
        return 0
    if not units:
        _echo(f"wave {level}: nothing left to dispatch")
        return 0

    worktrees, wt_problems = ([], [])
    if not args.no_worktree:
        worktrees, wt_problems = dispatch.prepare_worktrees(layout, slug, units)
    for problem in wt_problems:
        _echo(f"  ! {problem}")
    if wt_problems:
        _echo("")

    journal.append(
        layout, config, "start", slug,
        f"wave {level}, {len(units)} unit(s), {len(worktrees)} worktree(s)",
    )
    _echo(dispatch.instructions(layout, slug, level, units, budget, worktrees))
    return 0


# --------------------------------------------------------------------------- #
# phase 6 — the worktree tier
# --------------------------------------------------------------------------- #

def cmd_merge(args):
    """Land a unit's worktree branch. Refuses past a failed gate or stray writes."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return 0
    if not args.name:
        _echo(f"no unit name given. Units in plan {slug}:")
        _list_units(layout, slug)
        _echo("Ask which unit to merge, then run: ctx merge «unit-name»")
        return 0

    ok, messages = worktree.merge(
        layout, config, slug, args.name, skip_gate=args.skip_gate
    )
    for message in messages:
        if message:
            _echo(f"  {message}")
    journal.append(
        layout, config, "merge", args.name, "ok" if ok else "refused"
    )
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

    failed, pending, passed = [], [], []
    for level in sorted(grouped):
        for unit in grouped[level]:
            # A unit with no usable checks never gets here: plan_mod.check()
            # rejects it as a validation problem above.
            results, verdict = verify.run(
                layout, config, unit.checks, cwd=layout.root.parent,
                key=f"ci-{unit.name}", owns=unit.owns, recorded=unit.recorded,
                judged=False,
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
            else:
                passed.append(unit.name)

    _echo("")
    _echo(f"{len(passed)} passed · {len(pending)} awaiting sign-off · {len(failed)} failed")
    journal.append(
        layout, config, "verify", slug,
        f"plan run: {len(passed)}p/{len(pending)}w/{len(failed)}f",
    )
    return 1 if failed else 0


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

    error = worktree.remove(layout, args.name, force=args.force)
    if error:
        _echo(f"could not remove: {error}")
        _echo("pass --force to discard uncommitted work in the worktree")
        return 1
    journal.append(layout, config, "worktree", args.name, "removed")
    _echo(f"removed worktree and branch for {args.name}")
    return 0


def _gate_before_done(layout, config, slug, unit):
    """Non-zero exit code when this unit has not earned `done`, else None.

    Only the worktree `merge` path used to verify before completing a unit. For
    `subagent` — the default tier, and the one the dispatch brief pushes hardest
    — `done` was whatever the orchestrator typed after reading a report the unit
    had written about itself. The strongest guarantee in the system did not cover
    its most common path.
    """
    if not verify.ordered(unit.checks):
        _echo(f"refusing to mark {unit.name} done — it has no usable verify checks")
        _echo("add a `verify` block to the unit file, or pass --force to override")
        return 1

    results, verdict = verify.run(
        layout, config, unit.checks, cwd=layout.root.parent,
        key=f"{slug}/{unit.name}", owns=unit.owns, recorded=unit.recorded,
        judged=False,
    )
    if verdict in (verify.FAIL, verify.PENDING):
        _echo(f"refusing to mark {unit.name} done — the gate did not pass")
        for result in results:
            _echo(result.line())
        _echo("")
        _echo("Fix the failing criterion, or pass --force if you are deliberately")
        _echo("overriding the gate — which is a decision worth saying out loud.")
        journal.append(layout, config, "unit", unit.name, f"done refused ({verdict})")
        return 1
    if verdict == verify.ERROR:
        _echo(f"warning: no check could run for {unit.name} — that is a ctx.yaml")
        _echo("problem, not a work failure, so this is not blocking")
    return None


def cmd_unit(args):
    """Focus a unit so the done-gate applies to it, or record its outcome."""
    layout, config = _loaded(args)
    slug = _active_slug(layout, args.plan, "plan")
    if not slug:
        _echo("no active plan — /ctx:plan «slug» first")
        return 0

    if not args.name:
        _echo(f"no unit name given. Units in plan {slug}:")
        _list_units(layout, slug)
        return 0

    unit = plan_mod.find_unit(layout, slug, args.name)
    if unit is None:
        _echo(f"no unit {args.name!r} in plan {slug}. Units in plan {slug}:")
        _list_units(layout, slug)
        return 1

    if args.status == "done" and not args.force:
        refusal = _gate_before_done(layout, config, slug, unit)
        if refusal:
            return refusal

    unit.set(status=args.status)
    if args.status == "done":
        state.update(layout, unit=None)
        state.clear_attempts(layout, unit.name)
    else:
        state.update(layout, level="2", plan=slug, unit=unit.name)
    journal.append(layout, config, "unit", unit.name, args.status)

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


def cmd_trust(args):
    """Review and accept the verify commands this machine will execute.

    `ctx.yaml` is committed and its commands run with `shell=True` from a hook,
    which never sees a permission prompt. Acceptance is machine-local, so a
    cloned ledger is ungated until someone here has looked at what it runs.
    """
    layout, config = _loaded(args)
    declared = trust_mod.declared(layout, config)
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


def cmd_telemetry(args):
    layout = _layout(args)
    rows = telemetry.summarise(layout)
    if not rows:
        _echo("no telemetry recorded yet — hooks write it as they run")
        return 0
    _echo(f"{'event':<20}{'n':>5}{'median ms':>11}{'max ms':>9}{'median chars':>14}")
    for row in rows:
        chars = "" if row["median_chars"] is None else str(round(row["median_chars"]))
        _echo(f"{row['event']:<20}{row['count']:>5}{row['median_ms']:>11.1f}"
              f"{row['max_ms']:>9.1f}{chars:>14}")
    slow = [r for r in rows if r["max_ms"] > 1000]
    if slow:
        _echo("")
        _echo("slow hooks (>1s worst case): " + ", ".join(r["event"] for r in slow))
        _echo("SessionStart and UserPromptSubmit sit in front of every turn — a slow")
        _echo("one is felt directly. Check hook-errors.log and the verify commands.")
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
    report("every verify command is accepted on this machine", not pending,
           f"{len(pending)} awaiting review — run `ctx trust`")

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
    p.add_argument("--no-worktree", action="store_true",
                   help="do not create worktrees for session-tier units")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("merge", help="land a unit's worktree branch after its gate passes")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--plan", default=None)
    p.add_argument("--skip-gate", action="store_true",
                   help="merge without running the unit's verify checks")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("worktree", help="list or discard ctx worktrees")
    p.add_argument("action", choices=["list", "remove"])
    p.add_argument("name", nargs="?", default=None)
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
    p.set_defaults(func=cmd_trust)

    p = sub.add_parser("migrate", help="upgrade ledger files to this plugin's schema")
    p.add_argument("--check", action="store_true",
                   help="report what needs migrating and exit 1; writes nothing")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("budget", help="predicted and measured context cost")
    p.add_argument("--plan", default=None)
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("telemetry", help="hook durations and injected briefing sizes")
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

    return parser


# Gates and CI entry points must still fail loudly on a missing ledger. Every
# other command is something a person typed, and a non-zero exit there makes
# Claude Code abort the slash command before the prompt body can explain itself.
HARD_FAIL = frozenset({"verify", "ci", "spec-ready", "plan-check", "doctor",
                       "migrate", "trust"})


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        message = str(exc)
        if message and not message.isdigit():
            if args.command in HARD_FAIL:
                print(message, file=sys.stderr)
                return 2
            print(message)
            return 0
        raise
