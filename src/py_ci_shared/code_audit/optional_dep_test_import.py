"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
from pathlib import Path
from collections.abc import Iterable

from ._base import Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, is_line_exempt

__all__ = ["scan_unguarded_optional_test_imports"]

# --- a test module that imports an optional dependency without a skip guard ----------------------------
#
# A test module that does ``import lightning`` at module level fails at COLLECTION in any CI matrix leg that does not
# install it, so the whole file errors instead of skipping (mlframe audit 40-F2: the sklearn leg died on a missing
# lightning). ``pytest.importorskip("lightning")`` above the import turns that into a skip. Only module-level statements
# are checked: an import inside a test function runs only when that test runs.

_CHECK = "unguarded_optional_test_import"
_DEFAULT_OPTIONAL = frozenset({"lightning", "pytorch_lightning"})


def _importorskip_roots(stmt: ast.stmt) -> set[str]:
    """The top-level package names a statement guards with ``importorskip("<name>")``."""
    roots: set[str] = set()
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "importorskip"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            roots.add(node.args[0].value.split(".")[0])
    return roots


def _imported_roots(stmt: ast.stmt) -> list[str]:
    """Top-level package names a module-level import statement pulls in."""
    if isinstance(stmt, ast.Import):
        return [alias.name.split(".")[0] for alias in stmt.names]
    if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
        return [stmt.module.split(".")[0]]
    return []


def scan_unguarded_optional_test_imports(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
    optional_modules: Iterable[str] = _DEFAULT_OPTIONAL,
) -> list[Finding]:
    """Find ``test_*.py`` modules that import one of ``optional_modules`` at module level before any ``importorskip`` of it.

    A guard placed after the import does not help, because the import has already failed by then. Suppress one line with
    ``# audit-exempt: unguarded_optional_test_import -- <reason of 19+ chars>``. Severity P2.
    """
    optional = frozenset(optional_modules)
    findings: list[Finding] = []
    for py in _iter_py_files(root, exclude_dirs):
        if not py.name.startswith("test_"):
            continue
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        guarded: set[str] = set()
        for stmt in tree.body:
            guarded |= _importorskip_roots(stmt)
            hit = [name for name in _imported_roots(stmt) if name in optional and name not in guarded]
            if not hit or is_line_exempt(src_lines, stmt.lineno, _CHECK):
                continue
            findings.append(
                Finding(
                    check=_CHECK,
                    severity="P2",
                    file=rel,
                    line=stmt.lineno,
                    snippet=_line_text(src_lines, stmt.lineno),
                    detail=(
                        f'module-level import of optional dependency `{hit[0]}` with no earlier pytest.importorskip("{hit[0]}"). '
                        f"On a CI leg without it the whole test module fails at collection instead of skipping."
                    ),
                )
            )
    return findings
