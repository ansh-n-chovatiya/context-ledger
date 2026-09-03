"""Which verify commands this machine has agreed to run.

`ctx.yaml` is committed, and `verify.cmd.run` is executed with `shell=True` by a
hook — and hooks do not go through the tool permission prompt. So cloning a
repository and escalating to L1 runs whatever that file says, with nothing asked
first.

Until now the only thing standing in the way was accidental: `state.json` lives
under gitignored `runtime/`, so a fresh clone starts at L0 and never reaches the
gate. That is a real mitigation, but nobody documented it and nobody chose it.

This makes the boundary explicit and machine-local. Acceptance is recorded **per
command**, not per config file, because a task or unit carries its own copied
`verify` block — trusting `ctx.yaml` would say nothing about what a unit file
actually runs. A command that has not been accepted is reported as a
configuration error rather than executed, and configuration errors warn and pass:
an unaccepted ledger is *ungated*, never *broken*. Same failure policy as a
missing binary.
"""

import hashlib
import json

FILENAME = "verify.trust"

REASON = (
    "this command has not been accepted on this machine — review it and run "
    "`ctx trust` to allow it"
)


def path_for(layout):
    return layout.runtime / FILENAME


def command_id(check):
    """A digest of exactly what would be executed: the command, where, and with
    what environment. Changing any of the three is a new command."""
    env = check.get("env")
    payload = json.dumps(
        {
            "run": str(check.get("run") or ""),
            "cwd": str(check.get("cwd") or ""),
            "env": {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
        },
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load(layout):
    """Accepted command ids mapped to the command they stand for."""
    try:
        recorded = json.loads(path_for(layout).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    accepted = recorded.get("accepted") if isinstance(recorded, dict) else None
    return accepted if isinstance(accepted, dict) else {}


def is_accepted(check, accepted):
    return command_id(check) in (accepted or {})


def accept(layout, checks):
    """Record these commands as accepted. Returns the ones newly added."""
    accepted = load(layout)
    added = []
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") != "cmd":
            continue
        key = command_id(check)
        if key not in accepted:
            accepted[key] = str(check.get("run") or "")
            added.append(check)
    layout.runtime.mkdir(parents=True, exist_ok=True)
    path_for(layout).write_text(
        json.dumps({"accepted": accepted}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return added


def declared(layout, config):
    """Every `cmd` check anywhere in the ledger: config, tasks and units.

    A unit's block is a snapshot taken when the file was written, so it can name
    a command `ctx.yaml` no longer does. Trust has to cover what will actually
    run, not what the project currently intends to run.
    """
    found, seen = [], set()

    def collect(checks, source):
        for check in checks or []:
            if not isinstance(check, dict) or check.get("kind") != "cmd":
                continue
            key = command_id(check)
            if key in seen:
                continue
            seen.add(key)
            found.append((check, source))

    collect(config.get("verify"), "ctx.yaml")

    from . import frontmatter  # local: trust is imported by verify, which it is not

    for path in sorted(layout.tasks.glob("*.md")) if layout.tasks.is_dir() else []:
        doc = frontmatter.read(path)
        if doc:
            collect(doc.meta.get("verify"), layout.rel(path))
    for path in sorted(layout.plans.glob("*/units/*.md")) if layout.plans.is_dir() else []:
        doc = frontmatter.read(path)
        if doc:
            collect(doc.meta.get("verify"), layout.rel(path))
    return found
