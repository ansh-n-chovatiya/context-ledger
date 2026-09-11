"""Locating the ledger.

Every entry point resolves paths through here so hooks, the CLI and tests all
agree on where state lives. Discovery walks up from the working directory:
`.ctx/` wins if it exists, otherwise we fall back to the git root so `ctx init`
lands in the right place.
"""

import os
from pathlib import Path

CTX_DIRNAME = ".ctx"
GLOBAL_DEFAULT = "~/.claude/ctx"

# The repo-relative prefix every ledger path starts with, as it appears in a
# diff or a scope pattern: forward slashes, no leading `./`. Derived from
# `CTX_DIRNAME` rather than retyped — it lived as a `".ctx/"` literal in three
# modules, so renaming the ledger directory would have moved one of them and
# silently left the other two matching a directory that no longer exists.
LEDGER_PREFIX = CTX_DIRNAME + "/"


def global_root():
    """The store shared across projects, resolved on every call.

    It used to be a module-level constant, which meant `CTX_GLOBAL_ROOT` only
    took effect if it was set before the first import — so the test suite, which
    sets it in `setUp`, was silently writing into the developer's real
    `~/.claude/ctx` and sharing one store across every test.
    """
    return Path(os.environ.get("CTX_GLOBAL_ROOT") or GLOBAL_DEFAULT).expanduser()


def project_root(start=None):
    """Directory that contains (or should contain) `.ctx/`."""
    here = Path(start or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    candidates = [here, *here.parents]
    for path in candidates:
        if (path / CTX_DIRNAME).is_dir():
            return path
    for path in candidates:
        if (path / ".git").exists():
            return path
    return here


def ctx_dir(start=None):
    """The ledger directory, or None when the project has not run `ctx init`."""
    root = project_root(start) / CTX_DIRNAME
    return root if root.is_dir() else None


def require_ctx(start=None):
    root = ctx_dir(start)
    if root is None:
        raise SystemExit(
            "no .ctx/ found — run /ctx:init in the project you want to track"
        )
    return root


class Layout:
    """Named paths inside a ledger. Nothing here touches the filesystem."""

    def __init__(self, root):
        self.root = Path(root)

    config = property(lambda self: self.root / "ctx.yaml")
    tasks = property(lambda self: self.root / "tasks")
    specs = property(lambda self: self.root / "specs")
    plans = property(lambda self: self.root / "plans")
    contexts = property(lambda self: self.root / "contexts")
    journal = property(lambda self: self.root / "journal")
    decisions = property(lambda self: self.root / "decisions")
    runtime = property(lambda self: self.root / "runtime")

    digest = property(lambda self: self.journal / "DIGEST.md")
    state = property(lambda self: self.runtime / "state.json")
    nudge = property(lambda self: self.runtime / "nudge")
    errors = property(lambda self: self.runtime / "hook-errors.log")
    verify_logs = property(lambda self: self.runtime / "verify")
    context_index = property(lambda self: self.contexts / "index.md")

    def task_file(self, slug):
        return self.tasks / f"{slug}.md"

    def unit_file(self, plan_slug, unit_name):
        """`.ctx/plans/<plan_slug>/units/<unit_name>.md`.

        `plan.units_dir` owns this shape; this accessor exists so the four
        modules that only want to *read* a unit file do not each hand-build it
        — and so a reader outside `plan.py` never has to know that the segment
        is spelled `units`. `tests/test_shared_paths.py` asserts the two agree,
        which is what keeps this from becoming a second definition.
        """
        return self.plans / plan_slug / "units" / f"{unit_name}.md"

    def unit_files(self):
        """Every unit file in the ledger, sorted, `[]` when there are no plans.

        The sweep half of `unit_file`. Three modules used to spell
        `layout.plans.glob("*/units/*.md")` out by hand — `commands.py` for
        `ctx check`'s contract-drift pass, `migrate.py` for the per-kind
        migration, `trust.py` for collecting declared commands — and each also
        carried its own `is_dir()` guard, because `Path.glob` on a directory
        that does not exist is an empty iterator on some platforms and an
        `OSError` waiting to happen on others. Three copies of a search is the
        same defect as three copies of a path: change where units live and two
        of them keep looking in the old place.

        Sorted here rather than at the call sites: all three sorted anyway, and
        a sweep whose order depends on `readdir` gives `ctx migrate` a
        different report on two machines with the same ledger.
        """
        if not self.plans.is_dir():
            return []
        return sorted(self.plans.glob("*/units/*.md"))

    def journal_file(self, day):
        return self.journal / f"{day}.md"

    def rel(self, path):
        """Repo-relative display path, always with forward slashes.

        Briefings must never leak absolutes, and the separator must not depend on
        who ran the command: the journal, the digest and every unit's `owns` list
        are committed and shared, so a Windows session writing `src\\a.py` where a
        mac session writes `src/a.py` is divergence in a tracked file — merge
        noise, and two spellings of one path for scope matching to disagree over.
        """
        try:
            # Both sides resolved: comparing a resolved path against an
            # unresolved root fails for any project reached through a symlink,
            # and the fallback is the absolute path this exists to avoid.
            relative = str(Path(path).resolve().relative_to(self.root.resolve().parent))
        except (ValueError, OSError):
            relative = str(path)
        return relative.replace(os.sep, "/") if os.sep != "/" else relative

    # Directories that are created by init and expected to exist thereafter.
    def dirs(self):
        return [
            self.root, self.tasks, self.specs, self.plans, self.contexts,
            self.journal, self.decisions, self.runtime, self.verify_logs,
        ]


def global_layout():
    """Store for contexts promoted out of a single project."""
    return Layout(global_root())
