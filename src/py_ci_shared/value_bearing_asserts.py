"""No guard that checks a VALUE may ride on an ``assert`` in production code.

``python -O`` deletes every ``assert``. A guard that only narrows a type for mypy loses nothing when it goes;
a guard that checks a sum, a bound, a membership or a minimum loses the only thing enforcing it, and the code
after it runs on a value it was never allowed to see. production_scrapers paid for that twice: its CHANGELOG
(wave-7) records an ``-O``-stripped guard collapsing a scan into INSERT-only mode and dropping every renewed
job as ``claim_lost``, silently. realtime_applications then converted its three sites and wrote the ratchet
this module generalises (audit 2026-09-08 OPT-1); no other repository had one.

The rule is scoped by MEANING, not by count: ``assert x is not None`` (and a bare name, attribute or subscript,
and ``and``/``or`` of those) stays legal, because narrowing is what ``assert`` is for. ``isinstance`` and
``callable`` are value checks by default -- under ``-O`` they vanish too, and a caller passing the wrong type
then gets a wrong answer rather than an error -- but a repository whose ``isinstance`` asserts exist only for
mypy can pass ``allow_isinstance=True``.

There is no runtime form of "no module contains a value-bearing assert": under ``-O`` the statement is absent
from the bytecode. The subject is the source.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "REFRESH_FLAG",
    "assert_no_value_bearing_asserts",
    "find_value_bearing_asserts",
    "is_narrowing_assert",
]

REFRESH_FLAG = "--refresh-value-asserts-baseline"
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "probes", "scripts", ".venv", "venv", "__pycache__", "build", "dist"})


def is_narrowing_assert(test: ast.expr, *, allow_isinstance: bool = False) -> bool:
    """True for the forms that exist to narrow a type and carry no runtime meaning."""
    if isinstance(test, ast.BoolOp):
        return all(is_narrowing_assert(value, allow_isinstance=allow_isinstance) for value in test.values)
    if isinstance(test, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
        return isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None
    if allow_isinstance and isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id in ("isinstance", "callable"):
        return True
    return False


def _production_files(package_root: Path, exclude_parts: Iterable[str]) -> list[Path]:
    skip = set(exclude_parts)
    return sorted(p for p in package_root.rglob("*.py") if not (skip & set(p.relative_to(package_root).parts)))


def find_value_bearing_asserts(
    package_root: Path,
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    allow_isinstance: bool = False,
) -> tuple[list[str], int]:
    """``(["rel:lineno  expr", ...], asserts seen)`` over the production files under *package_root*."""
    offenders: list[str] = []
    seen = 0
    for path in _production_files(package_root, exclude_parts):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(package_root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                seen += 1
                if not is_narrowing_assert(node.test, allow_isinstance=allow_isinstance):
                    offenders.append(f"{rel}:{node.lineno}  {ast.unparse(node.test)[:90]}")
    return offenders, seen


def _key(entry: str) -> str:
    """``rel::expr`` -- the baseline key, stable across line shifts."""
    location, expr = entry.split("  ", 1)
    return f"{location.rsplit(':', 1)[0]}::{expr}"


def assert_no_value_bearing_asserts(
    package_root: Path,
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    allow_isinstance: bool = False,
    min_asserts_seen: int = 1,
    baseline_path: Path | None = None,
) -> None:
    """Fail on a value-bearing assert not in *baseline_path*, and on a baseline entry that no longer matches one.

    *min_asserts_seen* is the floor: a walk that found no asserts at all is a broken walk, not clean code. A
    missing baseline, or ``REFRESH_FLAG`` on the pytest command line, writes today's findings and skips.
    """
    import pytest

    offenders, seen = find_value_bearing_asserts(package_root, exclude_parts=exclude_parts, allow_isinstance=allow_isinstance)
    if seen < min_asserts_seen:
        pytest.fail(f"only {seen} assert(s) seen under {package_root}; expected at least {min_asserts_seen} -- the walk is not reaching the code")
    current = {_key(o): o for o in offenders}
    if baseline_path is not None and (REFRESH_FLAG in sys.argv or not baseline_path.exists()):
        baseline_path.write_text(json.dumps(sorted(current), indent=2) + "\n", encoding="utf-8")
        pytest.skip(f"value-assert baseline written: {len(current)} entr(ies) in {baseline_path.name}")
    accepted = set(json.loads(baseline_path.read_text(encoding="utf-8"))) if baseline_path is not None else set()
    new = sorted(v for k, v in current.items() if k not in accepted)
    stale = sorted(accepted - set(current))
    problems = []
    if new:
        problems.append("these check a VALUE, so `python -O` deletes the check. Use an explicit `raise`:\n    " + "\n    ".join(new))
    if stale:
        problems.append(f"baseline entr(ies) that no longer match an assert; remove them (or pass {REFRESH_FLAG}):\n    " + "\n    ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
