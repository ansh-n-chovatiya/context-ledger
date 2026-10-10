"""Read-only measurement of the context a project auto-loads.

Nothing here writes, edits or deletes. Token counts are chars/4 estimates and
are always labelled as such.
"""

import json
import os
import shutil
import sys
from typing import NamedTuple

CHARS_PER_TOKEN = 4
DEFAULT_THRESHOLD_TOKENS = 6000

BIG_DIR_NAMES = ("node_modules", "dist", "build", ".venv", "venv", "target",
                 "__pycache__")


class Item(NamedTuple):
    path: str
    chars: int
    tokens: int
    kind: str
    note: str


class Report(NamedTuple):
    items: list
    total_tokens: int
    threshold_tokens: int
    over: bool
    big_dirs: list
    notes: list


def estimate_tokens(chars):
    return -(-int(chars) // CHARS_PER_TOKEN)


def _inside(path, base):
    try:
        real, rbase = os.path.realpath(path), os.path.realpath(base)
        return os.path.commonpath([real, rbase]) == rbase
    except ValueError:
        return False


def _item(path, shown, kind, base):
    if not _inside(path, base):
        return Item(shown, 0, 0, kind, "symlink outside project")
    note = "symlink" if os.path.islink(path) else ""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Item(shown, 0, 0, kind, "not valid utf-8")
    except OSError:
        return Item(shown, 0, 0, kind, "unreadable")
    chars = len(text)
    return Item(shown, chars, estimate_tokens(chars), kind, note)


def measure(root, home=None):
    root = str(root)
    items = []
    for rel, kind in (("CLAUDE.md", "project"), ("CLAUDE.local.md", "local"),
                      (os.path.join(".claude", "CLAUDE.md"), "project")):
        p = os.path.join(root, rel)
        if os.path.lexists(p) and not os.path.isdir(p):
            items.append(_item(p, rel.replace(os.sep, "/"), kind, root))
    rules = os.path.join(root, ".claude", "rules")
    if os.path.isdir(rules):
        for dirpath, dirnames, files in os.walk(rules):
            dirnames.sort()
            for name in sorted(files):
                if name.endswith(".md"):
                    p = os.path.join(dirpath, name)
                    shown = os.path.relpath(p, root).replace(os.sep, "/")
                    items.append(_item(p, shown, "rule", root))
    if home is not None:
        p = os.path.join(str(home), ".claude", "CLAUDE.md")
        if os.path.lexists(p) and not os.path.isdir(p):
            items.append(_item(p, "~/.claude/CLAUDE.md", "user", str(home)))
    items.sort(key=lambda i: (-i.tokens, i.path))
    return items


def _denied_text(root, notes):
    parts = []
    for name in ("settings.json", "settings.local.json"):
        p = os.path.join(root, ".claude", name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            deny = data["permissions"]["deny"]
        except (ValueError, OSError):
            notes.append("could not parse .claude/%s; ignored" % name)
            continue
        except KeyError:
            continue
        except TypeError:
            notes.append("unexpected shape in .claude/%s; ignored" % name)
            continue
        if isinstance(deny, list):
            parts.extend(d for d in deny if isinstance(d, str))
    return parts


def _denies(rule, name):
    rule = rule.strip()
    if not (rule.startswith("Read(") and rule.endswith(")")):
        return False
    parts = [c for c in rule[5:-1].strip().split("/")
             if c not in ("", ".", "*", "**")]
    return name in parts


def check(root, home=None, threshold_tokens=DEFAULT_THRESHOLD_TOKENS):
    root = str(root)
    items = measure(root, home)
    total = sum(i.tokens for i in items)
    notes = ["token counts are chars/4 estimates",
             "could not verify that `.claudeignore` is honoured; "
             "use permission deny rules"]
    deny = _denied_text(root, notes)
    big = [d for d in BIG_DIR_NAMES
           if os.path.isdir(os.path.join(root, d))
           and not any(_denies(rule, d) for rule in deny)]
    return Report(items, total, threshold_tokens, total > threshold_tokens,
                  big, notes)


def python3_status(platform=None, which=None):
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    if which("python3"):
        return True, ""
    msg = "the ctx hooks run as `python3 ...` but `python3` is not on PATH. "
    if platform == "win32":
        msg += ("Install Python 3 and make `python3` resolvable (e.g. the "
                "Microsoft Store alias, or a `python3.exe` copy/shim).")
    else:
        msg += "Install Python 3.8+ and make sure `python3` is on PATH."
    return False, msg


def rtk_on_path(which=None):
    which = shutil.which if which is None else which
    return bool(which("rtk"))
