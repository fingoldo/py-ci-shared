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

import re
from pathlib import Path

from ._core import read_source

#: ``module:attr [extra1, extra2]``: the extras a console script needs, which are not part of the object reference.
_EXTRAS_RE = re.compile(r"\s*\[[^\]]*\]\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


def _iter_entry_specs(data: dict) -> list[tuple[str, str]]:
    """Return ``(qualified_name, "module:attr")`` for every
    ``[project.scripts]``, ``[project.gui-scripts]`` and ``[project.entry-points.<group>]`` entry."""
    project = data.get("project", {})
    out: list[tuple[str, str]] = []
    for table in ("scripts", "gui-scripts"):
        for name, spec in project.get(table, {}).items():
            out.append((f"[project.{table}] {name}", spec))
    for group, entries in project.get("entry-points", {}).items():
        for name, spec in entries.items():
            out.append((f"[project.entry-points.{group}] {name}", spec))
    return out


def _resolve(module: object, attr: str) -> tuple[bool, str]:
    """Walk ``attr`` (``Class.method``) one ``getattr`` at a time; ``(False, missing part)`` when one step is absent."""
    target = module
    for part in attr.split("."):
        if not hasattr(target, part):
            return False, part
        target = getattr(target, part)
    return True, ""


def find_unresolvable_entry_points(pyproject_path: Path) -> list[str]:
    """Return one message per entry-point spec that fails to import its
    module, or whose module lacks the named (possibly dotted) attribute. An extras suffix (``[cli]``) is
    ignored; an empty module or attribute is a violation."""
    import importlib
    from ._toml_compat import tomllib

    data = tomllib.loads(read_source(pyproject_path))
    violations: list[str] = []
    for qualified_name, spec in _iter_entry_specs(data):
        reference = _EXTRAS_RE.sub("", str(spec)).strip()
        module_path, sep, attr = (part.strip() for part in reference.partition(":"))
        # A console/GUI script must name a callable (`module:attr`); a plugin entry point may name a whole module
        # (`pytest11 = "pkg.plugin"`), which the spec allows. A colon with nothing on one side is malformed either way.
        module_only = not sep and not qualified_name.startswith("[project.scripts]") and not qualified_name.startswith("[project.gui-scripts]")
        if not module_only and (not sep or not module_path or not attr):
            violations.append(f"{qualified_name} = {spec!r}: not in 'module:attr' form")
            continue
        if not _IDENTIFIER_RE.match(module_path) or (attr and not _IDENTIFIER_RE.match(attr)):
            violations.append(f"{qualified_name} = {spec!r}: {module_path!r}:{attr!r} is not a dotted name")
            continue
        try:
            module = importlib.import_module(module_path)
        except Exception as e:  # any import failure is the finding itself
            violations.append(f"{qualified_name} = {spec!r}: cannot import {module_path!r}: {e}")
            continue
        ok, missing = _resolve(module, attr) if attr else (True, "")
        if not ok:
            violations.append(f"{qualified_name} = {spec!r}: {module_path!r} has no attribute {attr!r} ({missing!r} is missing)")
    return violations


def assert_all_entry_points_resolvable(pyproject_path: Path, *, min_entries: int = 1) -> None:
    """Fail if any entry-point spec in ``pyproject_path`` doesn't resolve, or if fewer than *min_entries* specs were
    found (a typo such as ``[project.script]`` declares nothing, and nothing would be checked).
    Call this directly as the body of a ``test_*`` function -- no
    baseline/refresh mechanism, since an unresolvable entry point is
    unconditionally wrong (there's no legitimate "grandfathered" broken
    console script)."""
    import pytest
    from ._toml_compat import tomllib

    found = len(_iter_entry_specs(tomllib.loads(read_source(pyproject_path))))
    if found < min_entries:
        pytest.fail(f"only {found} entry point(s) declared in {pyproject_path}; expected at least {min_entries} -- check the table names")
    violations = find_unresolvable_entry_points(pyproject_path)
    if violations:
        msg = "\n  ".join(violations)
        pytest.fail(f"Unresolvable entry point(s) in {pyproject_path}:\n  {msg}")
