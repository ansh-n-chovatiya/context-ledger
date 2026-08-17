#!/usr/bin/env python3
"""SubagentStop shim. All logic lives in ctx.hooks so the contract has one seam."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ctx.hooks import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main("SubagentStop"))
