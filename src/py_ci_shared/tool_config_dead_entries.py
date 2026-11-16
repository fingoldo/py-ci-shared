"""A pyproject ignore, omit or override must name something that exists.

deptry's ignore list has no expiry, and a glob in ``[tool.ruff.lint.per-file-ignores]``, ``[tool.coverage.run] omit`` or a
first-party ``[[tool.mypy.overrides]]`` pattern silently stops matching after a directory rename. Each such entry looks
like it protects something and does not (mlframe test_deptry_ignores_not_stale, 2026-07-18).

Checked entries:

* ``[tool.ruff.lint.per-file-ignores]`` keys and ``[tool.coverage.run] omit`` globs must match at least one tracked file;
* ``[tool.deptry.per_rule_ignores] DEP002`` names must be a declared dependency (project, optional or dependency group),
  compared with PEP 503 name normalisation;
* ``[[tool.mypy.overrides]] module`` patterns that look first-party (their top segment is a package under the root or
  ``src``) must match a module file.

Glob semantics: ``**/`` matches zero or more directories, ``**`` any path, ``*`` one segment's characters, ``?`` one
character. Boilerplate patterns (``__pycache__``, ``.venv``, ``site-packages``) are not checked, and neither are
``exclude`` lists, which are defensive by design.

``DEFAULT_EXEMPTIONS`` is the reasoned allow list: an entry named there is accepted with its reason. The cupy DEP002 entry
is one: deptry's DEP002 compares distribution names, but the project declares ``cupy-cuda12x`` and the ignore names the
import name, which is an owner decision. Pass ``exemptions={}`` to see every entry without the allow list.

Usage::

    from py_ci_shared.tool_config_dead_entries import assert_tool_config_dead_entries

    def test_pyproject_ignores_are_live():
        assert_tool_config_dead_entries(".")
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional, Union

from ._core import EmptyScanError, Finding
from ._core.corpus import iter_files_pruned
from ._core.git import git_output
from ._toml_compat import tomllib

__all__ = [
    "DEFAULT_EXEMPTIONS",
    "RULE",
    "assert_tool_config_dead_entries",
    "find_tool_config_dead_entries",
    "glob_matches",
]

RULE = "tool-config-dead-entry"
_BOILERPLATE = ("__pycache__", ".venv", "site-packages")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DEFAULT_EXEMPTIONS: dict[str, str] = {
    "cupy": (
        "deptry DEP002 compares distribution names; the project declares cupy-cuda12x and the ignore names the import name "
        "cupy, which is an owner decision (50-T9), so it is accepted here with that reason"
    ),
}


def _glob_regex(pattern: str, star_crosses_dirs: bool) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append(".*" if star_crosses_dirs else "[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def glob_matches(pattern: str, rel: str, *, star_crosses_dirs: bool = False) -> bool:
    """True when the posix path *rel* matches *pattern* in full (``**/`` = zero or more directories).

    ``*`` stays inside one path segment, as ruff's globs do; coverage's ``omit`` lets ``*`` cross directories, so
    its callers pass ``star_crosses_dirs=True``."""
    return re.fullmatch(_glob_regex(pattern, star_crosses_dirs), rel) is not None


def _ruff_matches(pattern: str, rel: str) -> bool:
    """A ruff per-file-ignore key with no ``/`` matches a file name in any directory, as ruff does."""
    if "/" not in pattern:
        return glob_matches(pattern, rel.rsplit("/", 1)[-1])
    return glob_matches(pattern, rel)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared(pyproject: dict[str, Any]) -> set[str]:
    project = pyproject.get("project", {})
    specs: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.extend(group)
    for group in pyproject.get("dependency-groups", {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    names: set[str] = set()
    for spec in specs:
        match = _NAME.match(spec.strip())
        if match:
            names.add(_normalise(match.group(0)))
    return names


def _line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return 1


def _module_names(root: Path) -> set[str]:
    names: set[str] = set()
    for base in (root / "src", root):
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(base).as_posix()
            if any(part in _BOILERPLATE for part in rel.split("/")):
                continue
            names.add(rel[: -len(".py")].replace("/", ".").removesuffix(".__init__"))
    return names


def _first_party(module: str, names: Iterable[str]) -> bool:
    top = module.split(".")[0].rstrip("*")
    return any(n == top or n.startswith(top + ".") for n in names)


def _mypy_matches(pattern: str, names: Iterable[str]) -> bool:
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return any(n == prefix or n.startswith(prefix + ".") for n in names)
    return pattern in names


def _tracked(root: Path, use_git: Optional[bool]) -> list[str]:
    if use_git is False:
        return sorted(p.relative_to(root).as_posix() for p in iter_files_pruned(root))
    try:
        return sorted(line for line in git_output(root, "ls-files").splitlines() if line)
    except Exception:
        return sorted(p.relative_to(root).as_posix() for p in iter_files_pruned(root))


def find_tool_config_dead_entries(
    root: Union[str, Path],
    *,
    exemptions: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every checked pyproject entry under *root* that names nothing that exists, minus the reasoned exemptions."""
    base = Path(root)
    files = _tracked(base, use_git)
    if len(files) < min_files:
        raise EmptyScanError(f"tool_config_dead_entries: {len(files)} tracked file(s) under {base}, need {min_files}")
    text = (base / "pyproject.toml").read_text(encoding="utf-8-sig")
    pyproject = tomllib.loads(text)
    allowed = {_normalise(k) for k in (DEFAULT_EXEMPTIONS if exemptions is None else exemptions)}
    tool = pyproject.get("tool", {})
    out: list[Finding] = []

    def dead(entry: str, why: str) -> None:
        out.append(Finding("pyproject.toml", _line_of(text, f'"{entry}"'), RULE, f"`{entry}` {why}; delete it or fix the pattern"))

    for pattern in _unmatched_globs(tool.get("ruff", {}).get("lint", {}).get("per-file-ignores", {}), files, _ruff_matches):
        dead(pattern, "is a ruff per-file-ignore that matches no tracked file")
    coverage_matches = functools.partial(glob_matches, star_crosses_dirs=True)
    for pattern in _unmatched_globs(tool.get("coverage", {}).get("run", {}).get("omit", []), files, coverage_matches):
        dead(pattern, "is a coverage omit glob that matches no tracked file")
    for name in _undeclared_deptry(tool, pyproject, allowed):
        dead(name, "is a deptry DEP002 ignore for a distribution that is not declared")
    for module in _unmatched_overrides(tool, _module_names(base)):
        dead(module, "is a mypy override for a first-party module that matches no file")

    return sorted(out, key=lambda f: (f.path, f.line))


def _unmatched_globs(patterns: Iterable[str], files: list[str], matches: Any) -> list[str]:
    """The glob entries (boilerplate excluded) that match no tracked file under the tool's *matches* rule."""
    return [p for p in patterns if not any(part in p for part in _BOILERPLATE) and not any(matches(p, f) for f in files)]


def _undeclared_deptry(tool: dict[str, Any], pyproject: dict[str, Any], allowed: set[str]) -> list[str]:
    """The DEP002 names that are neither declared nor on the reasoned allow list."""
    declared = _declared(pyproject)
    names = tool.get("deptry", {}).get("per_rule_ignores", {}).get("DEP002", [])
    return [n for n in names if _normalise(n) not in allowed and _normalise(n) not in declared]


def _unmatched_overrides(tool: dict[str, Any], names: set[str]) -> list[str]:
    """The first-party mypy override modules that match no module file."""
    modules = [m for override in tool.get("mypy", {}).get("overrides", []) for m in ([override.get("module", [])] if isinstance(override.get("module", []), str) else override.get("module", []))]
    return [m for m in modules if _first_party(m, names) and not _mypy_matches(m, names)]


def assert_tool_config_dead_entries(
    root: Union[str, Path],
    *,
    exemptions: Optional[Mapping[str, str]] = None,
    min_files: int = 1,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any pyproject ignore, omit or override that names nothing."""
    found = find_tool_config_dead_entries(root, exemptions=exemptions, min_files=min_files, use_git=use_git)
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
