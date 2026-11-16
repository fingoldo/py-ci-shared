"""A write to a global through a re-exporting facade changes the facade's copy, not the global.

``facade.py`` holds ``from .impl import _LIMIT``; ``user.py`` does ``from . import facade as _f`` and ``_f._LIMIT = v``.
The name ``_f._LIMIT`` is rebound on the facade, while the code that reads ``impl._LIMIT`` never sees the change. The
setting appears to apply and does not, and nothing fails (mlframe 2026-07-18, ``_gpu_resident_radix_ktc.py``).

Per file, an assignment or augmented assignment to ``alias.NAME`` is checked when ``NAME`` is ALL_CAPS (optionally with a
leading underscore), and ``alias`` is bound by a relative ``from . import mod as alias`` (or ``from .sub import mod``) to a
sibling module file. The sibling is a re-exporter of NAME when it binds NAME only through an import, not through a
module-level assignment, definition or class. Relative imports that resolve to no file are not checked.

Write through the owning module. Accepted deliberately with ``# reexport-write-ok: <reason>`` on the write's line or the
line above; a marker with no reason is a finding.

Usage::

    from py_ci_shared.global_write_via_reexport_alias import assert_global_write_via_reexport_alias

    def test_no_writes_through_reexports():
        assert_global_write_via_reexport_alias("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Finding
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_global_write_via_reexport_alias", "find_global_write_via_reexport_alias"]

RULE = "global-write-via-reexport-alias"
MARKER = "reexport-write-ok"
_GLOBAL = re.compile(r"_?[A-Z][A-Z0-9_]*")


def _module_bindings(tree: ast.Module, path: Path) -> dict[str, Path]:
    """``alias -> sibling module file`` for each ``from . import mod as alias`` / ``from .sub import mod as alias`` that resolves."""
    out: dict[str, Path] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ImportFrom) and node.level >= 1):
            continue
        base = path.parent
        for _ in range(node.level - 1):
            base = base.parent
        if node.module:
            base = base.joinpath(*node.module.split("."))
        for alias in node.names:
            candidate = base / f"{alias.name}.py"
            if candidate.is_file():
                out[alias.asname or alias.name] = candidate
    return out


def _origin_of_reexport(module_path: Path, name: str, cache: dict[Path, Optional[ast.Module]]) -> Optional[str]:
    """Where *module_path* imports *name* from (e.g. ``.impl``) when it binds *name* only through an import, else None."""
    if module_path not in cache:
        try:
            cache[module_path] = ast.parse(module_path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            cache[module_path] = None
    tree = cache[module_path]
    if tree is None:
        return None
    origin: Optional[str] = None
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
            origin = "." * node.level + (node.module or "")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name.split(".")[0]) == name for a in node.names):
            origin = origin or "?"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return None
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return None
    return origin


def _writes(tree: ast.Module) -> list[tuple[str, str, int]]:
    """Each ``<name>.<ATTR> = ...`` / ``+=`` / annotated assignment target as ``(name, ATTR, line)``."""
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        out.extend((t.value.id, t.attr, node.lineno) for t in targets if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name))
    return out


def _marker_reason(lines: list[str], line: int) -> Optional[str]:
    for k in (line, line - 1):
        if line_has_marker(lines, k, MARKER):
            return lines[k - 1].split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def find_global_write_via_reexport_alias(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every write to a re-exported ALL_CAPS name through a sibling facade under *root*, sorted by path and line."""
    base = Path(root)
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    cache: dict[Path, Optional[ast.Module]] = {}
    out: list[Finding] = []
    for parsed in scan:
        path = base / parsed.rel
        bindings = _module_bindings(parsed.tree, path)
        if not bindings:
            continue
        lines = parsed.source.splitlines()
        for owner, name, line in _writes(parsed.tree):
            module = bindings.get(owner)
            if module is None or not _GLOBAL.fullmatch(name):
                continue
            origin = _origin_of_reexport(module, name, cache)
            if origin is None:
                continue
            reason = _marker_reason(lines, line)
            if reason:
                continue
            suffix = "; the # reexport-write-ok marker needs a reason after it" if reason == "" else ""
            out.append(
                Finding(
                    parsed.rel,
                    line,
                    RULE,
                    f"`{owner}.{name} = ...` rebinds the facade `{module.name}`, which imports {name} from `{origin}`; that module's global is unchanged. Write through `{origin}`{suffix}",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_global_write_via_reexport_alias(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any write to a re-exported global through a facade alias."""
    found = find_global_write_via_reexport_alias(
        root,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
