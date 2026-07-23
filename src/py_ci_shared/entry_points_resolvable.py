"""Shared check: every ``[project.scripts]``/``[project.entry-points.*]``
entry in pyproject.toml actually resolves (module imports, attribute
exists).

Generalizes a 2026-07-22 audit finding (llm_bench, 09-High): pyproject.toml
declared ``llm-bench = "llm_bench.cli.main:main"`` before ``cli/main.py``
existed at all -- a fresh ``pip install`` + running the console script
crashed with ``ModuleNotFoundError`` on the very first invocation, and
nothing in the test suite exercised the installed entry point to catch it.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.entry_points_resolvable import assert_all_entry_points_resolvable

    def test_entry_points_resolvable():
        assert_all_entry_points_resolvable(Path(__file__).resolve().parents[2] / "pyproject.toml")

Deliberately dependency-light: ``tomllib``/``pytest``/``importlib`` are
imported lazily, matching this package's other modules.
"""

from __future__ import annotations

from pathlib import Path


def _iter_entry_specs(data: dict) -> list[tuple[str, str]]:
    """Return ``(qualified_name, "module:attr")`` for every
    ``[project.scripts]`` and ``[project.entry-points.<group>]`` entry."""
    project = data.get("project", {})
    out: list[tuple[str, str]] = []
    for name, spec in project.get("scripts", {}).items():
        out.append((f"[project.scripts] {name}", spec))
    for group, entries in project.get("entry-points", {}).items():
        for name, spec in entries.items():
            out.append((f"[project.entry-points.{group}] {name}", spec))
    return out


def find_unresolvable_entry_points(pyproject_path: Path) -> list[str]:
    """Return one message per entry-point spec that fails to import its
    module, or whose module lacks the named attribute."""
    import importlib
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for qualified_name, spec in _iter_entry_specs(data):
        module_path, sep, attr = spec.partition(":")
        if not sep:
            violations.append(f"{qualified_name} = {spec!r}: not in 'module:attr' form")
            continue
        try:
            module = importlib.import_module(module_path)
        except Exception as e:  # noqa: BLE001 -- any import failure is the finding itself
            violations.append(f"{qualified_name} = {spec!r}: cannot import {module_path!r}: {e}")
            continue
        if attr and not hasattr(module, attr):
            violations.append(f"{qualified_name} = {spec!r}: {module_path!r} has no attribute {attr!r}")
    return violations


def assert_all_entry_points_resolvable(pyproject_path: Path) -> None:
    """Fail if any entry-point spec in ``pyproject_path`` doesn't resolve.
    Call this directly as the body of a ``test_*`` function -- no
    baseline/refresh mechanism, since an unresolvable entry point is
    unconditionally wrong (there's no legitimate "grandfathered" broken
    console script)."""
    import pytest

    violations = find_unresolvable_entry_points(pyproject_path)
    if violations:
        msg = "\n  ".join(violations)
        pytest.fail(f"Unresolvable entry point(s) in {pyproject_path}:\n  {msg}")
