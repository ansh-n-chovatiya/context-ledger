"""Shared atomic-write primitive.

`frontmatter.Document.write` already does temp-file + fsync + `os.replace` so
that a reader sees the old file or the new one, never a half-written one. This
module lifts that implementation out so other call sites — anything still
using a plain `Path.write_text`, which truncates first and leaves a torn file
behind if the process dies mid-write — can route through the same path.
"""

import os
import tempfile


def write_text(path, text, encoding="utf-8"):
    """Write `text` to `path` atomically. Returns `path`.

    Creates parent directories. The temp file is made in the destination's own
    directory (os.replace is atomic only within one filesystem) with a `.ctx-`
    prefix and `.tmp` suffix, so it is not picked up by the `*.md` and
    `*.json` globs that scan the ledger. On any exception the temp file is
    removed and the original is left byte-identical.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding=encoding, dir=str(path.parent),
        prefix=".ctx-", suffix=".tmp", delete=False,
    )
    temp = handle.name
    try:
        handle.write(text)
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
