"""Source-text helpers of :mod:`py_ci_shared.dart_scanners`: comment stripping, string masking, bracket matching and
the finding-key scheme. Private; the scanners re-export nothing from here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _key(found: dict, rel: str) -> str:
    """A provisional, unique key while a scanner collects; :func:`_rekey` turns it into the stable one."""
    return f"{rel}#{len(found)}"


_LINE_REF_RE = re.compile(r"\(line \d+\)")


def _rekey(found: dict) -> dict:
    """``{rel#<hash of the finding>[#n]: description}``: the ratchet sees WHAT was found, not only how many.

    A key built from an ordinal alone let a fixed finding be swapped for a new one in the same file with no change
    to the key set. The hash covers the description minus its ``(line N)``, and the description names the matched
    text where the rule has one, so an edit above a finding keeps its key while a changed finding gets a new one.
    """
    out: dict[str, str] = {}
    seen: dict[str, int] = {}
    for key, description in found.items():
        rel = key.rsplit("#", 1)[0]
        digest = hashlib.sha1(_LINE_REF_RE.sub("", description).encode("utf-8")).hexdigest()[:10]
        base = f"{rel}#{digest}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out[base if n == 0 else f"{base}#{n}"] = description
    return out


def _strip_comments(source: str) -> str:
    """*source* with ``//`` and (nestable) ``/* */`` comments removed, string literals left intact.

    A regex strip ran inside strings, so ``Text('Visit https://x.com now')`` lost the rest of its line and hid any
    finding on it. Newlines inside a removed block comment are kept, so line numbers still match the file.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end == -1 else end
            continue
        if source.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if source.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif source.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append("\n" * source.count("\n", i, j))
            i = j
            continue
        if c in "'\"":
            j = _string_end(source, i)
            out.append(source[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _string_end(source: str, i: int) -> int:
    """Index just past the Dart string literal opening at *i* (single/double, triple, raw ``r'...'``)."""
    raw = i > 0 and source[i - 1] == "r" and (i < 2 or not (source[i - 2].isalnum() or source[i - 2] == "_"))
    quote = source[i] * 3 if source.startswith(source[i] * 3, i) else source[i]
    j = i + len(quote)
    n = len(source)
    while j < n:
        if not raw and source[j] == "\\":
            j += 2
            continue
        if source.startswith(quote, j):
            return j + len(quote)
        if len(quote) == 1 and source[j] == "\n":
            return j
        if not raw and source.startswith("${", j):
            depth, j = 1, j + 2
            while j < n and depth:
                if source[j] in "'\"":
                    j = _string_end(source, j)
                    continue
                depth += {"{": 1, "}": -1}.get(source[j], 0)
                j += 1
            continue
        j += 1
    return n


def _balanced_body(source: str, open_index: int, opener: str = "{", closer: str = "}") -> str:
    """Return the text between ``open_index`` (at an opener) and its matching closer."""
    depth = 0
    i = open_index
    while i < len(source):
        if source[i] == opener:
            depth += 1
        elif source[i] == closer:
            depth -= 1
            if depth == 0:
                return source[open_index + 1 : i]
        i += 1
    return source[open_index + 1 :]


def _mask_strings(source: str) -> str:
    """*source* with every string literal's contents blanked to spaces, so brackets inside text never count as code."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c in "'\"":
            end = _string_end(source, i)
            out.append("".join(ch if ch == "\n" else " " for ch in source[i:end]))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


_CALL_NAME = re.compile(r"([A-Za-z_]\w*)(?:\.[A-Za-z_]\w*)?\s*$")


def _enclosing_calls(masked: str, pos: int) -> Iterator[tuple[str, int]]:
    """``(callee, open_paren_index)`` of every call whose argument list contains *pos*, innermost first.

    Walks outwards through bracket nesting on string-masked source, so a label 1000 characters above a widget still
    counts when it sits on the widget's own ancestor, and a label on a sibling never does.
    """
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = masked[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth:
                depth -= 1
            elif ch == "(":
                m = _CALL_NAME.search(masked, max(0, i - 80), i)
                if m:
                    yield m.group(1), i


def _top_level_args(masked: str, open_index: int) -> str:
    """The argument text of the call opening at *open_index* with every nested bracket's contents removed."""
    body = _balanced_body(masked, open_index, "(", ")")
    out: list[str] = []
    depth = 0
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _enclosing_semantics_args(masked: str, pos: int) -> Iterator[str]:
    """Top-level argument text of every ``Semantics(`` that encloses *pos*."""
    for name, open_index in _enclosing_calls(masked, pos):
        if name == "Semantics":
            yield _top_level_args(masked, open_index)
