"""Frontmatter + section access for task, unit and bundle files.

Every ledger document is markdown a human can read with YAML frontmatter a
script can act on. Parsing is tolerant on read (a file with no frontmatter is
still a valid document) and strict on write.
"""

import os
import re
import tempfile

from . import miniyaml

FENCE = "---"
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")


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
        """Bullet or numbered items from a section, markers stripped."""
        items = []
        for line in self.section(*names).splitlines():
            stripped = line.strip()
            match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
            if match:
                items.append(match.group(1).strip())
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
            os.replace(temp, str(path))
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
