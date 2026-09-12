---
name: Bug report
about: Something behaves differently from what it says it does
labels: bug
---

**What happened, and what you expected instead.**

**How to reproduce it.** The smallest sequence that shows it. If it involves a
ledger, the `ctx.yaml` and the unit or task file matter more than the prose.

**What `ctx doctor` says.**

```
$ ctx doctor
```

**Diagnostics.** `ctx` swallows failures on the paths that must never break a
session, so a failed write can look like success. `CTX_LOG=error` surfaces
them:

```
$ CTX_LOG=error ctx <the command> 2>&1 | tail -20
```

**Environment.** `ctx --version`, your OS, and `python3 --version`. Python 3.8
and 3.9 behave differently from 3.12+ in places — argparse, pathlib and append
atomicity have all produced platform-specific bugs here — so the version is
rarely irrelevant.
