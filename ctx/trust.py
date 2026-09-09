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

The store lives outside the repository, under the global root. The first version
kept it at `.ctx/runtime/verify.trust` and relied on a `.gitignore` — which
`git add -f` defeats, so a hostile ledger could ship its own acceptance and the
review below would never be asked for. An in-repo record of what this machine
trusts is not a boundary; it is a suggestion the attacker also gets to write.

Upgrading therefore forgets existing acceptances by design. `ctx trust` shows
them again in one pass, and `ctx doctor` names any leftover in-repo store so the
silence is explained rather than mysterious.
"""

import hashlib
import json

from . import paths

LEGACY_FILENAME = "verify.trust"

REASON = (
    "this command has not been accepted on this machine — review it and run "
    "`ctx trust` to allow it"
)


def path_for(layout):
    """Where this machine records what it agreed to run — outside the repository.

    It used to live at `.ctx/runtime/verify.trust`, protected only by a one-line
    `.gitignore`. `git add -f` defeats a gitignore, so an attacker could commit
    the acceptance *alongside* the commands it accepted and the review this
    module exists to force would never be asked for. A record of what this
    machine trusts cannot travel with the thing that supplies the commands.

    Keyed by the project's absolute path so two checkouts of the same repository
    are trusted separately — which is the honest reading of "this machine agreed
    to run this here".
    """
    key = hashlib.sha256(
        str(layout.root.resolve().parent).encode("utf-8")
    ).hexdigest()[:16]
    return paths.global_root() / "trust" / (key + ".json")


def legacy_path_for(layout):
    """The pre-0.7 in-repo store. Never read — only reported, so a user who has
    one is told why their acceptances appear to have been forgotten."""
    return layout.runtime / LEGACY_FILENAME


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
    target = path_for(layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
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
