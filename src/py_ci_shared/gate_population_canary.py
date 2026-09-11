"""Shared harness: a pattern-matching gate must prove it still looks at something, and still matches it.

A gate reporting zero findings is either healthy ("I read 340 files and none offended") or inert ("I read
nothing"), and in a green suite the two are indistinguishable. glossum's 2026-09-01 audit, finding 06-F01: the
SDK-mock gate's pattern required ``(`` immediately after ``create``, so ``client.messages.create = AsyncMock(...)``
never entered its population. The gate matched exactly one file in the tree, that file sat in an exempt directory,
and so it reported an empty offending set while the defect it existed to catch sat untouched.

The distinguishing quantity is the candidate population -- the files the gate SCANNED, not the ones that matched --
plus a positive control for each pattern. Both are mechanical, and neither is specific to one repository.

What a gate module declares to opt in:

* ``_candidate_files()`` (or ``_CANDIDATE_FILES``): the files it walks. Non-empty is the whole assertion: a gate
  walking ``glossum/**/*.py`` has a population even in a repo where nothing matches.
* ``_CANARY``: strings at least one of the module's compiled patterns must match. For the gate above the set is
  ``(".messages.create(", ".messages.create = AsyncMock(", ".chat.completions.create(")`` -- the middle string is
  the one that would have failed on the day the gate was written.
* ``_EXPECTED_EMPTY_POPULATION = "<reason>"``: for a gate whose population is deliberately a filtered subset that
  can be empty today. It turns an invisible no-op into a recorded decision, and the reason has to be written out.

Usage in a consumer's ``tests/test_meta``::

    from py_ci_shared.gate_population_canary import (
        assert_every_gate_declares_its_population, assert_population_is_not_empty, assert_canary_is_matched,
        gate_canaries,
    )

    _HERE = Path(__file__).resolve().parent

    def test_every_baselined_gate_declares_its_population():
        assert_every_gate_declares_its_population(_HERE)

    @pytest.mark.parametrize("path", sorted(_HERE.glob("test_*.py")), ids=lambda p: p.name)
    def test_each_gate_examined_something(path):
        assert_population_is_not_empty(path)

    @pytest.mark.parametrize(("path", "canary"), gate_canaries(_HERE), ids=lambda v: getattr(v, "name", v))
    def test_each_declared_canary_is_matched(path, canary):
        assert_canary_is_matched(path, canary)
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

CANDIDATE_FUNCTION = "_candidate_files"
CANDIDATE_CONSTANT = "_CANDIDATE_FILES"
CANARY = "_CANARY"
EXPECTED_EMPTY = "_EXPECTED_EMPTY_POPULATION"
OFFENDING_FUNCTION = "_build_offending_set"
MIN_REASON_LENGTH = 20


def _module_level_names(path: Path) -> set[str]:
    """Every name a module binds at module level: functions, classes and plain assignments.

    Read from the source rather than by importing, so the convention check costs one parse per gate and cannot be
    defeated by a module that fails to import in the checking environment.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def gate_modules(tests_dir: Path) -> list[Path]:
    """The ``test_*.py`` files of *tests_dir*, sorted: the gates a consumer parametrizes over."""
    return sorted(p for p in tests_dir.glob("test_*.py") if p.is_file())


def find_gates_without_population(tests_dir: Path, exempt: frozenset[str] = frozenset()) -> list[str]:
    """Gate modules that build an offending set without declaring the population they built it from."""
    declared = {CANDIDATE_FUNCTION, CANDIDATE_CONSTANT, EXPECTED_EMPTY}
    return [
        path.name
        for path in gate_modules(tests_dir)
        if path.name not in exempt and OFFENDING_FUNCTION in (names := _module_level_names(path)) and not (names & declared)
    ]


def load_gate(path: Path) -> ModuleType:
    """Import a gate module from its path, under a name that cannot collide with the real test module."""
    spec = importlib.util.spec_from_file_location(f"_gate_canary_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _population(module: ModuleType) -> list[Any] | None:
    """The files a gate says it walks, or None when it declares none."""
    builder = getattr(module, CANDIDATE_FUNCTION, None)
    if callable(builder):
        return list(builder())
    constant = getattr(module, CANDIDATE_CONSTANT, None)
    return None if constant is None else list(constant)


def population_problem(path: Path) -> str | None:
    """Why *path*'s gate examines nothing, or None when it examines something (or says why it cannot)."""
    module = load_gate(path)
    reason = getattr(module, EXPECTED_EMPTY, None)
    if isinstance(reason, str):
        if len(reason.strip()) < MIN_REASON_LENGTH:
            return f"{path.name}: {EXPECTED_EMPTY} needs a written reason, not {reason!r}"
        return None
    candidates = _population(module)
    if candidates is None:
        return None  # not an opted-in gate; the convention check is what reports those
    if not candidates:
        return (
            f"{path.name}: the gate examined 0 files, so its empty result says nothing about the tree. Fix what it "
            f"walks, or declare {EXPECTED_EMPTY} with the reason its population is legitimately a filtered subset."
        )
    return None


def compiled_patterns(module: ModuleType) -> dict[str, re.Pattern[str]]:
    """The module-level compiled patterns of *module*, by attribute name."""
    return {name: value for name, value in vars(module).items() if isinstance(value, re.Pattern) and not name.startswith("__")}


def gate_canaries(tests_dir: Path) -> list[tuple[Path, str]]:
    """``(gate, canary)`` for every string a gate declares its patterns must match."""
    out: list[tuple[Path, str]] = []
    for path in gate_modules(tests_dir):
        if CANARY not in _module_level_names(path):
            continue
        out.extend((path, canary) for canary in getattr(load_gate(path), CANARY, ()))
    return out


def canary_problem(path: Path, canary: str) -> str | None:
    """Why no pattern in *path*'s gate matches *canary*, or None when one does."""
    patterns = compiled_patterns(load_gate(path))
    if not patterns:
        return f"{path.name}: declares {CANARY} but compiles no module-level pattern to match it against"
    if any(pattern.search(canary) for pattern in patterns.values()):
        return None
    return f"{path.name}: no pattern of {sorted(patterns)} matches its own canary {canary!r}; the gate no longer sees what it was written for"


def assert_every_gate_declares_its_population(tests_dir: Path, exempt: frozenset[str] = frozenset()) -> None:
    """Fail on a gate that builds an offending set without saying which files it built it from."""
    import pytest

    missing = find_gates_without_population(tests_dir, exempt)
    if missing:
        pytest.fail(
            f"{len(missing)} gate(s) define {OFFENDING_FUNCTION}() with no {CANDIDATE_FUNCTION}() / "
            f"{CANDIDATE_CONSTANT} / {EXPECTED_EMPTY}: an empty result from them cannot be told from an empty "
            f"population:\n  " + "\n  ".join(missing)
        )


def assert_population_is_not_empty(path: Path) -> None:
    """Fail when a gate examined no files at all."""
    import pytest

    problem = population_problem(path)
    if problem:
        pytest.fail(problem)


def assert_canary_is_matched(path: Path, canary: str) -> None:
    """Fail when a gate's own positive control no longer matches its pattern."""
    import pytest

    problem = canary_problem(path, canary)
    if problem:
        pytest.fail(problem)
