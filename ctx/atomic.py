"""Shared atomic-write primitive.

`frontmatter.Document.write` already does temp-file + fsync + `os.replace` so
that a reader sees the old file or the new one, never a half-written one. This
module lifts that implementation out so other call sites — anything still
using a plain `Path.write_text`, which truncates first and leaves a torn file
behind if the process dies mid-write — can route through the same path.
"""

import os
import time
import tempfile



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
