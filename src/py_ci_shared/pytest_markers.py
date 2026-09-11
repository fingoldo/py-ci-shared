"""Shared check: every pytest marker a test suite uses is registered.

Under ``--strict-markers`` an unregistered ``@pytest.mark.<name>`` is a collection-time error; without
it the marker is a silent no-op, so ``-m "not slow"`` keeps running a test whose ``slow`` was misspelled.
Either way the fix is registration, and nothing short of a scan tells you it is missing.

Four consumer repos (production_scrapers, realtime_applications, mlframe, glossum) each carried their
own copy of this check, 99 to 175 lines, all with the same regexes and slightly different builtin sets and
parsers. This module is their union:

* registered = ``[tool.pytest.ini_options].markers`` in pyproject (an ``name(args): text`` entry
  registers ``name``, as pytest itself reads it) plus every ``config.addinivalue_line("markers", ...)``
  in any ``conftest.py`` under the tests directory, plus pytest's builtins and the common plugins';
* used = every ``pytest.mark.<name>`` in any ``.py`` under the tests directory, docstrings included,
  because a snippet in a docstring is copied into a real decorator (mlframe S27).

Usage::

    from py_ci_shared.pytest_markers import assert_markers_registered

    def test_every_used_marker_is_registered():
        assert_markers_registered(REPO_ROOT, expect_registered=("slow", "integration"))
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from ._toml_compat import tomllib

#: Markers pytest and the plugins these repos use provide without registration.
BUILTIN_MARKERS: frozenset[str] = frozenset(
    {
        "skip",
        "skipif",
        "xfail",
        "parametrize",
        "usefixtures",
        "filterwarnings",
        "tryfirst",
        "trylast",
        "order",  # pytest-order
        "timeout",  # pytest-timeout
        "asyncio",  # pytest-asyncio
        "xdist_group",  # pytest-xdist loadgroup
    }
)

_MARK_RE = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")


def _marker_name(entry: str) -> str:
    """``"name(arg): text"`` -> ``"name"``; pytest matches the bare name before ``(`` or ``:``."""
    return entry.split(":", 1)[0].split("(", 1)[0].strip()


def pyproject_markers(pyproject: Path) -> set[str]:
    """Marker names registered in ``[tool.pytest.ini_options].markers``; empty if the file is absent."""
    if not pyproject.is_file():
        return set()
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    raw = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", []) or []
    return {name for entry in raw if isinstance(entry, str) and (name := _marker_name(entry))}


def conftest_markers(tests_dir: Path) -> set[str]:
    """Marker names registered through ``config.addinivalue_line("markers", "...")`` in any conftest."""
    out: set[str] = set()
    for conftest in sorted(tests_dir.rglob("conftest.py")):
        if "__pycache__" in conftest.parts:
            continue
        try:
            tree = ast.parse(conftest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addinivalue_line"):
                continue
            if len(node.args) < 2:
                continue
            first, second = node.args[0], node.args[1]
            if isinstance(first, ast.Constant) and first.value == "markers" and isinstance(second, ast.Constant) and isinstance(second.value, str):
                if name := _marker_name(second.value):
                    out.add(name)
    return out


def used_markers(tests_dir: Path, repo_root: Path, *, exclude: Iterable[Path] = ()) -> dict[str, list[str]]:
    """``{marker: [files that name it]}`` for every ``pytest.mark.<name>`` under *tests_dir*."""
    skip = {p.resolve() for p in exclude}
    seen: dict[str, list[str]] = {}
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts or path.resolve() in skip:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in sorted(set(_MARK_RE.findall(text))):
            seen.setdefault(name, []).append(path.relative_to(repo_root).as_posix())
    return seen


def find_unregistered_markers(
    repo_root: Path,
    *,
    tests_dir: "Path | None" = None,
    extra_registered: Iterable[str] = (),
    exclude: Iterable[Path] = (),
) -> dict[str, list[str]]:
    """Markers used under the tests directory that nothing registers, with the files naming each."""
    tests = tests_dir or repo_root / "tests"
    registered = pyproject_markers(repo_root / "pyproject.toml") | conftest_markers(tests) | BUILTIN_MARKERS | set(extra_registered)
    return {name: files for name, files in used_markers(tests, repo_root, exclude=exclude).items() if name not in registered}


def assert_markers_registered(
    repo_root: Path,
    *,
    tests_dir: "Path | None" = None,
    extra_registered: Iterable[str] = (),
    expect_registered: Iterable[str] = (),
    exclude: Iterable[Path] = (),
) -> None:
    """Fail on an unregistered marker, and on a registration the parsers could not see.

    ``expect_registered`` names markers the caller KNOWS it registers. If the parsers miss one of them
    (a pyproject schema move, a conftest refactor), the check would otherwise report a clean tree while
    reading nothing -- each local copy guarded that with a smoke test of its own; this is that guard.
    """
    import pytest

    tests = tests_dir or repo_root / "tests"
    registered = pyproject_markers(repo_root / "pyproject.toml") | conftest_markers(tests)
    missing = sorted(set(expect_registered) - registered)
    if missing:
        pytest.fail(f"expected registered marker(s) not found by the parser: {missing} -- pyproject or conftest moved, or the parser broke")
    unregistered = find_unregistered_markers(repo_root, tests_dir=tests, extra_registered=extra_registered, exclude=exclude)
    if unregistered:
        pytest.fail(
            "Unregistered pytest markers (an error under --strict-markers, a silent no-op without it):\n"
            + "\n".join(f"  {name}: {files[:5]}" for name, files in sorted(unregistered.items()))
            + "\nRegister them in pyproject.toml [tool.pytest.ini_options].markers or a conftest pytest_configure."
        )
