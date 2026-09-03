"""A strict, tiny YAML subset — enough for ctx.yaml and file frontmatter.

Deliberately not PyYAML: the plugin promises stdlib-only so it adds nothing to
the host project's dependency tree. The subset is:

    key: scalar
    key:
      nested: scalar
    key: [inline, list]
    key:
      - item
      - nested: map
        over: lines

Scalars are int, float, bool, null, or string (optionally quoted). Anything
outside the subset raises MiniYamlError rather than guessing, so a malformed
config fails loudly at init instead of silently at runtime.
"""

import re

__all__ = ["loads", "dumps", "MiniYamlError"]


class MiniYamlError(ValueError):
    pass


# A key is an identifier-ish token followed by a colon and then whitespace or
# end-of-line. Requiring the whitespace is what keeps `- https://example.com`
# and `run: pytest -q --tb=short` from being mistaken for nested maps. Leading
# digits are allowed so numeric keys (`0: 220`, `2026-08-17: …`) round-trip.
_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?:\s+(.*))?$")
_QUOTED = re.compile(r"""^(['"])(.*)\1$""", re.S)


def loads(text):
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise MiniYamlError(f"line {lineno}: tab indentation is not allowed")
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped, lineno))
    if not lines:
        return {}
    value, index = _block(lines, 0, lines[0][0])
    if index != len(lines):
        raise MiniYamlError(f"line {lines[index][2]}: unexpected indentation")
    return value


def _block(lines, i, indent):
    if lines[i][1].startswith("- "):
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent)


def _sequence(lines, i, indent):
    out = []
    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
        item, lineno = lines[i][1][2:].strip(), lines[i][2]
        i += 1
        match = _KEY.match(item)
        if match and not _QUOTED.match(item):
            entry = {}
            key, inline = match.group(1), (match.group(2) or "").strip()
            if inline:
                entry[key] = _scalar(inline, lineno)
            # An item's own continuation lines sit deeper than the dash.
            if i < len(lines) and lines[i][0] > indent:
                nested, i = _block(lines, i, lines[i][0])
                if not isinstance(nested, dict):
                    raise MiniYamlError(f"line {lineno}: expected mapping keys")
                if not inline:
                    entry[key] = nested.pop(key, None) if key in nested else None
                entry.update(nested)
            out.append(entry)
        else:
            out.append(_scalar(item, lineno))
    return out, i


def _mapping(lines, i, indent):
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        raw, lineno = lines[i][1], lines[i][2]
        if raw.startswith("- "):
            raise MiniYamlError(f"line {lineno}: list item where a key was expected")
        match = _KEY.match(raw)
        if not match:
            raise MiniYamlError(f"line {lineno}: cannot parse {raw!r}")
        key, inline = match.group(1), (match.group(2) or "").strip()
        i += 1
        if inline:
            out[key] = _scalar(inline, lineno)
        elif i < len(lines) and lines[i][0] > indent:
            out[key], i = _block(lines, i, lines[i][0])
        else:
            out[key] = None
    return out, i


def _scalar(text, lineno):
    quoted = _QUOTED.match(text)
    if quoted:
        return _unescape(quoted.group(2))
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p, lineno) for p in _split_items(inner)]
    if text.startswith("{"):
        raise MiniYamlError(f"line {lineno}: inline maps are not supported")
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _unescape(text):
    r"""Undo what `_emit` did to a quoted scalar.

    Without this the writer and the reader disagree: `_emit` escapes `"` as `\"`
    but nothing ever put it back, so every save/load cycle added another
    backslash. Unit frontmatter is rewritten on each status change, so the
    damage compounded rather than staying put.
    """
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "\"'\\":
            out.append(text[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _split_items(text):
    """Split an inline list on commas that are not inside a quoted item."""
    items, current, quote, escaped = [], [], "", False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif quote:
            current.append(char)
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return items


def dumps(value, indent=0):
    """Round-trip the same subset. Used to write frontmatter back after edits."""
    pad = "  " * indent
    out = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict) and item:
                out.append(f"{pad}{key}:")
                out.append(dumps(item, indent + 1))
            elif isinstance(item, list) and item:
                out.append(f"{pad}{key}:")
                out.append(dumps(item, indent + 1))
            elif isinstance(item, (dict, list)):
                out.append(f"{pad}{key}: []" if isinstance(item, list) else f"{pad}{key}:")
            else:
                out.append(f"{pad}{key}: {_emit(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                keys = list(item.items())
                first, rest = keys[0], keys[1:]
                out.append(f"{pad}- {first[0]}: {_emit(first[1])}")
                for key, sub in rest:
                    out.append(f"{pad}  {key}: {_emit(sub)}")
            else:
                out.append(f"{pad}- {_emit(item)}")
    else:
        out.append(f"{pad}{_emit(value)}")
    return "\n".join(out)


def _emit(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or text.strip() != text or _needs_quotes(text):
        # Backslash first, or escaping the quote would produce a backslash the
        # reader then eats as an escape of its own.
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % escaped
    return text


def _needs_quotes(text):
    if text[0] in "-[{#'\"":
        return True
    return text.lower() in ("true", "false", "yes", "no", "null", "~")
