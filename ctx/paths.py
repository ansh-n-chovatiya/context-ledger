"""Locating the ledger.

Every entry point resolves paths through here so hooks, the CLI and tests all
agree on where state lives. Discovery walks up from the working directory:
`.ctx/` wins if it exists, otherwise we fall back to the git root so `ctx init`
lands in the right place.
"""

import os
from pathlib import Path

CTX_DIRNAME = ".ctx"
GLOBAL_ROOT = Path(os.environ.get("CTX_GLOBAL_ROOT", "~/.claude/ctx")).expanduser()


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
    return Layout(GLOBAL_ROOT)
