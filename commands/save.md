---
description: Save current understanding as a portable context bundle
allowed-tools: Bash
argument-hint: «name» [--tag x]
---
Compose a context bundle for `$1` and write it.

Use exactly these sections in this order. The fixed schema is what lets another
session, another person, or a different model entirely pick the file up:

`## Situation` · `## Established facts` · `## Decisions made` · `## Open questions`
· `## Constraints` · `## Artifacts` · `## Resume here`

Rules that matter:
- **Established facts** are things you verified this session. A confident wrong
  fact in a bundle is worse than an absent one — if you inferred it, leave it out
  or mark it as an assumption.
- Prefer paths and diff ranges over pasted file contents.
- Open questions are `- [ ]` checkboxes.
- **Resume here** is one concrete next action, not a summary.

Then save it, substituting the real body:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/ctx" save $ARGUMENTS --stdin <<'CTXEOF'
# Context — <name>

## Situation
…
CTXEOF
```

Secrets are scrubbed on write, but do not put them there in the first place.
