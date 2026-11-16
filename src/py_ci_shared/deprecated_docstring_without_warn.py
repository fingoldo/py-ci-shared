"""A public function documented as deprecated must warn at call time, or carry a deprecated decorator.

A docstring-only notice ("Deprecated alias of :func:`new_name`") is invisible to IDEs, to ``-W error::DeprecationWarning``
sweeps and to callers, so a removal surprises them. Found in pyutilz ``pandaslib.frames.remove_stale_columns``, a
deprecated alias that never warned.

A function is checked when it is public (its name, and the name of every enclosing class, does not start with ``_``)
and the first paragraph of its docstring says it is deprecated: the word ``deprecat...`` outside a dotted name
(``span.deprecated`` is an attribute, not a notice) and outside inline literals. It is accepted when its body calls
``warnings.warn`` or ``warnings.warn_explicit`` (resolved through the module's imports, so ``from warnings import warn``
counts), or when a decorator's last name is ``deprecated`` (``typing_extensions.deprecated``, ``warnings.deprecated``).

Accepted deliberately with ``# deprecated-ok: <reason>`` on the ``def`` line or the line above. A marker with no reason is
itself a finding.

Usage::

    from py_ci_shared.deprecated_docstring_without_warn import assert_deprecated_docstrings_warn

    def test_deprecations_warn():
        assert_deprecated_docstrings_warn("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ImportAliases
from ._core import walk as shared_walk
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_deprecated_docstrings_warn", "find_deprecated_docstrings_without_warn"]

RULE = "deprecated-docstring-without-warn"
MARKER = "deprecated-ok"
_DEPRECATED = re.compile(r"(?<![\w.])deprecat\w*", re.IGNORECASE)
_INLINE_LITERAL = re.compile(r"``[^`]*``|`[^`]*`")
_WARNERS = frozenset({"warnings.warn", "warnings.warn_explicit"})


_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _functions(body: Sequence[ast.stmt], public: bool, prefix: str = "") -> Iterator[tuple[_FunctionNode, str, bool]]:
    """``(function node, dotted name, public)`` for each module-level function and each method of a class body.
    Nested functions are not part of the public surface and are not yielded."""
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            yield from _functions(stmt.body, public and not stmt.name.startswith("_"), f"{prefix}{stmt.name}.")
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield stmt, f"{prefix}{stmt.name}", public and not stmt.name.startswith("_")


def _claims_deprecation(fn: _FunctionNode) -> bool:
    """The first paragraph of the docstring says the function is deprecated (inline literals and dotted names excluded)."""
    doc = ast.get_docstring(fn) or ""
    first = _INLINE_LITERAL.sub("", doc.split("\n\n", 1)[0])
    return _DEPRECATED.search(first) is not None


def _signals_deprecation(fn: _FunctionNode, aliases: ImportAliases) -> bool:
    """A ``deprecated`` decorator, or a call to ``warnings.warn`` / ``warnings.warn_explicit`` anywhere in the function."""
    for decorator in fn.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (aliases.qualified_name(target) or "").rsplit(".", 1)[-1] == "deprecated":
            return True
    return any(isinstance(n, ast.Call) and _is_warner(aliases.qualified_name(n.func) or "") for n in shared_walk(fn))


def _is_warner(qualified: str) -> bool:
    """``warnings.warn`` however the module is bound: ``import warnings``, ``from warnings import warn``, or a package
    that re-exports it (``from ._common import warnings``). Only the last two segments of the resolved name are compared."""
    return ".".join(qualified.split(".")[-2:]) in _WARNERS


def _marker(source: str, line: int) -> Optional[str]:
    """The reason after ``# deprecated-ok:`` on *line* or the one above; "" for a marker with no reason; None without one."""
    lines = source.splitlines()
    for number in (line, line - 1):
        if line_has_marker(lines, number, MARKER):
            comment = lines[number - 1].split("#", 1)[1]
            return comment.split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def find_deprecated_docstrings_without_warn(
    root: Union[str, Path],
    *,
    skip_dir_names: Sequence[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every public function under *root* whose docstring calls it deprecated but that neither warns nor is decorated,
    sorted by path and line. Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for a file
    that cannot be read or parsed (unless *allow_unparsed*)."""
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        for fn, name, public in _functions(parsed.tree.body, True):
            if not public or not _claims_deprecation(fn) or _signals_deprecation(fn, aliases):
                continue
            reason = _marker(parsed.source, fn.lineno)
            if reason:
                continue
            suffix = "; the # deprecated-ok marker needs a reason after it" if reason == "" else ""
            out.append(
                Finding(
                    parsed.rel,
                    fn.lineno,
                    RULE,
                    f"`{name}` is documented as deprecated but neither calls warnings.warn nor carries a deprecated decorator, "
                    f"so callers get no warning; add one, or mark # deprecated-ok: <reason>{suffix}",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_deprecated_docstrings_warn(
    root: Union[str, Path],
    *,
    skip_dir_names: Sequence[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any public function documented as deprecated that does not warn."""
    found = find_deprecated_docstrings_without_warn(
        root,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        guidance = "a deprecation that only lives in the docstring is invisible to callers and to -W error::DeprecationWarning sweeps"
        raise AssertionError(f"{len(found)} {RULE} finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
