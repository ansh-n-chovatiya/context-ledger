# Contributing

Thanks for looking. This file says how a change gets in, and what the checks
are actually protecting — the second half matters more, because most of the
rules here exist because something went wrong once.

## Before you start

Open an issue for anything larger than a fix. This project is a context ledger
for agent work, so the design constraints are unusual and a plan is cheaper to
discuss than a branch.

## Running the suite

```
python3 -m unittest discover -s tests -q      # the whole thing
ruff check ctx/ tests/                        # pinned in CI to 0.16.7
./bin/ctx doctor && ./bin/ctx ci              # the tool checking itself
```

Python 3.8 is the floor and it is real: CI runs 3.8 through 3.13 on Linux, and
3.9 and 3.13 on macOS and Windows. No `match`, no PEP 604 unions, no
`removeprefix`.

## What CI enforces, and why

| Check | Exists because |
| --- | --- |
| A test-count floor | `unittest discover` exits 0 on "Ran 0 tests", so green did not entail that the suite ran |
| Coverage floors | line and branch, measured then floored just under, so a silent drop is visible |
| `ruff` | a narrow rule set the code passes; rules needing source changes are listed in `pyproject.toml` with reasons |
| SHA-pinned actions | a tag is mutable; a pin is the artifact that actually ran |
| Windows | the exit-code wrapper broke there for six versions before anyone noticed |

The floors are pinned **in pairs** — the workflow's value and the test's copy
must be equal, and a test asserts it. Raising one alone is red on purpose:
they drifted 449 tests apart once.

## Tests

Two habits are the house style, and both are load-bearing:

**Show the test failing first.** A test written after the fix, that has never
been seen red, is evidence of nothing. This suite has shipped tests that passed
because a loop body never executed, because a mocked function was never called,
and because an assertion compared a value to itself.

**Assert the race actually happened.** A concurrency test that races and then
measures survivors passes on an idle machine and fails on a loaded one. Force
the interleaving with markers, assert the collision occurred, *then* assert the
outcome. `tests/test_ledger_locks.py` is the reference.

Every test gets its own `TemporaryDirectory`. A guard in `tests/support.py`
fails loudly if a test writes into this checkout's own `.ctx/` — it exists
because one did.

## Commits and pull requests

Explain **why**, not what — the diff already says what. If you changed a test's
assertion, say what it was pinning and why that was wrong. If you found
something you did not fix, say so; a reported gap is worth more than a quiet
one.

Branch protection requires review from a code owner (`CODEOWNERS`) and all
checks green.

## Security

Do not open an issue for a vulnerability. `SECURITY.md` has the private
reporting channel.
