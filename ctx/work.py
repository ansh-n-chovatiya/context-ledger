"""The active unit of work, whichever level we are at.

L1 tracks a task file, L2 tracks a unit inside a plan. The gate, the briefing
and the CLI all need the same five facts about whichever is active, so they
resolve them here rather than each knowing about both shapes.
"""

import datetime
import os

from . import config as config_mod, frontmatter, state


class Work:
    """Active work: its file, its checks, its scope and its sign-off record."""

    def __init__(self, key, path, doc, level):
        self.key = key
        self.path = path
        self.doc = doc
        self.level = level

    @property
    def checks(self):
        return self.doc.meta.get("verify") or []

    @property
    def owns(self):
        return [str(p) for p in (self.doc.meta.get("owns") or [])]

    @property
    def recorded(self):
        """Judged kinds already signed off for the current state of the files."""
        return [str(k) for k in (self.doc.meta.get("verified") or [])]

    @property
    def criteria(self):
        return self.doc.list_items("acceptance criteria", "criteria")

    @property
    def attempt_key(self):
        """Namespaced, because gate attempts are counted per key and unit names
        are only unique inside their plan — two plans with an `01-api` shared a
        counter, so one plan's failures escalated the other's work."""
        plan = self.doc.meta.get("plan")
        return f"{plan}/{self.key}" if self.level == "2" and plan else self.key

    def record(self, kind, note=""):
        signed = set(self.recorded)
        signed.add(str(kind))
        self.doc.meta["verified"] = sorted(signed)
        self.doc.meta["verified_at"] = datetime.date.today().isoformat()
        if note:
            self.doc.meta["verified_note"] = note
        self.doc.write(self.path)

    def clear_recorded(self):
        """Called after any edit: a sign-off must not outlive the code it signed."""
        if not self.doc.meta.get("verified"):
            return False
        self.doc.meta.pop("verified", None)
        self.doc.meta.pop("verified_at", None)
        self.doc.meta.pop("verified_note", None)
        self.doc.write(self.path)
        return True

    def set_status(self, status):
        self.doc.meta["status"] = status
        self.doc.write(self.path)


def claim():
    """An explicit per-process claim from the environment, or (None, None).

    `state.json` is machine-local, so its `unit` pointer describes the machine,
    not the agent. Worktree-tier units are already isolated — each has its own
    checkout and therefore its own `.ctx/runtime/` — but two sessions working the
    same tree share one pointer, and the last `ctx unit` wins for both.

    `CTX_UNIT` (with `CTX_PLAN`) is the way out: it belongs to one process, so a
    terminal can say what it is working on without arguing with its neighbour.
    """
    unit = (os.environ.get("CTX_UNIT") or "").strip()
    plan = (os.environ.get("CTX_PLAN") or "").strip()
    return (unit or None), (plan or None)


def active(layout, current=None):
    """The Work in progress, or None at L0 / when nothing is pointed at."""
    current = current or state.load(layout)
    level = config_mod.normalise_level(current.get("level"))

    claimed_unit, claimed_plan = claim()
    if claimed_unit:
        plan = claimed_plan or current.get("plan")
        if plan:
            path = layout.plans / plan / "units" / f"{claimed_unit}.md"
            doc = frontmatter.read(path)
            if doc:
                return Work(claimed_unit, path, doc, "2")

    if level == "1" and current.get("task"):
        slug = current["task"]
        path = layout.task_file(slug)
        doc = frontmatter.read(path)
        return Work(slug, path, doc, level) if doc else None
    if level == "2" and current.get("plan") and current.get("unit"):
        unit = current["unit"]
        path = layout.plans / current["plan"] / "units" / f"{unit}.md"
        doc = frontmatter.read(path)
        return Work(unit, path, doc, level) if doc else None
    return None
