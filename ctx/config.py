"""ctx.yaml — defaults, merge and the engagement-level budgets.

The character caps in `briefing_chars` are the whole anti-bloat mechanism, so
they live in config where they can be measured (`ctx doctor`) and asserted in
CI. Characters rather than tokens on purpose: no tokenizer dependency, and the
ratio (~3.6 chars/token for prose) is stable enough for a budget.
"""

import copy

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
    "journal": {"digest_lines": 12, "max_line_chars": 200, "enabled": True},
    "gate": {
        "enabled": True,
        "max_attempts": 3,
        "output_head": 40,
        "output_tail": 20,
        "timeout_seconds": 240,  # must stay under the Stop hook timeout (300s)
    },
    "auto_load": [],
    "redact": [],
    "verify": [],
}

# Per-profile verify defaults proposed by `ctx init`. Every one is dry-run
# before it is written, so a project only ever inherits checks that pass on a
# clean tree.
PROFILES = {
    "code": [],
    "docs": [{"kind": "exists", "path": "."}],
    "research": [{"kind": "rubric"}],
    "infra": [],
    "data": [],
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


def normalise_level(value):
    text = str(value if value is not None else "0").strip().upper().lstrip("L")
    return text if text in LEVELS else "0"


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


def render(config):
    """Serialise a config for `ctx init`, with the levels explained inline."""
    body = miniyaml.dumps(config)
    header = (
        "# Context Ledger configuration.\n"
        "#\n"
        "#   level 0  trace    always on, no gates, ~55 tokens per session\n"
        "#   level 1  tracked  one task file, done-gate active\n"
        "#   level 2  planned  spec + plan + units, both gates active\n"
        "#\n"
        "# briefing_chars caps what SessionStart may inject per level. Raising it\n"
        "# is the fastest way to recreate the context problem this tool solves.\n"
    )
    return header + body + "\n"
