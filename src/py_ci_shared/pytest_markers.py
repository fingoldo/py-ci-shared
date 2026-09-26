"""Shared check: every pytest marker a test suite uses is registered.

Under ``--strict-markers`` an unregistered ``@pytest.mark.<name>`` is a collection-time error; without
it the marker is a silent no-op, so ``-m "not slow"`` keeps running a test whose ``slow`` was misspelled.
Either way the fix is registration, and nothing short of a scan tells you it is missing.

Four consumer repos (production_scrapers, realtime_applications, mlframe, glossum) each carried their
own copy of this check, 99 to 175 lines, all with the same regexes and slightly different builtin sets and
parsers. This module is their union:

* registered = ``[tool.pytest.ini_options].markers`` in pyproject (an ``name(args): text`` entry
  registers ``name``, as pytest itself reads it), the ``markers`` of ``pytest.ini`` / ``tox.ini`` /
  ``setup.cfg`` (dashboard registers there, and a pyproject-only reader called both its markers
  unregistered), plus every ``config.addinivalue_line("markers", ...)``
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

from ._core import DEFAULT_EXCLUDE, SourceError, UnparsedFilesError, iter_files, parse_source, read_source, relative_posix
from ._core.node_index import walk as _fast_walk
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


def _names(raw: object) -> set[str]:
    entries = raw.splitlines() if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return {name for entry in entries if isinstance(entry, str) and (name := _marker_name(entry))}


def pyproject_markers(pyproject: Path) -> set[str]:
    """Marker names registered in ``[tool.pytest.ini_options].markers`` or pytest 9's native ``[tool.pytest].markers``;
    empty if the file is absent."""
    if not pyproject.is_file():
        return set()
    data = tomllib.loads(read_source(pyproject))
    table = data.get("tool", {}).get("pytest", {})
    return _names(table.get("ini_options", {}).get("markers", [])) | _names(table.get("markers", []))


def toml_markers(repo_root: Path) -> set[str]:
    """Marker names registered in pytest 9's ``pytest.toml`` / ``.pytest.toml`` (``[pytest].markers``) at *repo_root*."""
    out: set[str] = set()
    for filename in ("pytest.toml", ".pytest.toml"):
        path = repo_root / filename
        if path.is_file():
            out |= _names(tomllib.loads(read_source(path)).get("pytest", {}).get("markers", []))
    return out


#: ini files pytest reads its ``markers`` from, with the section each uses.
_INI_SECTIONS: tuple[tuple[str, str], ...] = (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest"))


def ini_markers(repo_root: Path) -> set[str]:
    """Marker names registered in ``pytest.ini``, ``tox.ini`` or ``setup.cfg`` at *repo_root*.

    pytest splits the ``markers`` value into lines and registers each one, continuation lines included; only a line
    whose name is an identifier can match a ``pytest.mark.<name>``, so the rest are dropped.
    """
    import configparser

    out: set[str] = set()
    for filename, section in _INI_SECTIONS:
        path = repo_root / filename
        if not path.is_file():
            continue
        text = read_source(path)
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text, source=str(path))
        except (configparser.DuplicateOptionError, configparser.DuplicateSectionError) as exc:
            if exc.section == section:
                raise ValueError(f"{path}: {exc}; pytest cannot load this section either") from exc
            parser = configparser.ConfigParser(interpolation=None, strict=False)  # a duplicate in another tool's section
            parser.read_string(text, source=str(path))
        except configparser.Error as exc:
            raise ValueError(f"{path} is not a readable ini file, so its pytest markers are unknown: {exc}") from exc
        if parser.has_option(section, "markers"):
            for line in parser.get(section, "markers").splitlines():
                name = _marker_name(line)
                if name.isidentifier():
                    out.add(name)
    return out


def _string_values(node: ast.AST, constants: "dict[str, list[str]]", loops: "dict[str, list[str]]") -> "list[str] | None":
    """Literal strings an ``addinivalue_line`` argument can take: a constant, a module-level string (or tuple/list
    of strings) bound to a name, the loop variable of a ``for`` over literals, or the constant head of an f-string
    / ``%`` / ``+`` expression that holds the marker name. ``None`` when it cannot be resolved."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return loops.get(node.id) or constants.get(node.id)
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
        head = node.values[0].value
        return [head] if (":" in head or "(" in head) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        left = _string_values(node.left, constants, loops)
        heads = [v for v in left or () if ":" in v or "(" in v]
        return heads or None
    return None


def _literal_strings(node: ast.AST) -> "list[str] | None":
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return values if len(values) == len(node.elts) else None
    return None


def conftest_registrations(tree: ast.Module) -> "tuple[set[str], list[int]]":
    """``(marker names, lines of registrations that could not be resolved)`` for one conftest module."""
    constants: dict[str, list[str]] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            values = _literal_strings(statement.value)
            if values is not None:
                constants[statement.targets[0].id] = values
    loops: dict[str, list[str]] = {}
    for node in _fast_walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            values = _literal_strings(node.iter)
            if values is None and isinstance(node.iter, ast.Name):
                values = constants.get(node.iter.id)
            if values is not None:
                loops[node.target.id] = values
    names: set[str] = set()
    unresolved: list[int] = []
    for node in _fast_walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addinivalue_line"):
            continue
        if len(node.args) < 2:
            continue
        first = _string_values(node.args[0], constants, loops)
        if first != ["markers"]:
            if first is None:
                unresolved.append(node.lineno)
            continue
        values = _string_values(node.args[1], constants, loops)
        if values is None:
            unresolved.append(node.lineno)
            continue
        found = {name for value in values if (name := _marker_name(value)).isidentifier()}
        if not found:
            unresolved.append(node.lineno)
        names |= found
    return names, unresolved


def _conftests(tests_dir: Path, repo_root: "Path | None") -> list[Path]:
    found = list(iter_files(tests_dir, ("conftest.py",), exclude=DEFAULT_EXCLUDE)) if tests_dir.is_dir() else []
    if repo_root is not None and (repo_root / "conftest.py").is_file():
        found.append(repo_root / "conftest.py")
    return sorted({p.resolve(): p for p in found}.values())


def conftest_markers(tests_dir: Path, *, repo_root: "Path | None" = None, unresolved: "list[str] | None" = None) -> set[str]:
    """Marker names registered through ``config.addinivalue_line("markers", ...)`` in any conftest under *tests_dir*
    (and *repo_root*'s own ``conftest.py`` when given). Registrations whose value cannot be resolved statically are
    appended to *unresolved* as ``path:line``. Raises ``UnparsedFilesError`` on an unparsable conftest."""
    out: set[str] = set()
    problems: list[str] = []
    for conftest in _conftests(tests_dir, repo_root):
        try:
            _, tree = parse_source(conftest)
        except SourceError as exc:
            problems.append(str(exc))
            continue
        names, lines = conftest_registrations(tree)
        out |= names
        if unresolved is not None:
            unresolved.extend(f"{conftest}:{line}" for line in lines)
    if problems:
        raise UnparsedFilesError("conftest file(s) could not be parsed, so their marker registrations are unknown:\n  " + "\n  ".join(problems))
    return out


def used_markers(tests_dir: Path, repo_root: Path, *, exclude: Iterable[Path] = ()) -> dict[str, list[str]]:
    """``{marker: [files that name it]}`` for every ``pytest.mark.<name>`` under *tests_dir*."""
    skip = {p.resolve() for p in exclude}
    seen: dict[str, list[str]] = {}
    problems: list[str] = []
    for path in iter_files(tests_dir, ("*.py",), exclude=DEFAULT_EXCLUDE):
        if path.resolve() in skip:
            continue
        try:
            text = read_source(path)
        except SourceError as exc:
            problems.append(str(exc))
            continue
        for name in sorted(set(_MARK_RE.findall(text))):
            seen.setdefault(name, []).append(relative_posix(path, repo_root))
    if problems:
        raise UnparsedFilesError("test file(s) could not be read, so their markers are unknown:\n  " + "\n  ".join(problems))
    return seen


def registered_markers(repo_root: Path, *, tests_dir: "Path | None" = None, unresolved: "list[str] | None" = None) -> set[str]:
    """Every marker the repo registers: pyproject (both tables), ``pytest.toml``, the ini files, and the conftests under
    the tests directory plus the root ``conftest.py``."""
    tests = tests_dir or repo_root / "tests"
    return (
        pyproject_markers(repo_root / "pyproject.toml")
        | toml_markers(repo_root)
        | ini_markers(repo_root)
        | conftest_markers(tests, repo_root=repo_root, unresolved=unresolved)
    )


def find_unregistered_markers(
    repo_root: Path,
    *,
    tests_dir: "Path | None" = None,
    extra_registered: Iterable[str] = (),
    exclude: Iterable[Path] = (),
) -> dict[str, list[str]]:
    """Markers used under the tests directory that nothing registers, with the files naming each."""
    tests = tests_dir or repo_root / "tests"
    registered = registered_markers(repo_root, tests_dir=tests) | BUILTIN_MARKERS | set(extra_registered)
    return {name: files for name, files in used_markers(tests, repo_root, exclude=exclude).items() if name not in registered}


def assert_markers_registered(
    repo_root: Path,
    *,
    tests_dir: "Path | None" = None,
    extra_registered: Iterable[str] = (),
    expect_registered: Iterable[str] = (),
    exclude: Iterable[Path] = (),
    min_files: int = 1,
) -> None:
    """Fail on an unregistered marker, on a registration the parsers could not see, and on fewer than ``min_files``
    Python files under the tests directory (a scan that lost its subject).

    ``expect_registered`` names markers the caller KNOWS it registers. If the parsers miss one of them
    (a pyproject schema move, a conftest refactor), the check would otherwise report a clean tree while
    reading nothing -- each local copy guarded that with a smoke test of its own; this is that guard.
    """
    import pytest

    tests = tests_dir or repo_root / "tests"
    exclude = list(exclude)
    skip = {p.resolve() for p in exclude}
    scanned = len([p for p in iter_files(tests, ("*.py",), exclude=DEFAULT_EXCLUDE) if p.resolve() not in skip])
    if scanned < min_files:
        pytest.fail(f"only {scanned} test file(s) found under {tests}; expected at least {min_files}. The scan lost its subject.")
    unresolved: list[str] = []
    registered = registered_markers(repo_root, tests_dir=tests, unresolved=unresolved)
    missing = sorted(set(expect_registered) - registered)
    if missing:
        pytest.fail(f"expected registered marker(s) not found by the parser: {missing} -- pyproject or conftest moved, or the parser broke")
    unregistered = find_unregistered_markers(repo_root, tests_dir=tests, extra_registered=extra_registered, exclude=exclude)
    if unregistered:
        pytest.fail(
            "Unregistered pytest markers (an error under --strict-markers, a silent no-op without it):\n"
            + "\n".join(f"  {name}: {files[:5]}" for name, files in sorted(unregistered.items()))
            + "\nRegister them in pyproject.toml [tool.pytest.ini_options].markers or a conftest pytest_configure."
            + (
                "\nThese addinivalue_line registrations could not be read statically and may register some of them "
                "(use literal strings, or pass the names in extra_registered):\n  " + "\n  ".join(unresolved)
                if unresolved
                else ""
            )
        )
