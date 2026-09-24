"""``file.py:NNN`` citations that no longer point where they say.

A citation of a source line rots on every edit above it. A sweep of one package found 183 of 185 log-message
citations pointing at the wrong line. Three rules, all SELF-VERIFYING (a correct citation passes by construction, so no
allowlist judgement is needed):

* ``self-citation``: a string literal in code (not a docstring) that cites ITS OWN file (``"suppressed in _mah.py:118"``)
  with a line outside the statement that holds it. ``logging`` already records the true location, so the fix is to delete the
  ``file:line`` fragment.
* ``line-past-end``: any comment or string citing ``path.py:N`` (its own file, or a path that resolves to exactly one
  file in the corpus) where ``N`` is past the end of that file. Exact: the line cannot exist.
* ``symbol-not-at-line``: a comment, docstring or string that cites ``path.py:N`` next to a symbol (```sym`` (a.py:12)``,
  ``a.py:12 `sym```, ``sym() at a.py:12``, ``a.py:12 (sym)``), where the path resolves to exactly one file in the corpus
  and ``sym`` does not appear within :data:`WINDOW` lines of ``N`` (or ``N`` is past the end of the file). A citation
  whose path matches no file or several files is not judged (pyutilz's ``stale_source_citation`` reports vanished
  files); a bare ``file:line`` with no symbol is not judged either: nothing says what should be there.

Fix by citing the symbol instead of the line (``see `_mah._fit` ``), which ``phantom_code_references`` then validates.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ParsedFile, ScanResult, scan_python
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "WINDOW", "assert_no_stale_source_citations", "find_stale_source_citations"]

REFRESH_FLAG = "--refresh-stale-source-citations-baseline"
GATE = "stale-source-citations"
RULE_SELF = "self-citation"
RULE_SYMBOL = "symbol-not-at-line"
RULE_PAST_END = "line-past-end"
#: How far from the cited line the symbol may be (a citation of a def's decorator or docstring is still right).
WINDOW = 3
_PATH = r"(?P<path>[A-Za-z_][\w./\\-]*\.py):(?P<line>\d+)(?:-(?P<end>\d+))?"
_SYM = r"(?P<sym>[A-Za-z_][\w.]*)"
_CITATIONS = [
    re.compile(r"`" + _SYM + r"(?:\(\))?`\s*\(?\s*(?:in|at|@)?\s*" + _PATH),
    re.compile(_PATH + r"\s*\(?\s*`" + _SYM + r"(?:\(\))?`"),
    re.compile(_SYM + r"\(\)\s+(?:in|at|@)\s+" + _PATH),
    re.compile(_PATH + r"\s*\(\s*" + _SYM + r"(?:\(\))?\s*\)"),
]
_ANY_CITATION = re.compile(r"(?P<path>[A-Za-z_][\w./-]*\.py):(?P<line>\d+)")


def _texts(f: ParsedFile) -> Iterable[tuple[int, int, str, bool]]:
    """``(start_line, end_line, text, is_code_string)`` for every comment and string token of the file."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(f.source).readline))
    except (tokenize.TokenError, SyntaxError):
        return
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.end[0], tok.string, False
        elif tok.type == tokenize.STRING or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            yield tok.start[0], tok.end[0], tok.string, True


def _statement_span(f: ParsedFile, line: int) -> tuple[int, int]:
    """The smallest statement containing *line* (so a multi-line call's literal may cite the call's first line)."""
    import ast

    best = (line, line)
    for node in ast.walk(f.tree):
        if isinstance(node, ast.stmt) and node.lineno <= line <= (node.end_lineno or node.lineno):
            span = (node.lineno, node.end_lineno or node.lineno)
            if best == (line, line) or span[1] - span[0] < best[1] - best[0]:
                best = span
    return best


def _docstring_lines(f: ParsedFile) -> set[int]:
    import ast

    out: set[int] = set()
    for node in ast.walk(f.tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def _self_citations(f: ParsedFile) -> list[Finding]:
    own = f.path.name
    docstrings = _docstring_lines(f)
    out: list[Finding] = []
    for start, end, text, is_code in _texts(f):
        if not is_code or start in docstrings:
            continue
        for m in _ANY_CITATION.finditer(text):
            if m.group("path").replace("\\", "/").rsplit("/", 1)[-1] != own:
                continue
            cited = int(m.group("line"))
            lo, hi = _statement_span(f, start)
            if not (min(lo, start) <= cited <= max(hi, end)):
                message = f"a string cites its own file `{m.group(0)}` but sits on line {start}; drop the fragment (logging records the location)"
                out.append(Finding(f.rel, start, RULE_SELF, message, key=f"{RULE_SELF}::{f.rel}::{m.group(0)}"))
    return out


def _top_package(rel: str) -> str:
    """The first directory of a corpus-relative path; ``""`` for a file at the corpus root."""
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else ""


class _Index:
    """Parsed files by name and by relative path, for resolving a cited path to exactly one file."""

    def __init__(self, scan: ScanResult) -> None:
        self.by_name: dict[str, list[ParsedFile]] = {}
        for f in scan:
            self.by_name.setdefault(f.path.name, []).append(f)

    def resolve(self, cited: str, citing: Optional[ParsedFile] = None) -> Optional[ParsedFile]:
        """The one corpus file *cited* names. A bare name (``core.py``) must also share its top-level package with the
        citing file: ``core.py:2992`` in a strategy module more likely means a library's ``core.py`` than ours."""
        cited = cited.replace("\\", "/")
        candidates = [f for f in self.by_name.get(cited.rsplit("/", 1)[-1], []) if f.rel.endswith(cited) or "/" not in cited]
        if len(candidates) != 1:
            return None
        target = candidates[0]
        if "/" not in cited and citing is not None and _top_package(target.rel) != _top_package(citing.rel):
            return None
        return target


def _symbol_findings(f: ParsedFile, index: _Index) -> list[Finding]:
    out: list[Finding] = []
    for start, _end, text, _is_code in _texts(f):
        for pattern in _CITATIONS:
            for m in pattern.finditer(text):
                target = index.resolve(m.group("path"), f)
                if target is None:
                    continue
                line = int(m.group("line"))
                lines = target.source.splitlines()
                leaf = m.group("sym").rsplit(".", 1)[-1]
                window = lines[max(0, line - 1 - WINDOW) : line + WINDOW]
                if line <= len(lines) and any(re.search(r"\b" + re.escape(leaf) + r"\b", ln) for ln in window):
                    continue
                where = "past the end of the file" if line > len(lines) else f"not within {WINDOW} lines of it"
                message = f"cites `{m.group('sym')}` at {m.group('path')}:{line}, but it is {where}; cite the symbol, not the line"
                out.append(Finding(f.rel, start, RULE_SYMBOL, message, key=f"{RULE_SYMBOL}::{f.rel}::{m.group('sym')}@{m.group('path')}:{line}"))
    return out


def _past_end_findings(f: ParsedFile, index: _Index) -> list[Finding]:
    out: list[Finding] = []
    for start, _end, text, _is_code in _texts(f):
        for m in _ANY_CITATION.finditer(text):
            cited = m.group("path")
            target = f if cited.replace("\\", "/").rsplit("/", 1)[-1] == f.path.name and "/" not in cited else index.resolve(cited, f)
            if target is None:
                continue
            n_lines = len(target.source.splitlines())
            if int(m.group("line")) > n_lines:
                message = f"cites {m.group(0)}, but {target.rel} has {n_lines} lines; cite the symbol, not the line"
                out.append(Finding(f.rel, start, RULE_PAST_END, message, key=f"{RULE_PAST_END}::{f.rel}::{m.group(0)}"))
    return out


def find_stale_source_citations(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = (),
    rules: Iterable[str] = (RULE_SELF, RULE_PAST_END, RULE_SYMBOL),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)``; cited paths are resolved against the same corpus."""
    wanted = set(rules)
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    index = _Index(scan)
    findings: list[Finding] = []
    for f in scan:
        if RULE_SELF in wanted:
            findings.extend(_self_citations(f))
        if RULE_PAST_END in wanted:
            findings.extend(_past_end_findings(f, index))
        if RULE_SYMBOL in wanted:
            findings.extend(_symbol_findings(f, index))
    unique = {(x.path, x.line, x.key): x for x in findings}
    return sorted(unique.values(), key=lambda x: (x.path, x.line, x.rule)), scan


def assert_no_stale_source_citations(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = (),
    rules: Iterable[str] = (RULE_SELF, RULE_PAST_END, RULE_SYMBOL),
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a stale citation not accepted by *baseline_path*. Missing baseline fails; refresh with ``REFRESH_FLAG``
    or ``PY_CI_SHARED_REFRESH=stale-source-citations``."""
    findings, scan = find_stale_source_citations(root, exclude_parts=exclude_parts, rules=rules, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="cite the symbol (`module.function`) instead of a line number, or delete a self-citation from a log message",
    )
