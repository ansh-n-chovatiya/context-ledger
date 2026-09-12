## What this changes, and why

The diff says what. Say why — especially what was wrong before.

## Evidence

- [ ] The suite passes: `python3 -m unittest discover -s tests -q`
- [ ] `ruff check ctx/ tests/` is clean
- [ ] `ctx doctor` and `ctx ci` exit 0
- [ ] Any new test was **seen failing** before it passed — paste both runs

## Tests you changed rather than added

If you edited an existing assertion, say what it was pinning and why that was
wrong. An assertion changed to accommodate a diff is how a guard quietly stops
guarding; several in this repo were doing exactly that.

## Anything you found and did not fix

A reported gap is worth more than a silent one. This includes blind spots in
what you added: a check that cannot see something should say so.
