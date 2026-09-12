"""ctx.yaml — defaults, merge and the engagement-level budgets.

The character caps in `briefing_chars` are the whole anti-bloat mechanism, so
they live in config where they can be measured (`ctx doctor`) and asserted in
CI. Characters rather than tokens on purpose: no tokenizer dependency, and the
ratio (~3.6 chars/token for prose) is stable enough for a budget.
"""

import copy
import datetime
import os
import sys
from pathlib import Path

from . import log, miniyaml, paths

SCHEMA = 1

# Engagement levels. L0 is the floor: always on, writes to disk, injects almost
# nothing. Ceremony is opt-in upward, never automatic.
LEVELS = ("0", "1", "2")
LEVEL_NAMES = {"0": "trace", "1": "tracked", "2": "planned"}

DEFAULTS = {
    "schema": SCHEMA,
    "profile": "code",
    "level": "0",
    # Keyed `l0`/`l1`/`l2` rather than bare numbers: readable in ctx.yaml, and
    # it sidesteps the int-vs-string key ambiguity of hand-edited YAML.
    "briefing_chars": {"l0": 220, "l1": 900, "l2": 2600},
    "journal": {
        "digest_lines": 12, "max_line_chars": 200, "enabled": True,
        # Days of journal history `ctx prune` keeps before folding older days
        # into one archive per month. 0 — the shipped default — means **never
        # prune**: every day file is kept for ever.
        #
        # It stays 0 because `prune` rewrites committed files, and a default
        # that rewrites tracked history on a schedule nobody set is a worse
        # failure than a directory that grows. The growth is not silent: past
        # `journal.GROWTH_WARN_FILES` day files, `journal.overgrown()` says so
        # and names this setting. The reasoning is written out in full above
        # `journal.prune`.
        "keep_days": 0,
    },
    # Hook timings and briefing sizes, written to gitignored `.ctx/runtime/`.
    # Local-only, and switchable because a policy review will ask.
    "telemetry": {"enabled": True},
    "gate": {
        "enabled": True,
        "max_attempts": 3,
        "output_head": 40,
        "output_tail": 20,
        # Budget for the whole gate, not per command. Per-command it could not be
        # enforced: three commands at 240s each outlive the 300s Stop hook, and a
        # killed hook returns no decision, so the gate silently stopped applying.
        "timeout_seconds": 240,
    },
    "plan": {
        "wave_budget_tokens": 250000,
        # Two different failure modes need two different caps. A wave can be
        # affordable and still be too *wide*: twelve 5k units clear the token
        # budget above with room to spare, and still ask one orchestrating
        # session to hold twelve concurrent Task calls, twelve reports and
        # twelve review packages at once — which is the context flatness the
        # whole dispatch discipline exists to protect.
        #
        # 8 rather than something tighter because the cap has to be checked
        # against history, not taste: the widest wave this project has itself
        # dispatched is 4 units, and a shipped default that would have refused
        # the tool's own past waves is a default every user immediately edits
        # — at which point it has taught them to ignore it. 8 is twice that
        # high-water mark, so it refuses the runaway twenty-unit wave while
        # leaving normal practice untouched. 0 disables the check, exactly as
        # it does for `wave_budget_tokens`.
        "max_wave_units": 8,
    },
    # Which model each dispatched role runs on. Declared here rather than left to
    # the dispatching session's judgement: a Task call with no model named
    # inherits the caller's, which is the most capable and most expensive one
    # available — so a wave of eight one-line units silently books eight of the
    # priciest seats on the account. A unit whose work genuinely needs more can
    # say so with `model:` in its own frontmatter.
    "models": {
        "runner": "sonnet", "reviewer": "opus", "verifier": "opus",
        # Cheapest first. This is the one list `tier_up` and complexity-score
        # walk to escalate a unit past its default model — every other module
        # is expected to name a tier by looking it up here, never by spelling
        # a model name of its own, so a project that swaps in a house-hosted
        # cheap model or drops one of these three gets that choice honoured
        # everywhere at once instead of in whichever call site remembered to
        # check.
        "tiers": ["haiku", "sonnet", "opus"],
        # A round that failed on its assigned tier escalates to the next one
        # by default: the working assumption is that a failure is more likely
        # a capability gap than bad luck, and silently re-running the same
        # model is the more expensive way to find that out. A project that
        # trusts its own judged-verify prompts enough to prefer a flat retry
        # can turn this off here without touching the dispatcher.
        "escalate_on_failed_round": False,
    },
    # How `complexity-score` turns a unit's own frontmatter into a number, and
    # where that number crosses into a costlier dispatch tier. Every weight
    # below is a judgement call, not a measurement — there is no corpus yet of
    # units and how hard they actually were, so these are the roughest guess
    # that still orders units sensibly relative to each other: a bigger stated
    # budget, more owned paths, more inputs to read, more coordination
    # partners and a verify step a machine cannot decide all make a unit
    # harder, and a bug fix is presumed harder than a green-field feature
    # because the failure it is chasing is, by definition, one nobody expected.
    # Expect these numbers to move once real dispatch history exists to check
    # them against; until then they are a starting point, not a promise.
    "complexity": {
        "weights": {
            # One point per 15,000 tokens of the unit's own stated budget — the
            # unit author's own estimate of how much work this is, which is a
            # better signal than anything derivable from the plan file alone.
            "budget_per_15k": 1.0,
            # Half a point per path the unit owns. Owning is what risks a
            # collision with a sibling, so it counts on its own rather than
            # folding into `reads_per_2paths` below.
            "owns_per_path": 0.5,
            # Half a point per two paths *read*. Reading carries half the
            # weight owning does per path, so it is scored two-at-a-time
            # instead of halving the same constant twice.
            "reads_per_2paths": 0.5,
            # Half a point per entry in `depends_on`. Each dependency is a
            # point where this unit's result depends on another wave having
            # actually finished, which is a coordination cost even when the
            # work itself is small.
            "depends_on_each": 0.5,
            # Two flat points when any `verify` entry is `kind: rubric` or
            # `kind: human`. A command either passes or does not; a judgement
            # call can be argued with, so it is weighted like two extra units
            # of scope rather than a fraction of one.
            "judged_verify": 2.0,
            # Two flat points when the unit's `## Interfaces` section is
            # non-empty. A published interface is read by a sibling before
            # this unit is done, so getting it wrong costs someone else's work
            # too, not just this one's.
            "publishes_iface": 2.0,
            # Two flat points when the plan's own `kind` is `bug`. Chasing a
            # failure nobody predicted is presumed harder than building
            # something to a known spec, on no stronger evidence than that
            # being the reason bug units get written in the first place.
            "kind_bug": 2.0,
        },
        "thresholds": {"standard": 3.0, "deep": 6.0},
    },
    # Thresholds for the review package itself — measured in bytes read, not
    # complexity-score points, which is why this is its own top-level key
    # rather than a fourth entry under `complexity`: burying a byte count
    # inside a block whose other numbers are score points is how a future
    # reader misreads both. `small_package_bytes` is the cutoff `dispatch.
    # model_for` checks before it will pick a reviewer tier one cheaper than
    # `models.reviewer` — a package at or under it, with no scope
    # violations, is cheap and clean enough to read on the lighter seat.
    # 20,000 is a judgement call, not a measurement, exactly like the
    # complexity weights above: there is no dispatch history yet to weigh it
    # against, so expect it to move once telemetry gives us real package
    # sizes to check it against.
    "review": {"small_package_bytes": 20000},
    "auto_load": [],
    "redact": [],
    # Extra commands `ctx init` should consider, for a toolchain no marker table
    # can anticipate: a wrapper script, a bazel target, a house Makefile rule.
    "verify_candidates": [],
    "verify": [],
}

# Per-profile fallbacks, used only when no concrete command could be detected
# from the toolchain.
#
# `code` and `infra` get real commands from `_verify_candidates`, so their
# fallbacks carry the judgement a command cannot supply. The others lean on
# judgement because no command can decide whether prose is correct.
#
# Deliberately no `{"kind": "exists", "path": "."}` anywhere: the working
# directory always exists, so that check can never fail. A default that always
# passes is worse than no default — it makes an unguarded project look guarded.
PROFILES = {
    "code": [],
    "infra": [{"kind": "human", "about": "review the planned changes before applying"}],
    "docs": [{"kind": "rubric", "about": "the text satisfies the acceptance criteria"}],
    "research": [
        {"kind": "rubric", "about": "findings are sourced and answer the question asked"}
    ],
    "data": [{"kind": "human", "about": "sanity-check the output before relying on it"}],
}


# --------------------------------------------------------------------------- #
# the policy layer — system, then user, then the repository
# --------------------------------------------------------------------------- #
#
# `.ctx/ctx.yaml` is editable in a pull request, so every control it holds is a
# control the repository grants itself. That is right for budgets and profiles
# and wrong for anything an organisation needs to hold across repositories. So
# two layers sit *above* it, on the machine rather than in the tree, and a
# `locked:` list in either one names keys a later layer may not change.
#
# Absent both files, the layers are empty and this reduces exactly to what it
# always was: DEFAULTS under ctx.yaml.

POLICY_FILENAME = "policy.yaml"
LOCKED_KEY = "locked"

# Distinguishes "locked to no value at all" from "locked to None".
_MISSING = object()


def system_policy_path():
    """The machine-wide policy file, where only an administrator can write it.

    POSIX: `/etc/ctx/policy.yaml`. Windows: `%PROGRAMDATA%\\ctx\\policy.yaml`.
    Both are the platform's conventional home for machine-wide configuration,
    and both are root/Administrator-owned by default — which is the point. A
    control a repository cannot overrule is worth little if the account running
    the tool can rewrite it as easily as the repository could.
    """
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or "C:\\ProgramData"
        return Path(base) / "ctx" / POLICY_FILENAME
    return Path("/etc/ctx") / POLICY_FILENAME


def user_policy_path():
    """The per-account policy file, beside the trust store in the global root.

    `~/.claude/ctx/policy.yaml`, or `$CTX_GLOBAL_ROOT/policy.yaml` when that is
    set. It shares a root with `trust/` deliberately: both answer "what has this
    account agreed to", both must live outside the repository that supplies the
    commands, and one root is one thing to back up, audit or wipe.
    """
    return paths.global_root() / POLICY_FILENAME


def policy_layers():
    """`(source, path)` for every layer above the repository, weakest first."""
    layers = [("system", system_policy_path())]
    extra = os.environ.get("CTX_POLICY_SYSTEM")
    if extra:
        # Additive, never a replacement. A fleet tool (or a test) can stage a
        # second machine-wide file, but it is applied *after* the fixed one, so
        # it cannot unlock anything the fixed one locked. An environment
        # variable that could void a root-owned policy would be the same
        # unlogged off-switch this module exists to close.
        layers.append(("system-env", Path(extra).expanduser()))
    layers.append(("user", user_policy_path()))
    return layers


class Policy:
    """A resolved configuration, plus how each value got there.

    `config` is the merged mapping `load` returns. The rest is the audit trail:
    which layer supplied each live value, which keys are locked and by whom, and
    every attempt a later layer made to change a locked one.
    """

    def __init__(self):
        self.config = copy.deepcopy(DEFAULTS)
        self.layers = []      # (source, path, present)
        self.origin = {}      # dotted key -> source that supplied the live value
        self.locks = {}       # dotted key -> source that locked it
        self.refusals = []    # (source, dotted key, attempted, held, lock source)
        self.notes = []       # non-fatal complaints about a layer

    def source_of(self, dotted):
        """Which layer supplied the active value — `default` when nobody did."""
        holder, _key = _lock_holder(self.locks, dotted)
        return self.origin.get(dotted, "default") if holder is None else holder

    def locked_by(self, dotted):
        """The layer that locked this key (or a parent of it), or None."""
        return _lock_holder(self.locks, dotted)[0]

    def value_of(self, dotted, default=None):
        """The active value at a dotted key, or `default` when there is none."""
        found = _lookup(self.config, dotted)
        return default if found is _MISSING else found

    def present(self):
        return [(source, path) for source, path, found in self.layers if found]


def _flatten(data, prefix=""):
    """A mapping as `(dotted key, leaf value)` pairs.

    An empty mapping is skipped rather than emitted, matching `_merge`: `key: {}`
    in an override has always meant "nothing to say about key", and turning it
    into an assignment here would let an empty block wipe a populated one.
    """
    pairs = []
    for key, value in data.items():
        dotted = prefix + str(key)
        if isinstance(value, dict):
            if value:
                pairs.extend(_flatten(value, dotted + "."))
        else:
            pairs.append((dotted, value))
    return pairs


def _lookup(data, dotted):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _assign(data, dotted, value):
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _lock_holder(locks, dotted):
    """`(source, locked key)` for the most specific lock covering `dotted`.

    Prefixes count: `locked: [gate]` locks `gate.enabled` too. Longest match
    wins, so a broad lock and a narrow one can coexist and the narrow one is the
    one reported.
    """
    parts = dotted.split(".")
    for size in range(len(parts), 0, -1):
        key = ".".join(parts[:size])
        if key in locks:
            return locks[key], key
    return None, None


def _locked_keys(parsed):
    """What this layer refuses to let a later one change.

    Two spellings, because both are natural and neither is ambiguous:

        locked: [gate.enabled, gate.allow_override]   # values from the body
        locked: {gate.allow_override: false}          # sets *and* locks

    The mapping form exists so a value and its lock cannot drift apart, which is
    what happens when a policy has to state the same setting twice.
    """
    raw = parsed.get(LOCKED_KEY)
    if isinstance(raw, dict):
        return [(str(key), value) for key, value in raw.items()]
    if isinstance(raw, (list, tuple)):
        return [(str(key), _MISSING) for key in raw
                if not isinstance(key, (dict, list, tuple))]
    if isinstance(raw, str) and raw.strip():
        return [(raw.strip(), _MISSING)]
    return []


def _apply_layer(policy, source, parsed, allow_locks=True):
    """Merge one layer, refusing anything an earlier layer locked."""
    locked = _locked_keys(parsed) if allow_locks else []
    body = dict((key, value) for key, value in parsed.items() if key != LOCKED_KEY)
    pairs = _flatten(body)
    # A mapping-form lock carries its own value, so it is applied like a body
    # setting — and, being applied here, it is subject to any earlier lock too.
    pairs.extend((key, value) for key, value in locked if value is not _MISSING)

    for dotted, value in pairs:
        if value is None:
            # `_merge` has always treated an explicit null as "say nothing".
            continue
        holder, lock_key = _lock_holder(policy.locks, dotted)
        if holder is not None:
            held = _lookup(policy.config, dotted)
            if held is not _MISSING and held == value:
                continue  # restating the locked value is not an override
            policy.refusals.append(
                (source, dotted, value, None if held is _MISSING else held, holder)
            )
            continue
        _assign(policy.config, dotted, value)
        policy.origin[dotted] = source

    for dotted, _value in locked:
        # First lock wins the attribution: a user policy re-stating a system
        # lock has changed nothing, and the system is who to complain to.
        policy.locks.setdefault(dotted, source)


def _read_policy(path, source):
    """One policy file, or None when there is not one.

    A policy file that exists but cannot be read is fatal, deliberately. Every
    other read in this module fails soft, because the cost of a missing optional
    file is a smaller feature set. The cost of an unreadable *control plane* is
    a control silently not applied, which is the failure this whole layer exists
    to prevent — so it stops the command and names the file instead.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # A policy file that exists but cannot be read is indistinguishable
        # from one that does not exist — a control plane silently not applied,
        # which is precisely what this layer is here to prevent. It still
        # fails soft (an unreadable *optional* file must not stop every
        # command), but it no longer does so without saying anything.
        log.failure("config.policy", exc, source=source, path=path)
        return None
    try:
        parsed = miniyaml.loads(text) or {}
    except miniyaml.MiniYamlError as exc:
        # `from exc` rather than a bare re-raise: the parse error is the whole
        # explanation of why the policy could not be applied, and losing its
        # traceback would leave `CTX_LOG=debug` with nothing to print beyond
        # the one line already in the message.
        raise SystemExit(f"{path}: {source} policy is unreadable — {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{path}: {source} policy must be a mapping")
    return parsed


def _read_repo_config(path):
    parsed = miniyaml.loads(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise miniyaml.MiniYamlError("ctx.yaml must be a mapping")
    found = parsed.get("schema", SCHEMA)
    if isinstance(found, int) and found > SCHEMA:
        raise SystemExit(
            f"ctx.yaml declares schema {found} but this plugin understands {SCHEMA} "
            "— upgrade the plugin rather than downgrading the ledger"
        )
    return parsed


def resolve_policy(layout=None):
    """Resolve system → user → repo and record how every value got there.

    Pure apart from reading files: it reports, it does not warn. `load` does the
    warning, so `ctx doctor` can ask this the second time without printing the
    same complaint twice.
    """
    policy = Policy()
    for source, path in policy_layers():
        parsed = _read_policy(path, source)
        policy.layers.append((source, path, parsed is not None))
        if parsed:
            _apply_layer(policy, source, parsed)

    if layout is not None:
        path = layout.config
        parsed = _read_repo_config(path) if path.is_file() else None
        policy.layers.append(("repo", path, parsed is not None))
        if parsed is not None:
            if LOCKED_KEY in parsed:
                policy.notes.append(
                    "`locked:` in ctx.yaml is ignored — a repository cannot lock "
                    "its own settings; policy belongs in the user or system layer"
                )
            _apply_layer(policy, "repo", parsed, allow_locks=False)

    policy.config["level"] = normalise_level(policy.config.get("level"))
    return policy


def load(layout):
    """Config for a ledger: defaults, under policy, under the repo's own file.

    Precedence is system policy → user policy → `.ctx/ctx.yaml`, later winning,
    except where an earlier layer locked the key. With no policy files present
    the two upper layers contribute nothing and this is byte-for-byte the merge
    it always was.
    """
    policy = resolve_policy(layout)
    for refusal in policy.refusals:
        _warn_refusal(refusal)
    for note in policy.notes:
        _warn_policy(note)
    return policy.config


# Refusals and notes already reported this process, so a command that loads the
# config four times complains once. Same shape and same reasoning as
# `_WARNED_LEVELS` above.
_WARNED_POLICY = set()


def reset_policy_warnings():
    """Forget what has already been reported. For tests, which share a process."""
    _WARNED_POLICY.clear()


def _warn_policy(message):
    if message in _WARNED_POLICY:
        return
    _WARNED_POLICY.add(message)
    print(f"ctx: {message}", file=sys.stderr)


def _warn_refusal(refusal):
    """One stderr line per attempt to change a locked setting.

    Silence here would be the whole failure mode: a repository that sets
    `gate.enabled: false` under a lock would run gated and its author would
    spend an afternoon working out why their config "did not apply".
    """
    source, dotted, attempted, held, holder = refusal
    _warn_policy(
        f"{source} sets {dotted}={ascii(attempted)} but {holder} policy locks it "
        f"to {ascii(held)} — the locked value holds"
    )


# --------------------------------------------------------------------------- #
# the gate's escape hatch
# --------------------------------------------------------------------------- #

# Every spelling that turns the gate off. One tuple, because the CLI used to
# honour three of these and the Stop hook four, so `CTX_GATE=disabled` made
# `ctx doctor` report a gate that the hook had already switched off.
GATE_OFF_VALUES = ("off", "0", "false", "disabled")
_ALLOW_OFF_VALUES = ("false", "off", "no", "0", "disabled")


class GateOverride:
    """What `CTX_GATE` did at one site, and whether the record of it landed."""

    def __init__(self, requested=False, value="", disabled=False, refused=False,
                 lock=None, note="", recorded=None):
        self.requested = requested   # the variable asked for the gate to be off
        self.value = value           # what it was actually set to
        self.disabled = disabled     # the gate will not run here
        self.refused = refused       # policy said no; the gate runs anyway
        self.lock = lock             # which layer refused
        self.note = note             # the journalled sentence
        self.recorded = recorded     # True once the journal took it

    def __bool__(self):
        return self.disabled

    __nonzero__ = __bool__  # python 2 spelling, harmless and free


def gate_override(layout, config, site):
    """Whether `CTX_GATE` disables the gate here — and the record that it did.

    Every site that honours the variable calls this, so there is one definition
    of "off", one journal line per bypass, and one place a policy can refuse it.
    `site` names the caller (`stop`, `doctor`) and goes into the record, because
    "the gate was off" and "the gate was off *for the Stop hook*" are different
    facts to an auditor.

    A `gate.allow_override: false` that a policy has locked refuses the bypass
    outright: the gate runs, and the attempt is journalled either way.
    """
    raw = str(os.environ.get("CTX_GATE", "") or "")
    if raw.strip().lower() not in GATE_OFF_VALUES:
        return GateOverride()

    allowed = (config.get("gate") or {}).get("allow_override", True)
    if allowed is False or str(allowed).strip().lower() in _ALLOW_OFF_VALUES:
        holder = _override_lock_source(layout)
        override = GateOverride(
            requested=True, value=raw, disabled=False, refused=True, lock=holder,
            note=(f"{site}: CTX_GATE={raw} refused by {holder} policy "
                  "(gate.allow_override is off) — the gate ran"),
        )
    else:
        override = GateOverride(
            requested=True, value=raw, disabled=True,
            note=f"{site}: gate disabled by CTX_GATE={raw}",
        )
    return _record_override(layout, config, override)


def _override_lock_source(layout):
    """Which layer refused the bypass. Best effort — it decorates a message."""
    try:
        policy = resolve_policy(layout)
    except Exception:  # pragma: no cover - a policy file that stops loading
        return "policy"
    holder, _key = _lock_holder(policy.locks, "gate.allow_override")
    return holder or policy.origin.get("gate.allow_override", "policy")


def _record_override(layout, config, override):
    """Journal a bypass that has already been decided.

    The tension: an escape hatch nobody can audit is not defensible, and an
    escape hatch that can *fail a session* because a disk is full is worse than
    the thing it was protecting. So the decision is made above and passed in;
    nothing here can change it, and nothing here is allowed to raise. If the
    journal will not take the line, the bypass still happens — and says so on
    stderr and in `runtime/hook-errors.log`, which `ctx doctor` already reads.
    Unrecorded, never unnoticed.
    """
    from . import journal  # local: config is imported by nearly everything, and
                           # a module-level import here would order that graph

    line = None
    try:
        line = journal.append(layout, config, "gate", "CTX_GATE", override.note)
    except Exception as exc:
        log.failure("config.record_override", exc, note=override.note)
        line = None
    override.recorded = line is not None
    if line is None:
        _report_unrecorded(layout, override)
    return override


def _report_unrecorded(layout, override):
    """Say that a bypass went unrecorded, on both channels, without raising.

    `journal.append` returns None for two different reasons — the write failed,
    or journalling is switched off in config — and for this one event they are
    the same reason: the audit trail does not have it. Both get reported.
    """
    try:
        print(
            f"ctx: CTX_GATE override was not journalled ({override.note}) — the "
            "gate decision stands, but this bypass is unrecorded",
            file=sys.stderr,
        )
    except Exception:  # pragma: no cover - a console that cannot take the line
        pass
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with layout.errors.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {stamp} gate-override ---\n{override.note}\n")
    except Exception as exc:
        # Both channels for this one are now gone. Nothing else can be done
        # without failing the session the override exists to keep running.
        log.failure("config.report_unrecorded", exc, path=layout.errors)


# Offending level spellings already reported this process.
#
# `normalise_level` is called from `config.load`, `state.load` and five places
# in `cli`/`hooks`/`briefing`/`work`, but measured against a ctx.yaml holding
# `level: L3` the fall-down is reached exactly *once* per invocation of `ctx
# status`, `doctor`, `briefing` and `spec-ready` — `config.load` writes the
# coerced level back into the dict it returns, and `state.json` carries a
# level that has already been through here, so the later calls all see a
# recognised `"0"`. So this set is a guard rather than a fix for an observed
# flood: nothing stops a future command loading config twice, and one line is
# the answer either way. Keyed on the *value*, so a second, different bad
# spelling still gets its own line. A process is one `ctx` invocation, so the
# user is told every time they run a command, not just the first.
_WARNED_LEVELS = set()


def reset_level_warnings():
    """Forget what has already been reported. For tests, which share a process."""
    _WARNED_LEVELS.clear()


def _warn_level_fallback(value, level):
    """One line on stderr naming the value we could not read and where it landed.

    stderr, not stdout: `ctx briefing` and the hooks put machine-read output on
    stdout, and a warning mixed into that is a parse error rather than a
    message. `ascii()` rather than `repr()` so the line is single-line and
    ASCII-clean whatever the value was, and the line itself carries no
    non-ASCII punctuation either, so a `cp1252` console cannot raise on it - a
    `level:` with a newline or a
    non-encodable character in it must not be able to break the console it is
    being reported on.
    """
    try:
        shown = ascii(value)
    except Exception:  # pragma: no cover - a __repr__ that raises
        shown = "<unprintable>"
    if shown in _WARNED_LEVELS:
        return
    _WARNED_LEVELS.add(shown)
    name = LEVEL_NAMES.get(level, "")
    print(
        f"ctx: unrecognised level {shown} - falling back to L{level} ({name}); "
        f"expected one of {', '.join('L' + item for item in LEVELS)}",
        file=sys.stderr,
    )


def normalise_level(value):
    """A level spelling reduced to a member of `LEVELS`.

    Anything unrecognised falls *down* to `"0"`, deliberately: `briefing_cap`
    turns this into a spend limit, so an unparseable level must buy the
    smallest briefing, not the largest. What was missing was not the coercion
    but the notice — a hand-edited `level: L3` demoted a whole project in
    silence. `None` is not a mistake (it means "unset", and the caller's
    default applies), so it stays silent.
    """
    text = str(value if value is not None else "0").strip().upper().lstrip("L")
    if text in LEVELS:
        return text
    if value is not None:
        _warn_level_fallback(value, "0")
    return "0"


def briefing_cap(config, level):
    caps = config.get("briefing_chars") or {}
    level = normalise_level(level)
    # Accept `l0`, `"0"` and `0` so a hand-edited config works either way.
    for key in (f"l{level}", level, int(level)):
        if key in caps:
            try:
                return max(0, int(caps[key]))
            except (TypeError, ValueError):
                break
    return DEFAULTS["briefing_chars"][f"l{level}"]


def tier_up(config, model):
    """The next dearer model in `models.tiers`, or `model` unchanged.

    Unchanged rather than escalated when `model` is not in the tier list at
    all: a unit that names a `model:` of its own in its own frontmatter made
    that choice on purpose, and this function has no way to tell an
    intentional off-list choice from a typo, so it leaves both alone rather
    than guessing which one it is. Unchanged rather than raising when `model`
    is already the dearest tier there is: `findings-rounds` calls this after
    every failed round without first checking whether escalation is still
    possible, and an IndexError there would fail a round for a reason that
    round had no way to see coming.
    """
    tiers = (config.get("models") or {}).get("tiers") or DEFAULTS["models"]["tiers"]
    try:
        index = tiers.index(model)
    except ValueError:
        return model
    return tiers[index + 1] if index + 1 < len(tiers) else model


def render(config):
    """Serialise a config for `ctx init`, with the levels explained inline."""
    body = miniyaml.dumps(config)
    header = (
        "# Context Ledger configuration.\n"
        "#\n"
        "#   level 0  trace    always on, no gates, ~30-token briefing\n"
        "#   level 1  tracked  one task file, done-gate active\n"
        "#   level 2  planned  spec + plan + units, both gates active\n"
        "#\n"
        "# briefing_chars caps what SessionStart may inject per level, in\n"
        "# characters (~3.6 chars per token). Raising it is the fastest way to\n"
        "# recreate the context problem this tool solves; `ctx doctor` reports\n"
        "# when a briefing had to be truncated to fit.\n"
        "#\n"
        "# The plugin's own always-on cost is separate and larger than any\n"
        "# briefing: measure it with `claude plugin details ctx`.\n"
        "#\n"
        "# models.tiers is the ordered, cheapest-first vocabulary every model\n"
        "# name in this plugin is drawn from — dispatch and complexity-score\n"
        "# look a tier up in this list rather than naming a model themselves,\n"
        "# so reordering or trimming it here is honoured everywhere at once.\n"
        "# models.escalate_on_failed_round moves a retried round one tier up\n"
        "# this list; a unit's own `model:` is never escalated, since that\n"
        "# choice was made on purpose.\n"
        "#\n"
        "# complexity.weights turns a unit's own frontmatter into a single\n"
        "# number — budget, paths owned and read, dependencies, a judged\n"
        "# verify, a published interface and a bug fix each add to it. These\n"
        "# are judgement calls, not measurements: there is no dispatch history\n"
        "# yet to weigh them against, so expect the numbers to move.\n"
        "# complexity.thresholds is where that number crosses from a light\n"
        "# unit into `standard` dispatch and then into `deep`.\n"
        "#\n"
        "# review.small_package_bytes is a different kind of threshold — bytes\n"
        "# read, not score points — below which a clean review package earns\n"
        "# the reviewer tier one cheaper than models.reviewer. Also a guess,\n"
        "# not a measurement, for the same reason the weights above are.\n"
    )
    return header + body + "\n"
