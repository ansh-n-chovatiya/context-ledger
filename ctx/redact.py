"""Secret scrubbing for anything that reaches the journal or a context bundle.

Bundles are designed to be pasted into other tools and committed to git, so
redaction runs on the write path, not the read path — a secret that reaches
disk has already leaked.

Running on the write path also makes every mistake permanent: the original text
never reaches disk, so a false positive destroys content nothing can recover.
That asymmetry is why this module only matches shapes it can *name*. It used to
score Shannon entropy over any 24-character run of `[A-Za-z0-9+/=_-]`, which
included `/` and `-`, so `src/components/dashboard/widgets/Panel.tsx` scored as
high as an API key and was replaced by `<<redacted>>.tsx` in the journal the
next SessionStart read back — while `postgres://admin:hunter2@host/db` sailed
through, because entropy measures character variety and a password made of
words has little of it. Length and variety describe source paths at least as
well as they describe credentials, so entropy scoring is gone rather than
tuned.

Every pattern below is anchored on something a human can point at: a vendor
prefix, a URL structure, a PEM header, or a secret-ish key immediately followed
by an assignment. Precision is preferred to recall on purpose — a credential
this misses is a risk, but a file path it eats is a certainty.
"""

import re

PLACEHOLDER = "<<redacted>>"

# Vendor-prefixed tokens: the prefix *is* the evidence, so no length or entropy
# test is needed. Covers OpenAI/Anthropic/Stripe `sk-`/`pk-`/`rk-`, GitHub
# `ghp_`/`gho_`/`ghu_`/`ghs_`, GitLab `glpat-`, Slack `xox*`, Shopify `shpat_`.
_PREFIXED = re.compile(
    r"\b(sk|pk|rk|ghp|gho|ghu|ghs|ghr|glpat|xoxb|xoxp|xoxa|xoxs|shpat|npm)[-_]"
    r"[A-Za-z0-9_\-]{12,}\b"
)
_AWS = re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")
_GOOGLE = re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\b")

# A PEM block is redacted whole. Replacing only the header would leave the key
# material — the part that matters — sitting in the file underneath it.
_PEM_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)
_PEM_HEADER = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")

# Basic-auth credentials in a URL — `postgres://user:pass@host/db`, and the
# same shape for https, redis, mongodb, amqp. The `/` and `@` exclusions keep
# `https://host:8080/path` (a port, not a password) out of it.
_URL_CREDENTIALS = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+:(?P<value>[^\s:/@]+)@"
)
# A Slack webhook URL is a bearer credential with no keyword anywhere near it.
_SLACK_WEBHOOK = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/+_\-]+")
_AUTH_HEADER = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic|token|digest)\s+(?P<value>\S+)"
)
# `mysql -pSecret` — the shell form with no separator. Anchored on the client
# name because a bare `-p<something>` is far more often `-print` or a flag.
_MYSQL_PASSWORD = re.compile(
    r"(?i)\b(?:mysql|mysqladmin|mysqldump|mysqlshow)\b[^\n]*?\s-p(?P<value>\S+)"
)
# Twilio: an Account SID or API key SID, optionally paired with its auth token.
# Matched before `_HEX32` so the pair is removed as one unit.
_TWILIO = re.compile(r"\b(?:AC|SK)[0-9a-fA-F]{32}\b(?::[0-9a-fA-F]{32}\b)?")
# Exactly 32 hex characters: the shape of most API secrets that carry no prefix
# at all. The word boundaries are what make this safe — a 40-character git SHA,
# a 64-character sha256 or a docker digest has no boundary at 32, so none of
# them match, and no shorter id in this project is 32 wide.
_HEX32 = re.compile(r"\b[0-9a-fA-F]{32}\b")

# A key whose *name* claims it holds a secret, followed by an assignment. The
# name may be qualified (`AWS_SECRET_ACCESS_KEY`) and may be quoted, which is
# what `{"password": "x"}` needs: the old pattern required the keyword to touch
# the separator, so a JSON key defeated it. `auth` is deliberately absent —
# `auth: none` and `auth: cookie-based` are prose, and `_AUTH_HEADER` covers
# the header that actually carries a credential.
_KEYWORD = (
    r"(?:api[_-]?key|secret|token|passwo?rd|passwd|pwd|credential|"
    r"private[_-]?key|access[_-]?key|auth[_-]?token)"
)
_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"[\"']?[A-Za-z0-9_.\-]*" + _KEYWORD + r"[A-Za-z0-9_.\-]*[\"']?"
    r"\s*[:=]\s*"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&\"'\n]+)"
)


def _mask_value(match):
    """Replace only the `value` group, leaving the syntax around it intact.

    The old code substituted `f"{key}={PLACEHOLDER}"`, which rewrote the
    separator: `auth: cookie-based` came back as `auth=<<redacted>>`, corrupting
    YAML and prose in documents humans read and paste.
    """
    whole, base = match.group(0), match.start(0)
    value = match.group("value")
    masked = PLACEHOLDER
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        # Keep the quotes so a redacted JSON or YAML document still parses.
        masked = value[0] + PLACEHOLDER + value[-1]
    return (
        whole[: match.start("value") - base]
        + masked
        + whole[match.end("value") - base:]
    )


# Order matters: the widest, most specific shapes first, so a credential is
# removed as one unit rather than in pieces by a later pattern.
_BUILTIN = (
    (_PEM_BLOCK, PLACEHOLDER),
    (_PEM_HEADER, PLACEHOLDER),
    (_SLACK_WEBHOOK, PLACEHOLDER),
    (_URL_CREDENTIALS, _mask_value),
    (_AUTH_HEADER, _mask_value),
    (_JWT, PLACEHOLDER),
    (_PREFIXED, PLACEHOLDER),
    (_AWS, PLACEHOLDER),
    (_GOOGLE, PLACEHOLDER),
    (_TWILIO, PLACEHOLDER),
    (_HEX32, PLACEHOLDER),
    (_MYSQL_PASSWORD, _mask_value),
    (_ASSIGNMENT, _mask_value),
)


def scrub(text, extra_patterns=()):
    """Return `text` with anything that looks like a credential removed."""
    if not text:
        return text
    out = text
    for pattern, replacement in _BUILTIN:
        out = pattern.sub(replacement, out)
    for raw in extra_patterns or ():
        try:
            out = re.sub(raw, PLACEHOLDER, out)
        except re.error:
            continue  # a bad user pattern must not break the write path
    return out
