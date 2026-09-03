#!/usr/bin/env python3
"""Platform-neutral entry point.

The real logic is `python -m ctx`; this exists so a caller does not have to set
PYTHONPATH, and so there is one entry point that is not a shell script. The
`bin/ctx` (POSIX) and `bin/ctx.cmd` (Windows) wrappers both delegate here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ctx.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
