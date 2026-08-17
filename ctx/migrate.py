"""Schema migration.

`config.load` already refuses a ledger stamped newer than the plugin understands,
which prevents corruption but leaves you stuck. This is the other half: walking
every ledger file, working out what schema it is at, and upgrading it.

Three details worth stating, because they are what makes a migration safe to run
on someone's repository:

* **Idempotent.** Running it twice changes nothing the second time, so a
  half-finished run is recoverable by running it again.
* **`--check` never writes.** CI can assert a repo is current without mutating it.
* **`ctx.yaml` is edited textually**, not re-rendered. Re-rendering would preserve
  the values and silently drop any comments the user added.

Schema 0 means "written before this plugin stamped versions" — an unstamped file
is not corrupt, it is old, and stamping it is the 0→1 migration.
"""

import json
import re

from . import bundle, config as config_mod, frontmatter

# Frontmatter key by file family. Bundles use their own key for the same reason
# they are portable: the file should announce what kind of thing it is.
KEY_CTX = "ctx_schema"
KEY_BUNDLE = "ctx_bundle"
KEY_CONFIG = "schema"

_CONFIG_LINE = re.compile(r"^(\s*)schema\s*:\s*(\d+)\s*$", re.M)


class Item:
    """One migratable file: where it is, what schema it is at, how to write it."""

    def __init__(self, path, kind, version):
        self.path = path
        self.kind = kind
        self.version = version

    def __repr__(self):
        return f"<{self.kind} {self.path.name} v{self.version}>"


def discover(layout):
    """Every ledger file we know how to version, with its current schema."""
    items = []

    if layout.config.is_file():
        text = layout.config.read_text(encoding="utf-8")
        match = _CONFIG_LINE.search(text)
        items.append(Item(layout.config, "config", int(match.group(2)) if match else 0))

    families = (
        ("task", layout.tasks.glob("*.md"), KEY_CTX),
        ("spec", layout.specs.glob("*/spec.md"), KEY_CTX),
        ("questions", layout.specs.glob("*/questions.md"), KEY_CTX),
        ("unit", layout.plans.glob("*/units/*.md"), KEY_CTX),
        ("decision", layout.decisions.glob("*.md"), KEY_CTX),
        ("bundle", layout.contexts.glob("*" + bundle.SUFFIX), KEY_BUNDLE),
    )
    for kind, paths, key in families:
        for path in sorted(paths):
            doc = frontmatter.read(path)
            if doc is None:
                continue
            try:
                version = int(doc.meta.get(key, 0) or 0)
            except (TypeError, ValueError):
                version = 0
            items.append(Item(path, kind, version))

    for path in sorted(layout.plans.glob("*/plan.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = int(data.get(KEY_CTX, 0) or 0)
        except (OSError, ValueError, TypeError):
            continue
        items.append(Item(path, "graph", version))

    return items


def pending(layout, target=None):
    """Files below the target schema, and any stamped above it."""
    target = config_mod.SCHEMA if target is None else target
    items = discover(layout)
    behind = [i for i in items if i.version < target]
    ahead = [i for i in items if i.version > target]
    return behind, ahead


def upgrade(layout, target=None, dry_run=False):
    """(changed, problems). With dry_run, reports what it would do and writes nothing."""
    target = config_mod.SCHEMA if target is None else target
    behind, ahead = pending(layout, target)
    problems = [
        f"{layout.rel(i.path)} is stamped v{i.version}, newer than this plugin's "
        f"v{target} — upgrade the plugin rather than downgrading the ledger"
        for i in ahead
    ]
    if problems:
        return [], problems

    changed = []
    for item in behind:
        steps = [v for v in range(item.version + 1, target + 1)]
        if not steps:
            continue
        if dry_run:
            changed.append((item, steps))
            continue
        try:
            for version in steps:
                MIGRATIONS[version](layout, item)
            item.version = target
            changed.append((item, steps))
        except (OSError, ValueError) as exc:
            problems.append(f"{layout.rel(item.path)}: {exc}")
    return changed, problems


# --------------------------------------------------------------------------- #
# migrations
# --------------------------------------------------------------------------- #

def _to_v1(layout, item):
    """0 → 1: stamp the schema version.

    Files written before versioning existed, or unit files a user hand-wrote
    without frontmatter boilerplate. Stamping is the whole migration.
    """
    if item.kind == "config":
        text = layout.config.read_text(encoding="utf-8")
        if _CONFIG_LINE.search(text):
            text = _CONFIG_LINE.sub(r"\g<1>schema: 1", text, count=1)
        else:
            lines = text.splitlines()
            insert = 0
            while insert < len(lines) and (
                not lines[insert].strip() or lines[insert].lstrip().startswith("#")
            ):
                insert += 1
            lines.insert(insert, "schema: 1")
            text = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")
        layout.config.write_text(text, encoding="utf-8")
        return

    if item.kind == "graph":
        data = json.loads(item.path.read_text(encoding="utf-8"))
        data[KEY_CTX] = 1
        item.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return

    doc = frontmatter.read(item.path)
    if doc is None:
        raise ValueError("unreadable")
    key = KEY_BUNDLE if item.kind == "bundle" else KEY_CTX
    doc.meta[key] = 1
    # A file with no frontmatter at all needs its identity too, or the next
    # discover() pass cannot tell what family it belongs to.
    if item.kind == "unit" and not doc.meta.get("unit"):
        doc.meta["unit"] = item.path.stem
    if item.kind == "bundle" and not doc.meta.get("name"):
        doc.meta["name"] = item.path.name[: -len(bundle.SUFFIX)]
    doc.write(item.path)


MIGRATIONS = {1: _to_v1}
