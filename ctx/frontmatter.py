"""Frontmatter + section access for task, unit and bundle files.

Every ledger document is markdown a human can read with YAML frontmatter a
script can act on. Parsing is tolerant on read (a file with no frontmatter is
still a valid document) and strict on write.
"""

import os
import time
import re
import tempfile

from . import miniyaml

FENCE = "---"
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")


def _collapse(pieces):
    """Join wrapped-line fragments into one string, whitespace normalised.

    Each fragment is already stripped of its own leading/trailing whitespace;
    this only needs to guard against a fragment carrying internal runs (a
    tab, doubled spaces) so the result always reads as one clean sentence.
    """
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()



def _replace(temp, destination):
    """`os.replace`, retried briefly on Windows.

    POSIX renames over a file no matter who has it open. Windows refuses with
    `PermissionError: [WinError 5]` while any other process holds the
    destination — so two agents writing one ledger file, which is the whole
    situation this package is built for, turned an atomic write into a raised
    exception on that platform. CI found it: a findings ledger written from two
    processes at once failed with Access is denied on the `os.replace` at the
    end of `frontmatter.Document.write`.

    The retry is short and bounded. A handle held briefly by a reader clears in
    milliseconds; one held open indefinitely is a real problem and should still
    surface as the error it is, rather than hanging.
    """
    if os.name != "nt":
        os.replace(temp, destination)
        return
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(temp, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)

class Document:
    def __init__(self, meta, body, had_frontmatter=True):
        self.meta = meta or {}
        self.body = body or ""
        self.had_frontmatter = had_frontmatter

    def sections(self):
        """Map heading text (lowercased) to its body, for any heading depth."""
        out, current, buffer = {}, None, []
        for line in self.body.splitlines():
            match = _HEADING.match(line)
            if match:
                if current is not None:
                    out[current] = "\n".join(buffer).strip()
                current, buffer = match.group(2).strip().lower(), []
            elif current is not None:
                buffer.append(line)
        if current is not None:
            out[current] = "\n".join(buffer).strip()
        return out

    def section(self, *names):
        found = self.sections()
        for name in names:
            if name.lower() in found:
                return found[name.lower()]
        return ""

    def list_items(self, *names):
        """Bullet or numbered items from a section, markers stripped.

        A markdown list item is not always one physical line: the source can
        wrap it, and any following line that is indented past the marker is
        part of the same item, not a new one. We honour that. A line closes
        the item currently being built and (if the line is itself a marker)
        opens the next one when it is blank, when it matches the marker
        pattern, or when the section ends; every other non-blank line is
        folded onto the open item *only if it is indented further than that
        item's own marker was* — text at or before the marker's own column is
        left alone, matching the pre-fix behaviour of simply dropping it,
        since nothing here can tell a stray paragraph from a mistake.
        Whitespace inside the joined result, including the run between
        physical lines, collapses to a single space.

        A nested sub-list is a real markdown construct, but this method
        returns a flat list, and the marker regex below does not look at
        indentation to decide whether something *is* a marker — only
        whether a non-marker line continues one. So an indented `- ` or
        `1.` line still opens its own item here rather than nesting into
        its parent's text; that flattening is the pre-existing behaviour of
        this method (every caller — review, briefing, work, cli —
        already receives sub-list markers as their own entries) and this
        fix deliberately leaves it alone.
        """
        items = []
        buffer = None
        marker_indent = 0
        for line in self.section(*names).splitlines():
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
            if match:
                if buffer is not None:
                    items.append(_collapse(buffer))
                buffer = [match.group(1).strip()]
                marker_indent = indent
            elif not stripped:
                if buffer is not None:
                    items.append(_collapse(buffer))
                buffer = None
            elif buffer is not None and indent > marker_indent:
                buffer.append(stripped)
        if buffer is not None:
            items.append(_collapse(buffer))
        return items

    def render(self):
        if not self.meta:
            return self.body.rstrip() + "\n"
        head = miniyaml.dumps(self.meta)
        return f"{FENCE}\n{head}\n{FENCE}\n\n{self.body.strip()}\n"

    def write(self, path):
        """Write the document atomically — a reader sees the old file or the new.

        This is the write path for every committed artifact: tasks, units,
        specs, ADRs, bundles, findings. A plain `write_text` truncates first, so
        a session killed mid-write leaves a half-file whose frontmatter no
        longer parses; the tolerant reader then supplies defaults, and a unit
        that was `running` with owned paths and passing checks comes back
        `pending` with nothing — which dispatches completed work a second time.
        `state.save` already writes the *disposable*, gitignored pointer this
        way. The durable, shared files deserve it at least as much.

        No lock is taken, unlike `state.save`: callers render a whole document
        and replace it, so there is no read-modify-write window to serialise.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.render()
        # The temp file must share the destination's directory: `os.replace` is
        # only atomic within one filesystem, and a `.tmp` suffix keeps the
        # transient file out of the `*.md` globs that scan the ledger.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent),
            prefix=".ctx-", suffix=".tmp", delete=False,
        )
        temp = handle.name
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            _replace(temp, str(path))
        except BaseException:
            # Anything that stops the replace leaves the original intact, but a
            # stray temp file next to a committed document would be noise in a
            # diff — and on Windows it would also keep the name locked.
            try:
                handle.close()
            except OSError:
                pass
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        return path


def parse(text):
    if not text.startswith(FENCE):
        return Document({}, text, had_frontmatter=False)
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            raw = "\n".join(lines[1:index])
            try:
                meta = miniyaml.loads(raw) or {}
            except miniyaml.MiniYamlError:
                meta = {}
            body = "\n".join(lines[index + 1:]).lstrip("\n")
            return Document(meta if isinstance(meta, dict) else {}, body)
    return Document({}, text, had_frontmatter=False)


def read(path):
    if not path or not path.is_file():
        return None
    return parse(path.read_text(encoding="utf-8"))
