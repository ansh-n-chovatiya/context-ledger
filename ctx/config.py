"""ctx.yaml — defaults, merge and the engagement-level budgets.

The character caps in `briefing_chars` are the whole anti-bloat mechanism, so
they live in config where they can be measured (`ctx doctor`) and asserted in
CI. Characters rather than tokens on purpose: no tokenizer dependency, and the
ratio (~3.6 chars/token for prose) is stable enough for a budget.
"""

import copy
import sys

from . import miniyaml

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
        # Days of journal history `ctx prune` keeps. 0 means keep everything.
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
    "plan": {"wave_budget_tokens": 250000},
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


def load(layout):
    """Config for a ledger, defaults merged under any on-disk overrides."""
    data = copy.deepcopy(DEFAULTS)
    path = layout.config
    if path.is_file():
        parsed = miniyaml.loads(path.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise miniyaml.MiniYamlError("ctx.yaml must be a mapping")
        found = parsed.get("schema", SCHEMA)
        if isinstance(found, int) and found > SCHEMA:
            raise SystemExit(
                f"ctx.yaml declares schema {found} but this plugin understands {SCHEMA} "
                "— upgrade the plugin rather than downgrading the ledger"
            )
        _merge(data, parsed)
    data["level"] = normalise_level(data.get("level"))
    return data


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        elif value is not None:
            base[key] = value


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
