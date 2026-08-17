"""Secret scrubbing for anything that reaches the journal or a context bundle.

Bundles are designed to be pasted into other tools and committed to git, so
redaction runs on the write path, not the read path — a secret that reaches
disk has already leaked.

The entropy heuristic deliberately skips pure hex: a 40-character git SHA is
high-entropy and completely safe, and redacting commit ids would make the
journal useless.
"""

import math
import re

PLACEHOLDER = "<<redacted>>"

_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?keys?|secrets?|tokens?|passwo?r?d|passwd|auth|bearer|"
    r"client[_-]?secret|private[_-]?key)\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)"
)
_PREFIXED = re.compile(
    r"\b(sk|pk|rk|ghp|gho|ghu|ghs|glpat|xoxb|xoxp|xoxa|shpat|npm)[-_]"
    r"[A-Za-z0-9_\-]{12,}\b"
)
_AWS = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\b")
_PEM = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")

_BUILTIN = (_PREFIXED, _AWS, _JWT, _PEM)


def scrub(text, extra_patterns=()):
    """Return `text` with anything that looks like a credential removed."""
    if not text:
        return text
    out = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}={PLACEHOLDER}", text)
    for pattern in _BUILTIN:
        out = pattern.sub(PLACEHOLDER, out)
    for raw in extra_patterns or ():
        try:
            out = re.sub(raw, PLACEHOLDER, out)
        except re.error:
            continue  # a bad user pattern must not break the write path
    return _CANDIDATE.sub(_maybe_entropy, out)


def _maybe_entropy(match):
    token = match.group(0)
    if _HEX.match(token):
        return token  # git SHAs, checksums, colour hexes
    if token.count("-") >= 4 and len(token) <= 36:
        return token  # UUIDs
    classes = sum(
        bool(re.search(pattern, token))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[+/=_\-]")
    )
    if classes < 3:
        return token
    return PLACEHOLDER if _entropy(token) >= 3.6 else token


def _entropy(text):
    total = len(text)
    counts = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    return -sum(
        (count / total) * math.log2(count / total) for count in counts.values()
    )
