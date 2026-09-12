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
    for path in layout.unit_files():
        doc = frontmatter.read(path)
        if doc:
            collect(doc.meta.get("verify"), layout.rel(path))
    return found


# --------------------------------------------------------------------------- #
# the committed lockfile
# --------------------------------------------------------------------------- #
#
# The store above is machine-local by design, and that design has a hole the
# size of CI: every ephemeral runner starts with an empty store, so the only way
# `ctx ci` ever passed there was `ctx trust --yes`, which accepts whatever the
# repository's own PR-editable `ctx.yaml` happens to declare. That is not a
# review; it is a rubber stamp with a hostname on it.
#
# The lockfile is the other half. It is committed, so adding a command to it is
# a diff a human approves in the pull request that adds the command — the review
# happens where reviews already happen. It grants nothing on a developer's
# machine: `load`/`is_accepted` do not read it, so the local boundary is exactly
# what it was. What it does is let CI answer one question without accepting
# anything: *is every command this ledger will run one that was reviewed?*

LOCK_FILENAME = "trust.lock"
LOCK_SCHEMA = 1


def lock_path(layout):
    """The committed record of reviewed commands, inside `.ctx/`.

    In the repository, unlike the trust store, and for the opposite reason: this
    file is not a claim about what a machine trusts, it is the artefact under
    review. Its authority comes from the pull request that added a line to it.
    """
    return layout.root / LOCK_FILENAME


def lock_entry(check):
    """One command as it appears in the lockfile.

    `id` is `command_id`, unchanged — one identity scheme for a command, or the
    lockfile stops matching the store for reasons nobody can see. `run`, `cwd`
    and `env` are carried alongside it so the diff is readable: an id on its own
    is reviewable only by someone willing to recompute a hash.
    """
    env = check.get("env")
    return {
        "id": command_id(check),
        "run": str(check.get("run") or ""),
        "cwd": str(check.get("cwd") or ""),
        "env": {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
    }


def lock_render(checks):
    """The lockfile's exact bytes for a set of commands.

    Deterministic on purpose, and the determinism is a feature, not tidiness: a
    file that reorders itself between runs is a file reviewers learn to skim,
    and a lockfile nobody reads is the rubber stamp again. Sorted by id, which
    is hex and so orders the same under every locale; JSON with sorted keys; no
    timestamp, no hostname, no absolute path, and `\\n` endings written
    explicitly so a Windows checkout does not produce a whole-file diff.
    """
    entries = {}
    for check in checks or []:
        if not isinstance(check, dict) or check.get("kind") != "cmd":
            continue
        entry = lock_entry(check)
        entries[entry["id"]] = entry
    payload = {
        "schema": LOCK_SCHEMA,
        "commands": [entries[key] for key in sorted(entries)],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def lock_write(layout, checks):
    """Write the lockfile. Returns the entries it now holds, in file order."""
    text = lock_render(checks)
    target = lock_path(layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(str(target), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return json.loads(text)["commands"]


def lock_load(layout):
    """`(entries by id, problem)` — `({}, None)` when there is no lockfile.

    A lockfile that exists and will not parse is a problem, never an empty one:
    "unreadable" must not be able to look like "nothing declared", which is the
    shape of failure that lets a tampered file pass.
    """
    path = lock_path(layout)
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"{LOCK_FILENAME} could not be read — {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("commands"), list):
        return {}, f"{LOCK_FILENAME} is not a lockfile — expected a `commands` list"
    schema = data.get("schema")
    if isinstance(schema, int) and schema > LOCK_SCHEMA:
        return {}, (f"{LOCK_FILENAME} declares schema {schema} but this plugin "
                    f"understands {LOCK_SCHEMA} — upgrade the plugin")
    entries = {}
    for item in data["commands"]:
        if isinstance(item, dict) and item.get("id"):
            entries[str(item["id"])] = item
    return entries, None


def lock_verify(layout, config, found=None):
    """Check the ledger against the lockfile without accepting anything.

    Returns `(missing, unused, problems)`. `missing` is `(check, source)` for
    every declared command whose id is not in the lockfile — a command added to
    `ctx.yaml` after the lock was written, or one whose `run` text was edited,
    which is the same thing to `command_id` and deliberately so. `unused` is
    lockfile entries nothing declares any more, which is information rather than
    a failure: a finished unit's command going away is normal.
    """
    entries, problem = lock_load(layout)
    problems = [problem] if problem else []
    if not lock_path(layout).is_file():
        problems.append(
            f"no {LOCK_FILENAME} in this ledger — write one with `ctx trust --lock` "
            "and commit it"
        )
    declarations = declared(layout, config) if found is None else found
    seen = set()
    missing = []
    for check, source in declarations:
        key = command_id(check)
        seen.add(key)
        if key not in entries:
            missing.append((check, source))
    unused = [entries[key] for key in sorted(entries) if key not in seen]
    return missing, unused, problems
