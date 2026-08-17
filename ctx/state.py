"""Machine-local pointers: which level we are at and what is active.

Lives in `.ctx/runtime/` and is gitignored — it is the only part of the ledger
that is not meant to be reviewed. Writes are atomic so a killed session cannot
leave a half-written pointer that breaks the next SessionStart.
"""

import json
import os
import tempfile

from . import config as config_mod

EMPTY = {
    "schema": config_mod.SCHEMA,
    "level": "0",
    "task": None,
    "spec": None,
    "plan": None,
    "unit": None,
    "attempts": {},
    "last_session": None,
}


def load(layout):
    data = dict(EMPTY)
    path = layout.state
    if path.is_file():
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(found, dict):
                data.update(found)
        except (OSError, ValueError):
            pass  # a corrupt pointer degrades to L0 rather than failing
    data["level"] = config_mod.normalise_level(data.get("level"))
    if not isinstance(data.get("attempts"), dict):
        data["attempts"] = {}
    return data


def save(layout, data):
    layout.runtime.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(layout.runtime), delete=False, suffix=".tmp"
    )
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, layout.state)
    return data


def update(layout, **changes):
    data = load(layout)
    data.update(changes)
    return save(layout, data)


def attempts(layout, key):
    return int(load(layout).get("attempts", {}).get(key, 0))


def bump_attempts(layout, key):
    data = load(layout)
    counts = data.setdefault("attempts", {})
    counts[key] = int(counts.get(key, 0)) + 1
    save(layout, data)
    return counts[key]


def clear_attempts(layout, key=None):
    data = load(layout)
    if key is None:
        data["attempts"] = {}
    else:
        data.get("attempts", {}).pop(key, None)
    return save(layout, data)


def set_nudge(layout, message):
    """Queue a one-shot correction for the next UserPromptSubmit."""
    try:
        layout.runtime.mkdir(parents=True, exist_ok=True)
        layout.nudge.write_text(message.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def take_nudge(layout):
    """Read and clear the pending nudge. Empty string when there is none."""
    path = layout.nudge
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        path.unlink()
    except OSError:
        pass
    return text
