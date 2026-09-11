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

#: Path components that mark test-adjacent code, exempt from the rule.
DEFAULT_EXEMPT_PARTS: tuple[str, ...] = ("tests", "_benchmarks", "__pycache__")
#: File-name prefixes that mark white-box instrumentation.
DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("_profile_", "_bench_")


def owning_package(module: str) -> "str | None":
    """``pkg.a._b.c`` -> ``pkg.a``; ``None`` when no segment after the first starts with ``_``."""
    parts = module.split(".")
    for i, part in enumerate(parts[1:], start=1):
        if part.startswith("_"):
            return ".".join(parts[:i])
    return None


def _imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def _caller_package(py: Path, src_dir: Path, package: str) -> str:
    rel = py.parent.relative_to(src_dir).as_posix()
    return package if rel == "." else package + "." + rel.replace("/", ".")


def find_private_cross_package_imports(
    src_dir: Path,
    package: str,
    repo_root: Path,
    *,
    exempt_parts: Iterable[str] = DEFAULT_EXEMPT_PARTS,
    exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
) -> set[tuple[str, str]]:
    """``{(importer relative to repo_root, imported module)}`` for every reach into a foreign private module."""
    skip_parts = set(exempt_parts)
    prefixes = tuple(exempt_prefixes)
    out: set[tuple[str, str]] = set()
    for py in sorted(src_dir.rglob("*.py")):
        if set(py.relative_to(src_dir).parts) & skip_parts or py.name.startswith(prefixes):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        caller = _caller_package(py, src_dir, package)
        for module in _imports(tree):
            if module != package and not module.startswith(package + "."):
                continue
            owner = owning_package(module)
            if owner is None or caller == owner or caller.startswith(owner + "."):
                continue
            out.add((py.relative_to(repo_root).as_posix(), module))
    return out


def assert_no_private_cross_package_imports(
    src_dir: Path,
    package: str,
    repo_root: Path,
    *,
    allowlist: Iterable[tuple[str, str]] = (),
    exempt_parts: Iterable[str] = DEFAULT_EXEMPT_PARTS,
    exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
) -> None:
    """Fail on a reach not in *allowlist*, and on an allowlist entry that no longer occurs."""
    import pytest

    allowed = set(allowlist)
    found = find_private_cross_package_imports(src_dir, package, repo_root, exempt_parts=exempt_parts, exempt_prefixes=exempt_prefixes)
    problems = []
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
