"""Shared check: every save-failure marker a writer emits is one the success decision recognises.

WHERE THIS CAME FROM
--------------------
glossum's savers append ``"<name>_failed: ..."`` markers to a result's error list, and a separate list of
fatal prefixes decides whether the run is stamped successful. The two lived in different files with nothing
tying them together, and they drifted twice: markers the savers appended matched no fatal prefix, so a whole
category that failed to save still produced a green status row. When the first check was written, eleven
markers were unrecognised.

WHAT THIS CHECKS
----------------
:func:`find_emitted_markers` finds the markers the code emits. By default it reads the AST: a string or f-string
starting with ``<name>_failed:`` handed to ``.append``/``.extend``/``.insert``, added with ``+=``, or a
``marker="<name>_failed"`` keyword or assignment; any string prefix (``rf"..."``, ``F"..."``) is fine and comments
and docstrings never count. A marker whose name is itself interpolated (``f"{kind}_failed: ..."``) is reported
under its template (``{kind}_failed``): it cannot be checked, so it must be listed in ``non_fatal`` with a reason.
Caller-supplied regex ``patterns`` still work; they run over the source with comments blanked out.
:func:`assert_markers_are_fatal` fails for a marker the ``is_fatal`` predicate does not accept unless it is in
``non_fatal`` with a reason, for a ``non_fatal`` entry that is actually fatal, for a ``non_fatal`` entry no source
emits any more, for fewer than ``min_markers`` markers found (a wrong or missing root), and for a file that cannot
be read or parsed.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from re import Pattern
from typing import Callable, Optional

from ._core import DEFAULT_EXCLUDE, ParsedFile, ScanResult, scan_python

__all__ = ["DEFAULT_MARKER_PATTERNS", "assert_markers_are_fatal", "find_emitted_markers"]

_PREFIX = r"""(?:[rRbBuUfF]{0,2})"""
#: Regex form of the default detection, for callers who pass patterns explicitly. The AST default is stricter.
DEFAULT_MARKER_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(rf"""\.(?:append|insert|extend)\(\s*(?:\d+\s*,\s*)?\[?\s*{_PREFIX}["']([a-z0-9_]+_failed):"""),
    re.compile(rf"""\+=\s*[\[(]\s*{_PREFIX}["']([a-z0-9_]+_failed):"""),
    re.compile(rf"""marker\s*=\s*{_PREFIX}["']([a-z0-9_]+_failed)["']"""),
)
_SKIP_DIRS = DEFAULT_EXCLUDE
_MARKER_RE = re.compile(r"^([a-z0-9_]+_failed):")
_NAME_RE = re.compile(r"^[a-z0-9_]+_failed$")
_TEMPLATE_RE = re.compile(r"^([a-z0-9_{}.\[\]'\"()]*_failed):")
_ADDERS = frozenset({"append", "extend", "insert", "appendleft", "add"})


def _leading_text(node: ast.expr) -> Optional[str]:
    """The literal text a string node starts with; an f-string's placeholders become ``{expr}``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                out.append("{" + ast.unparse(part.value) + "}")
            if ":" in "".join(out):
                break
        return "".join(out)
    return None


def _marker_of(node: ast.expr) -> Optional[str]:
    text = _leading_text(node)
    if text is None:
        return None
    m = _MARKER_RE.match(text) or _TEMPLATE_RE.match(text)
    return m.group(1) if m else None


def _elements(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return [node]


def _ast_markers(parsed: ParsedFile) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []

    def emit(node: ast.expr, name_only: bool = False) -> None:
        if name_only:
            text = _leading_text(node)
            if text is not None and (_NAME_RE.match(text) or (text.endswith("_failed") and "{" in text)):
                out.append((text, node.lineno))
            return
        name = _marker_of(node)
        if name:
            out.append((name, node.lineno))

    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in _ADDERS:
                for arg in node.args:
                    for elt in _elements(arg):
                        emit(elt)
            for kw in node.keywords:
                if kw.arg == "marker":
                    emit(kw.value, name_only=True)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            for elt in _elements(node.value):
                emit(elt)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any(isinstance(t, ast.Name) and t.id == "marker" for t in targets):
                emit(node.value, name_only=True)
    return out


def _code_without_comments(source: str) -> str:
    """*source* with every comment replaced by spaces, so a commented-out append is not a marker."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source
    lines = source.splitlines(keepends=True)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            line = lines[row - 1]
            end = col + len(tok.string)
            lines[row - 1] = line[:col] + " " * (end - col) + line[end:]
    return "".join(lines)


def _regex_markers(parsed: ParsedFile, patterns: Sequence[Pattern[str]]) -> list[tuple[str, int]]:
    src = _code_without_comments(parsed.source)
    return [(m.group(1), src.count("\n", 0, m.start()) + 1) for pattern in patterns for m in pattern.finditer(src)]


def _scan(root: Path, glob: str) -> ScanResult:
    return scan_python(root, patterns=(glob,), exclude=_SKIP_DIRS)


def _markers(scan: ScanResult, patterns: Optional[Sequence[Pattern[str]]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for parsed in scan:
        found = _ast_markers(parsed) if patterns is None else _regex_markers(parsed, patterns)
        for name, line in sorted(found, key=lambda t: t[1]):
            out.setdefault(name, []).append(f"{parsed.rel}:{line}")
    return out


def find_emitted_markers(
    root: Path,
    patterns: Optional[Sequence[Pattern[str]]] = None,
    *,
    glob: str = "*.py",
    allow_unparsed: bool = False,
) -> dict[str, list[str]]:
    """``{marker: ["relative/path.py:line", ...]}`` for every marker emitted under ``root``.

    ``patterns=None`` (the default) reads the AST; a sequence of regexes (group 1 = the marker) is applied to the
    source with comments blanked. A missing *root* raises ``CorpusError``; a file that cannot be read or parsed
    raises ``UnparsedFilesError`` (an ``AssertionError``) unless *allow_unparsed*.
    """
    scan = _scan(Path(root), glob)
    if not allow_unparsed:
        scan.check_unparsed()
    return _markers(scan, patterns)


def assert_markers_are_fatal(
    root: Path,
    is_fatal: Callable[[str], bool],
    *,
    non_fatal: Optional[Mapping[str, str]] = None,
    patterns: Optional[Sequence[Pattern[str]]] = None,
    probe: Callable[[str], str] = lambda name: f"{name}: probe",
    extra_markers: Iterable[str] = (),
    min_markers: int = 1,
    glob: str = "*.py",
) -> None:
    """Fail unless every emitted marker is fatal per ``is_fatal`` or listed in ``non_fatal`` with a reason.

    ``probe`` turns a marker name into the error string the predicate is asked about, so a predicate keyed
    on a prefix, a separator or a whole-line format can be tested the way the writer really emits it. Fewer
    than ``min_markers`` markers found, or a file that cannot be parsed, fails too.
    """
    non_fatal = dict(non_fatal or {})
    scan = _scan(Path(root), glob)
    markers = _markers(scan, patterns)
    found_count = len(markers)
    for name in extra_markers:
        markers.setdefault(name, ["<extra>"])
    blank = [n for n, reason in non_fatal.items() if not reason.strip()]
    dynamic = {n: sites for n, sites in markers.items() if "{" in n and n not in non_fatal}
    unrecognised = {n: sites for n, sites in markers.items() if n not in non_fatal and n not in dynamic and not is_fatal(probe(n))}
    wrongly_listed = [n for n in non_fatal if "{" not in n and is_fatal(probe(n))]
    stale = sorted(set(non_fatal) - set(markers))
    messages = []
    if scan.unparsed:
        messages.append("files that could not be read or parsed, so their markers are unknown:\n  " + "\n  ".join(p.render() for p in scan.unparsed))
    if found_count < min_markers:
        messages.append(
            f"found {found_count} emitted marker(s) under {root}, expected at least {min_markers}: the scan is not reaching the savers "
            "(wrong root or glob, or the writers changed shape)."
        )
    if blank:
        messages.append(f"every non-fatal marker needs a reason: {blank}")
    if dynamic:
        messages.append(
            f"these markers build their NAME at runtime, so no predicate can be checked against them: {dynamic}. Emit a literal name, "
            "or list the template in non_fatal with the reason it is safe."
        )
    if unrecognised:
        messages.append(
            "these save-failure markers are emitted but the success decision does not treat them as fatal, so a "
            f"failed save still reads as success: {unrecognised}. Make them fatal, or list them as non-fatal with the reason."
        )
    if wrongly_listed:
        messages.append(f"these are listed as non-fatal but the predicate treats them as fatal; drop them from the list: {wrongly_listed}")
    if stale:
        messages.append(f"these non-fatal entries match no emitted marker; a stale exemption is a blind spot waiting for the next marker of that name: {stale}")
    if messages:
        raise AssertionError("\n".join(messages))
