"""No import reaches into another distribution's private vendored copy of a third package.

``from joblib.externals import cloudpickle`` worked until joblib 1.6.0 stopped vendoring cloudpickle; 35 tests of
mlframe's sklearn-matrix job then failed at import (mlframe 7a8fd1d15). deptry and the declared-dependency checks
cannot see it, because ``joblib`` IS declared: what the code really depends on is ``cloudpickle``, through a path
joblib never promised to keep.

Flagged: ``import``/``from`` of a module path whose second or later segment is a vendoring directory
(``externals``, ``_externals``, ``_vendor``, ``vendor``, ``_vendored``, ``vendored``, ``extern``), plus the
vendoring paths that do not use such a name (``requests.packages``), and ``importlib.import_module``/
``__import__`` of the same with a literal argument. The repo's OWN vendored code is not foreign:
a path whose top-level package is first-party (a package directory at the repo root or under ``src/``, or one named
in *first_party*) is not reported, and neither is a relative import.

Accepted without a marker: the standard fallback that prefers the standalone package, i.e. the vendored import
inside an ``except ImportError`` handler whose ``try`` body imports the standalone module of the same name::

    try:
        import cloudpickle
    except ImportError:
        from joblib.externals import cloudpickle

Any other line stays only with ``# vendored-ok: <reason>``. The fix the gate asks for: import the standalone
distribution and declare it.

Usage::

    from py_ci_shared.vendored_internal_imports import assert_no_vendored_internal_imports

    def test_no_vendored_internal_imports():
        assert_no_vendored_internal_imports(REPO, baseline_path=REPO / "tests/baselines/vendored_internal_imports.json")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._core.node_index import walk as _fast_walk
from ._gate_report import line_has_marker, report, scan_tree, skip_set

__all__ = ["MARKER", "REFRESH_FLAG", "RULE", "VENDOR_SEGMENTS", "assert_no_vendored_internal_imports", "find_vendored_internal_imports", "vendored_part"]

RULE = "vendored-internal-import"
REFRESH_FLAG = "--refresh-vendored-internal-imports-baseline"
MARKER = "vendored-ok"

#: A module path segment (after the first) that names a directory holding a private copy of another distribution.
VENDOR_SEGMENTS = frozenset({"externals", "_externals", "_vendor", "vendor", "_vendored", "vendored", "extern"})
#: Vendoring paths whose directory is not named like one of :data:`VENDOR_SEGMENTS`.
_VENDOR_PREFIXES = ("requests.packages", "urllib3.packages")
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})


def vendored_part(module: str) -> Optional[tuple[str, str]]:
    """``(vendoring package path, standalone name)`` when *module* reaches into a vendored copy, else None.

    ``joblib.externals.loky.backend`` -> ``("joblib.externals", "loky")``; ``joblib.externals`` alone -> the standalone name
    is empty (the imported names are the vendored modules)."""
    parts = module.split(".")
    for prefix in _VENDOR_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            rest = module[len(prefix) + 1 :]
            return prefix, rest.split(".", 1)[0] if rest else ""
    for i, part in enumerate(parts[1:], start=1):
        if part in VENDOR_SEGMENTS:
            return ".".join(parts[: i + 1]), parts[i + 1] if i + 1 < len(parts) else ""
    return None


def _first_party(root: Path, extra: Iterable[str]) -> frozenset[str]:
    """Top-level package names the repo itself ships: directories with an ``__init__.py`` at the root or under ``src/``."""
    names = set(extra)
    for base in (root, root / "src"):
        if base.is_dir():
            names.update(p.parent.name for p in base.glob("*/__init__.py"))
    return frozenset(names)


def _handler_fallbacks(tree: ast.Module) -> dict[int, set[str]]:
    """``{id(import node): standalone names imported by the enclosing try body}`` for imports inside an ImportError handler."""
    out: dict[int, set[str]] = {}
    for node in _fast_walk(tree):
        if not isinstance(node, ast.Try):
            continue
        tried = _imported_modules(node.body)
        for handler in node.handlers:
            if _catches_import_error(handler):
                for sub in (s for stmt in handler.body for s in ast.walk(stmt)):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        out.setdefault(id(sub), set()).update(tried)
    return out


def _imported_modules(body: list[ast.stmt]) -> set[str]:
    """Modules the statements import absolutely (``import x`` -> ``x``, ``from x import y`` -> ``x``)."""
    out: set[str] = set()
    for sub in (s for stmt in body for s in ast.walk(stmt)):
        if isinstance(sub, ast.Import):
            out.update(a.name for a in sub.names)
        elif isinstance(sub, ast.ImportFrom) and sub.level == 0 and sub.module:
            out.add(sub.module)
    return out


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    types = caught.elts if isinstance(caught, ast.Tuple) else [caught] if caught is not None else []
    return any(isinstance(t, ast.Name) and t.id in _IMPORT_ERRORS for t in types)


def _imports(tree: ast.Module) -> list[tuple[ast.AST, int, str, list[str]]]:
    """``(node, line, module path, standalone candidates)`` for every absolute import (or literal dynamic import)."""
    aliases = ImportAliases.from_tree(tree)
    out: list[tuple[ast.AST, int, str, list[str]]] = []
    for node in _fast_walk(tree):
        if isinstance(node, ast.Import):
            out += [(node, node.lineno, a.name, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node, node.lineno, node.module, [a.name for a in node.names]))
        elif isinstance(node, ast.Call) and node.args:
            arg = node.args[0]
            if aliases.qualified_name(node.func) in ("importlib.import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((node, node.lineno, arg.value, []))
    return out


def _file_findings(parsed: ParsedFile, first_party: frozenset[str]) -> list[Finding]:
    lines = parsed.source.splitlines()
    fallbacks = _handler_fallbacks(parsed.tree)
    out: list[Finding] = []
    for node, line, module, names in _imports(parsed.tree):
        hit = vendored_part(module)
        if hit is None or module.split(".", 1)[0] in first_party or line_has_marker(lines, line, MARKER):
            continue
        vendor, standalone = hit
        standalone_names = [standalone] if standalone else names
        if standalone_names and all(n in fallbacks.get(id(node), set()) for n in standalone_names):
            continue  # the vendored copy is the fallback of a standalone import
        shown = ", ".join(standalone_names) or "the vendored package"
        out.append(
            Finding(
                parsed.rel,
                line,
                RULE,
                f"imports `{module}`, a private copy vendored inside `{vendor}`: import the standalone distribution ({shown}) and declare it",
            )
        )
    return out


def _collect(
    root: Union[str, Path], *, first_party: Iterable[str], skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    own = _first_party(Path(root), first_party)
    findings = [f for parsed in scan for f in _file_findings(parsed, own)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_vendored_internal_imports(
    root: Union[str, Path],
    *,
    first_party: Iterable[str] = (),
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = True,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every import of a foreign vendored path under *root*, plus one ``unparsed-file`` finding per unparsable file."""
    findings, scan = _collect(root, first_party=first_party, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_vendored_internal_imports(
    root: Union[str, Path],
    *,
    first_party: Iterable[str] = (),
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = True,
    min_files: int = 1,
    allow_unparsed: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on an import of a foreign vendored path (new against *baseline_path* when given), on fewer than *min_files*
    parsed files, and on an unparsable file unless *allow_unparsed*."""
    findings, scan = _collect(root, first_party=first_party, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="vendored-internal-imports",
        flag=REFRESH_FLAG,
        guidance=f"import the standalone package and declare it, or mark a guarded line `# {MARKER}: <reason>`",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        allow_unparsed=allow_unparsed,
    )
