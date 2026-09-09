"""Content snapshots: a diff that does not need a version control system.

Reviewing a unit's work means answering two questions — what changed, and did
anything change that the unit never declared it owned. Both are normally answered
with a commit range, and that answer costs a commit: the implementer has to commit
before it can be reviewed, so the review protocol ends up dictating the project's
git history. This module answers both from content instead.

A snapshot is taken before a unit is dispatched and again after it reports.
*Every* file in the project is fingerprinted, so a write to a path nobody declared
is still visible; the declared paths additionally have their bytes stored, because
those are the ones a diff has to be reconstructed from. Nothing is committed,
nothing is branched, and a project with no repository at all reviews exactly the
same way as one with a hundred thousand commits.

Snapshots live under `.ctx/runtime/`, which the ledger's own `.gitignore`
excludes — they are scratch, not history. Content is stored raw and scrubbed at
render time rather than at capture: redacting on the way in would make every
credential-shaped line read as a change on the way out, which is a diff that lies
about what the unit did.
"""

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

SNAPSHOT_SUBDIR = "snapshots"

# Directories whose contents are never anyone's declared work: build output,
# dependency trees, and the ledger's own scratch. Fingerprinting them would cost
# most of the walk and report a "change" every time a cache warmed up.
DEFAULT_IGNORE = (
    ".git", ".hg", ".svn", ".ctx/runtime", "node_modules", "__pycache__",
    ".venv", "venv", ".env", "dist", "build", "target", ".next", ".nuxt",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".gradle", ".idea",
    ".vscode", "vendor", "coverage", ".DS_Store", "*.pyc", "*.pyo", "*.so",
    "*.dylib", "*.class", "*.o", "*.a", "*.egg-info",
)

# A file larger than this is fingerprinted but never stored, and a project with
# more files than the cap is snapshotted as far as the cap allows and flagged.
# Both bounds exist so a snapshot cannot become the slow part of a dispatch.
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 20_000


def settings(config):
    """Review knobs, defaults merged under any ctx.yaml override."""
    review = (config or {}).get("review") or {}
    ignore = review.get("ignore")
    return {
        "ignore": tuple(ignore) if ignore else DEFAULT_IGNORE,
        "max_file_bytes": int(review.get("max_file_bytes") or MAX_FILE_BYTES),
        "max_files": int(review.get("max_files") or MAX_FILES),
    }


def root_dir(layout):
    return layout.runtime / SNAPSHOT_SUBDIR


def snapshot_dir(layout, key):
    return root_dir(layout) / _safe(key)


def _safe(key):
    """A key is a filesystem name, so it may not carry separators of its own."""
    return re.sub(r"[^A-Za-z0-9._@-]", "-", str(key)) or "unnamed"


def _blob_name(relpath):
    """Content is filed under a hash of its path: no traversal, no length limit."""
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# walking
# --------------------------------------------------------------------------- #

def _ignored(relpath, name, ignore):
    """True when a path segment or the whole relative path is excluded."""
    import fnmatch

    if any(fnmatch.fnmatchcase(name, pattern) for pattern in ignore):
        return True
    return any(
        relpath == pattern or relpath.startswith(pattern.rstrip("/") + "/")
        for pattern in ignore
    )


def walk(root, ignore=DEFAULT_IGNORE, max_files=MAX_FILES):
    """Relative paths of every file worth fingerprinting. (paths, truncated)."""
    root = Path(root)
    found, truncated = [], False
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        # Pruning in place is what keeps `node_modules` from costing anything.
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not _ignored(f"{rel_dir}/{d}".lstrip("/"), d, ignore)
        ]
        for name in sorted(filenames):
            relpath = f"{rel_dir}/{name}".lstrip("/")
            if _ignored(relpath, name, ignore):
                continue
            if len(found) >= max_files:
                truncated = True
                return found, truncated
            found.append(relpath)
    return found, truncated


def covers(relpath, patterns):
    """Whether a declared scope entry covers this path.

    An entry may name a file, a directory prefix, or a glob — the same three
    shapes `owns` already accepts, matched the same way, so the review boundary
    and the dispatch boundary cannot drift apart.
    """
    import fnmatch

    normalised = str(relpath).replace(os.sep, "/")
    for raw in patterns or ():
        pattern = str(raw).replace(os.sep, "/").rstrip("/")
        if not pattern:
            continue
        if normalised == pattern:
            return True
        if normalised.startswith(pattern + "/"):
            return True
        if fnmatch.fnmatchcase(normalised, pattern):
            return True
        if fnmatch.fnmatchcase(normalised, pattern.rstrip("/") + "/*"):
            return True
    return False


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #

LEDGER_PREFIX = ".ctx/"


def is_ledger(path):
    """Ledger bookkeeping: changed by ctx itself, owned by no unit."""
    return str(path).replace("\\", "/").startswith(LEDGER_PREFIX)


def is_binary(data):
    return b"\x00" in data[:8192]


def capture(layout, config, key, root, content_paths=()):
    """Fingerprint the tree, storing bytes for the declared scope. Returns the
    manifest it wrote.

    `content_paths` is normally the unit's `owns` plus its `reads`: those are the
    paths a diff may need to be reconstructed from. Everything else is recorded
    by hash alone, which is enough to prove that it changed and cheap enough to
    do on every dispatch.
    """
    options = settings(config)
    root = Path(root)
    directory = snapshot_dir(layout, key)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    blobs = directory / "files"
    blobs.mkdir(parents=True, exist_ok=True)

    paths, truncated = walk(root, options["ignore"], options["max_files"])
    files, stored = {}, []
    for relpath in paths:
        target = root / relpath
        try:
            data = target.read_bytes()
        except OSError:
            continue  # a file that vanished mid-walk is not a change to report
        entry = {
            "sha": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        if is_binary(data):
            entry["binary"] = True
        files[relpath] = entry
        wanted = covers(relpath, content_paths)
        if wanted and not entry.get("binary") and len(data) <= options["max_file_bytes"]:
            (blobs / _blob_name(relpath)).write_bytes(data)
            stored.append(relpath)

    manifest = {
        "key": str(key),
        "root": str(root),
        "taken_at": time.time(),
        "truncated": truncated,
        "content_paths": [str(p) for p in content_paths],
        "stored": sorted(stored),
        "files": files,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def store_extra(layout, config, key, root, relpaths):
    """Store content for paths the capture did not think it needed.

    A unit that wrote outside `owns` produced a change whose path was, by
    definition, not in the declared content set — so its bytes were never kept
    and the reviewer would see the violation named but not shown. Called after
    the comparison, this fills in the *after* side so the review can quote what
    actually landed. The before side stays genuinely absent, and is rendered as
    absent rather than as an empty file.
    """
    options = settings(config)
    directory = snapshot_dir(layout, key)
    manifest = load(layout, key)
    if manifest is None:
        return manifest
    blobs = directory / "files"
    blobs.mkdir(parents=True, exist_ok=True)
    stored = set(manifest.get("stored") or [])
    for relpath in relpaths:
        if relpath in stored:
            continue
        entry = (manifest.get("files") or {}).get(relpath)
        if not entry or entry.get("binary"):
            continue
        if entry.get("size", 0) > options["max_file_bytes"]:
            continue
        try:
            data = (Path(root) / relpath).read_bytes()
        except OSError:
            continue
        (blobs / _blob_name(relpath)).write_bytes(data)
        stored.add(relpath)
    manifest["stored"] = sorted(stored)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def load(layout, key):
    """The manifest for a key, or None when nothing was captured under it."""
    path = snapshot_dir(layout, key) / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


TEST_RUNS_SUBDIR = "test_runs"


def test_runs_dir(layout):
    return layout.runtime / TEST_RUNS_SUBDIR


def _test_runs_path(layout, key):
    return test_runs_dir(layout) / f"{_safe(key)}.json"


def record_test_run(layout, key, paths, exit_code, when=None):
    """Append one test run's exit status and timestamp under a key.

    `test_first` needs to answer "did a failing run happen before the
    implementation was captured", and an answer to that has to survive the
    session that produced it — a run recorded only in a transcript is gone the
    moment the transcript is.

    Deliberately *not* stored inside `snapshot_dir(layout, key)`: `capture`
    deletes that whole directory and rebuilds it on every call (`if
    directory.exists(): shutil.rmtree(...)`), which would erase a failing run
    recorded before a later capture — destroying the exact evidence
    `test_first` exists to check. A history of runs has to outlive the
    manifests it will be compared against, so it lives in its own directory
    next to `snapshots/`, keyed the same way but on its own lifecycle.

    Appended rather than overwritten, because the run that proves red-before-
    green is very often not the most recent one — a unit usually keeps running
    its tests after they start passing, and the last entry in that history is
    always green. Losing the early failing run to a later success would make
    a real test-first unit look like it skipped the test.
    """
    directory = test_runs_dir(layout)
    directory.mkdir(parents=True, exist_ok=True)
    path = _test_runs_path(layout, key)
    runs = test_runs(layout, key)
    runs.append({
        "paths": [str(p) for p in paths],
        "exit_code": int(exit_code),
        "at": float(when) if when is not None else time.time(),
    })
    path.write_text(json.dumps(runs, indent=2, sort_keys=True), encoding="utf-8")
    return runs


def test_runs(layout, key):
    """Recorded test runs for a key, oldest first. `[]` when none were ever recorded.

    Empty, not missing, is the honest answer for a unit nobody ever ran a test
    for — `test_first` turns that into a fail rather than treating an absent
    file as nothing to check.
    """
    path = _test_runs_path(layout, key)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return list(data) if isinstance(data, list) else []


def stored_text(layout, key, relpath):
    """The captured text of one path, or None if it was never stored."""
    blob = snapshot_dir(layout, key) / "files" / _blob_name(relpath)
    if not blob.is_file():
        return None
    try:
        return blob.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def discard(layout, key):
    shutil.rmtree(snapshot_dir(layout, key), ignore_errors=True)


def discard_all(layout):
    shutil.rmtree(root_dir(layout), ignore_errors=True)


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #

class Delta:
    """What changed between two snapshots, by path.

    `added` and `deleted` are literal: a created file has no before, a deleted
    one has no after, and both are represented as an empty side rather than
    omitted — a review that silently drops deletions is a review that cannot see
    the most destructive edit a unit can make.
    """

    def __init__(self, added, modified, deleted, truncated=False):
        self.added = added
        self.modified = modified
        self.deleted = deleted
        self.truncated = truncated

    @property
    def changed(self):
        return sorted(self.added + self.modified + self.deleted)

    def __bool__(self):
        return bool(self.added or self.modified or self.deleted)

    def __repr__(self):
        return (f"<Delta +{len(self.added)} ~{len(self.modified)} "
                f"-{len(self.deleted)}>")


def compare(before, after):
    """Delta between two manifests. Either may be None.

    Deletions are not reported when either snapshot hit the file cap. `walk`
    truncates a *sorted* listing, so adding one file near the front pushes one
    off the end — and that file then appears in the before set and not the
    after, which reads as a deletion the unit never made. Observed: a unit that
    edited one file and added another was accused of deleting `f049.txt`, which
    it had never opened. An accusation that a snapshot cannot support must not be
    made; `truncated` is set so the caller says so instead.
    """
    before_files = (before or {}).get("files") or {}
    after_files = (after or {}).get("files") or {}
    truncated = bool((before or {}).get("truncated") or (after or {}).get("truncated"))
    added = sorted(p for p in after_files if p not in before_files)
    deleted = [] if truncated else sorted(
        p for p in before_files if p not in after_files
    )
    modified = sorted(
        p for p in after_files
        if p in before_files and after_files[p]["sha"] != before_files[p]["sha"]
    )
    return Delta(added, modified, deleted, truncated)


def out_of_scope(delta, owns):
    """Changed paths the unit never declared it owned.

    This is the check `unit-runner.md` has always asserted — "never write outside
    `owns` — not a suggestion" — and that nothing had ever actually performed.
    It needs no model: either the path is covered by a declared pattern or it is
    not.

    The ledger is excluded. Every `ctx` command appends to the journal and flips
    a `status:` field, so without this every review would open with a Critical
    finding against `.ctx/journal/<today>.md` — noise that trains a reader to
    skip the section that matters.
    """
    return [path for path in delta.changed
            if not is_ledger(path) and not covers(path, owns)]
