"""`python -m ctx …` — everything the slash commands and CI call.

Anything that can be decided without inference lives here rather than in a
prompt: status boards, collision checks, digests, scope checks and budget
measurement all cost zero tokens when they run as code.
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys

from . import (
    __version__, briefing, bundle, config as config_mod, frontmatter,
    journal, paths, spec as spec_mod, state, verify, work,
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


def _detect_profile(root):
    checks = (
        ("infra", ("main.tf", "terraform")),
        ("data", ("dbt_project.yml", "notebooks")),
        ("docs", ("mkdocs.yml", "docusaurus.config.js", "docs")),
        ("code", ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml")),
    )
    for profile, markers in checks:
        for marker in markers:
            if (root / marker).exists():
                return profile
    return "code"


def _verify_candidates(root, profile):
    """Commands we would propose. Availability is checked; passing is not."""
    out = []
    if profile == "code":
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            out.append("python -m pytest -q")
        if (root / "package.json").exists():
            text = (root / "package.json").read_text(encoding="utf-8", errors="replace")
            if '"typecheck"' in text:
                out.append("npm run typecheck")
            elif '"tsc"' in text or (root / "tsconfig.json").exists():
                out.append("npx tsc --noEmit")
            if '"test"' in text:
                out.append("npm test")
        if (root / "go.mod").exists():
            out.append("go build ./...")
        if (root / "Cargo.toml").exists():
            out.append("cargo check")
    elif profile == "infra":
        out.append("terraform validate")
    return out


def _runnable(command):
    binary = command.split()[0]
    return shutil.which(binary) is not None


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
    candidates = _verify_candidates(root, profile)
    accepted, rejected = [], []
    for command in candidates:
        if not _runnable(command):
            rejected.append((command, "binary not on PATH"))
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
            "auto_load": [],
            "redact": [],
            "verify": accepted or config_mod.PROFILES.get(profile, []),
        }
        layout.config.write_text(config_mod.render(settings), encoding="utf-8")

    (layout.root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    config = config_mod.load(layout)
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
        _echo("  no verify commands configured — add them to ctx.yaml before using L1/L2")
    _echo("L0 is active: work is journalled to disk, and the hook briefing costs")
    _echo("~30 tokens per session (cap 61). See `claude plugin details ctx` for the")
    _echo("plugin's own always-on footprint, which is separate and larger.")
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

    entries, earlier = journal.tail(layout, 8)
    _echo("")
    _echo("recent journal:")
    for entry in entries or ["  (none)"]:
        _echo(f"  {entry}")
    if earlier:
        _echo(f"  … {earlier} earlier entries")
    return 0


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
    slug = bundle.slugify(args.name)
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
    path = bundle.resolve(layout, args.name)
    if path is None:
        _echo(f"no context named {args.name!r} — /ctx:list to see what exists")
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def cmd_promote(args):
    layout, config = _loaded(args)
    target = bundle.promote(layout, args.name)
    if target is None:
        _echo(f"no context named {args.name!r}")
        return 1
    journal.append(layout, config, "promote", args.name, "to global store")
    _echo(f"promoted to {target}")
    return 0


def cmd_list(args):
    layout, _config = _loaded(args)
    rows = bundle.listing(layout)
    if not rows:
        _echo("no saved contexts — /ctx:save «name»")
        return 0
    width = max(len(name) for _s, name, _p, _sum in rows)
    for scope, name, _path, summary in rows:
        _echo(f"{scope:<8} {name:<{width}}  {summary}")
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


def cmd_doctor(args):
    layout, config = _loaded(args)
    problems = 0

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
        over = measured["chars"] > measured["cap"]
        problems += 1 if over else 0
        _echo(
            f"  {'OVER' if over else 'ok  '} L{level} "
            f"{measured['chars']}/{measured['cap']} chars "
            f"(~{measured['approx_tokens']} tokens)"
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
        if not _runnable(command):
            _echo(f"  MISS {command} — binary not on PATH")
            problems += 1
        elif args.verify:
            code, output = _run(command, layout.root.parent, args.timeout)
            tail = output.strip().splitlines()[-1:] or [""]
            _echo(f"  {'ok  ' if code == 0 else 'FAIL'} {command} (exit {code}) {tail[0][:80]}")
            problems += 0 if code == 0 else 1
        else:
            _echo(f"  ok   {command} (available; pass --verify to run it)")

    _echo("## plugin footprint")
    _echo("  the briefing above is the hook cost only; the plugin's own always-on")
    _echo("  context is separate — measure it with: claude plugin details ctx")

    _echo("## gate")
    disabled = os.environ.get("CTX_GATE", "").lower() in ("off", "0", "false")
    _echo(f"  enabled={bool((config.get('gate') or {}).get('enabled')) and not disabled}"
          f"  max_attempts={(config.get('gate') or {}).get('max_attempts')}"
          + ("  (CTX_GATE=off in this environment)" if disabled else ""))

    if layout.errors.is_file():
        lines = layout.errors.read_text(encoding="utf-8", errors="replace").splitlines()
        _echo(f"## hook errors ({len(lines)} lines in {layout.rel(layout.errors)})")
        for line in lines[-6:]:
            _echo(f"  {line}")
        problems += 1

    _echo("")
    _echo(f"{problems} problem(s)" if problems else "all checks passed")
    return 1 if problems else 0


# --------------------------------------------------------------------------- #
# phase 3 — the ambiguity gate
# --------------------------------------------------------------------------- #

def cmd_spec(args):
    """Escalate to L2 and scaffold the spec. Gate 1 lives in its questions file."""
    layout, config = _loaded(args)
    slug = bundle.slugify(args.name)
    path, qpath = spec_mod.create(layout, slug, args.intent or "", config.get("verify"))
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
    slug = bundle.slugify(args.name or state.load(layout).get("spec") or "")
    if not slug:
        _echo("no active spec — /ctx:spec «intent» first")
        return 1
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
    slug = bundle.slugify(args.name or state.load(layout).get("spec") or "")
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
    slug = bundle.slugify(args.name or state.load(layout).get("spec") or "")
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
    slug = bundle.slugify(args.title)
    path = spec_mod.write_decision(
        layout, args.title, slug, args.context or "", args.decision or "",
        args.consequences or "",
    )
    journal.append(layout, config, "decide", layout.rel(path), args.title[:60])
    _echo(f"wrote {layout.rel(path)}")
    return 0


# --------------------------------------------------------------------------- #
# phase 4 — the done-gate
# --------------------------------------------------------------------------- #

def cmd_verify(args):
    """Run the gate by hand. Same code path the Stop hook uses."""
    layout, config = _loaded(args)
    item = work.active(layout)
    if item is None:
        _echo("nothing active to verify — /ctx:task or /ctx:spec first")
        return 1

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
        state.clear_attempts(layout, item.key)
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
    p.add_argument("name")
    p.add_argument("--objective", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_task)

    p = sub.add_parser("save", help="write a portable context bundle")
    p.add_argument("name")
    p.add_argument("--stdin", action="store_true", help="read the bundle body from stdin")
    p.add_argument("--file", default=None)
    p.add_argument("--tag", action="append", default=[])
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("load", help="print a bundle: project, then global, then path")
    p.add_argument("name")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("promote", help="copy a bundle into the global store")
    p.add_argument("name")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("journal", help="append one entry")
    p.add_argument("kind")
    p.add_argument("target")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_journal)

    p = sub.add_parser("doctor", help="check layout, budgets, verify commands, gate")
    p.add_argument("--verify", action="store_true", help="actually run verify commands")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("spec", help="escalate to L2 and scaffold a spec")
    p.add_argument("name")
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
    p.add_argument("title")
    p.add_argument("--context", default=None)
    p.add_argument("--decision", default=None)
    p.add_argument("--consequences", default=None)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("verify", help="run the done-gate for the active work")
    p.add_argument("--sign-off", choices=list(verify.JUDGED), default=None,
                   help="record a judged check as passed")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        message = str(exc)
        if message and not message.isdigit():
            print(message, file=sys.stderr)
            return 2
        raise
