"""Shared check: production code does not import another package's underscore-prefixed module.

An underscore module is internal to the package that holds it; its public surface is whatever that
package re-exports from its ``__init__.py``. A sibling inside the same package may import it freely. Code
elsewhere that reaches into ``other_pkg._private`` couples two subsystems to an implementation detail and
breaks, silently, when either side moves a helper.

mlframe carried two copies (one scoped to ``training.core``, one generalised) and glossum a third ported
from them; the owning package is the dotted path up to the first underscore segment in all three, and an
importer inside that package or below it is a sibling. Test harnesses are exempt: white-box access is what
tests are for.

Usage::

    from py_ci_shared.private_imports import assert_no_private_cross_package_imports

    def test_no_private_cross_package_imports():
        assert_no_private_cross_package_imports(REPO_ROOT / "src" / "pkg", "pkg", REPO_ROOT)
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, ScanResult, SourceProblem, module_of, package_of, relative_posix, resolve_relative, scan_python
from ._core.node_index import walk as _fast_walk

#: Path components that mark test-adjacent code, exempt from the rule.
DEFAULT_EXEMPT_PARTS: tuple[str, ...] = ("tests", "_benchmarks", "__pycache__")
#: File-name prefixes that mark white-box instrumentation.
DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("_profile_", "_bench_")

#: The ``module`` slot of a finding for a file that could not be parsed.
UNPARSED_MARKER = "<unparsed>"


def owning_package(module: str) -> "str | None":
    """``pkg.a._b.c`` -> ``pkg.a``; ``None`` when no segment after the first starts with ``_``."""
    parts = module.split(".")
    for i, part in enumerate(parts[1:], start=1):
        if part.startswith("_"):
            return ".".join(parts[:i])
    return None


def _imports(tree: ast.AST, caller: str) -> Iterator[str]:
    """Every dotted target an import in *tree* reaches, relative imports resolved against *caller*.

    ``from pkg.a import _impl`` yields ``pkg.a._impl`` (the imported name may be a private submodule or a private
    helper of a public module; either way it is internal to ``pkg.a``). ``from pkg.a._core import X`` yields just
    ``pkg.a._core``: the reach is into the module, and the public name inside it adds nothing.
    """
    for node in _fast_walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = resolve_relative(node.module, node.level, caller)
            if module is None:
                continue
            if owning_package(module) is not None:
                yield module
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name.startswith("_") and not alias.name.startswith("__"):
                    yield f"{module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def _caller_package(py: Path, src_dir: Path, package: str) -> str:
    return package_of(py, src_dir, package)


def _scan(src_dir: Path, exempt_parts: Iterable[str], exempt_prefixes: Iterable[str], use_git: Optional[bool]) -> ScanResult:
    prefixes = tuple(exempt_prefixes)
    scan = scan_python(src_dir, exclude=DEFAULT_EXCLUDE | frozenset(exempt_parts), use_git=use_git)
    scan.files = [f for f in scan.files if not f.path.name.startswith(prefixes)]
    scan.unparsed = [p for p in scan.unparsed if not p.path.name.startswith(prefixes)]
    return scan


def _owner(module: str, plain_modules: "set[str]") -> "str | None":
    """The package *module* is internal to. A private NAME of a plain module (``pkg.mod._helper``, where ``pkg/mod.py``
    is a file) belongs to the package holding that module, ``pkg``, like the module itself: its siblings share it."""
    owner = owning_package(module)
    if owner is not None and owner in plain_modules and module.count(".") == owner.count(".") + 1:
        return owner.rpartition(".")[0] or owner
    return owner


def _reaches(scan: ScanResult, src_dir: Path, package: str, repo_root: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    plain_modules = {module_of(f.path, src_dir, package) for f in scan if f.path.name != "__init__.py"}
    for f in scan:
        caller = _caller_package(f.path, src_dir, package)
        for module in _imports(f.tree, caller):
            if module != package and not module.startswith(package + "."):
                continue
            owner = _owner(module, plain_modules)
            if owner is None or caller == owner or caller.startswith(owner + "."):
                continue
            out.add((relative_posix(f.path, repo_root), module))
    return out


def find_private_cross_package_imports(
    src_dir: Path,
    package: str,
    repo_root: Path,
    *,
    exempt_parts: Iterable[str] = DEFAULT_EXEMPT_PARTS,
    exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
    use_git: Optional[bool] = None,
) -> set[tuple[str, str]]:
    """``{(importer relative to repo_root, imported module)}`` for every reach into a foreign private module.

    A file that cannot be read or parsed appears as ``(importer, "<unparsed>")`` rather than being skipped. An
    importer outside *repo_root* is keyed by its absolute POSIX path. A missing *src_dir* raises ``CorpusError``.
    """
    scan = _scan(src_dir, exempt_parts, exempt_prefixes, use_git)
    found = _reaches(scan, src_dir, package, repo_root)
    found.update((relative_posix(p.path, repo_root), UNPARSED_MARKER) for p in scan.unparsed)
    return found


def _render_unparsed(problems: list[SourceProblem], repo_root: Path) -> str:
    return "\n    ".join(f"{relative_posix(p.path, repo_root)}:{p.line}: {p.kind}: {p.message}" for p in problems)


def assert_no_private_cross_package_imports(
    src_dir: Path,
    package: str,
    repo_root: Path,
    *,
    allowlist: Iterable[tuple[str, str]] = (),
    exempt_parts: Iterable[str] = DEFAULT_EXEMPT_PARTS,
    exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
    min_files: int = 1,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a reach not in *allowlist*, on an allowlist entry that no longer occurs, on an unparsable file,
    and when fewer than *min_files* files parsed."""
    import pytest

    allowed = set(allowlist)
    scan = _scan(src_dir, exempt_parts, exempt_prefixes, use_git)
    scan.min_files = min_files
    found = _reaches(scan, src_dir, package, repo_root)
    problems = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append("files that could not be parsed, so their imports were not checked:\n    " + _render_unparsed(scan.unparsed, repo_root))
    new = sorted(found - allowed)
    if new:
        problems.append(
            "production code imports another package's underscore module; re-export the name from the owning "
            "package's __init__.py (or import it from its real public home):\n    " + "\n    ".join(f"{p} -> {m}" for p, m in new)
        )
    stale = sorted(allowed - found)
    if stale:
        problems.append("allowlist entries that no longer occur (delete them):\n    " + "\n    ".join(f"{p} -> {m}" for p, m in stale))
    if problems:
        pytest.fail("\n  ".join(problems))
