"""A test that reads the real clock and shifts it by part of a day fails for part of every day.

A pagination cursor is valid for the UTC day it was minted in. A test minted one against ``time.time()`` and moved the
clock forward an hour, so for the hour before UTC midnight "an hour later" was the next day and the cursor was
correctly refused: the suite failed once a day, every day, for one hour. The fix is to pin both ends to a fixed
instant (or freeze the clock), so the assertion means the same thing whenever the suite runs.

Hit, inside a ``test_*`` function whose name, docstring, identifiers or strings speak of calendar days (``day``,
``today``, ``midnight``, ``cursor``, ...; an hour-shift elsewhere is an mtime or a duration, not a boundary):
``+``/``-`` between a real clock reading (``time.time()``, ``datetime.now()``,
``datetime.utcnow()``, ``datetime.today()``, ``date.today()``, names resolved through ``ImportAliases``, or a local name
assigned from one) and an offset of at least an hour that is NOT a whole number of days (a literal number of seconds,
or ``timedelta(hours=...)``/``minutes=``/``seconds=``). A whole-day shift crosses the boundary from every hour of the
day, so it is deterministic; a shift under an hour cannot cross it from most of the day and is left to the timing
checks. Not reported: a reading normalised to a day (``// 86400``, ``.replace(hour=...)``, ``.date()`` on the same line),
a JWT lifetime claim (``{"exp": time.time() + 3600}`` is true whenever it runs), a test whose clock is frozen
(a ``freeze_time``/``time_machine``/``travel`` decorator, a ``freezer``/``time_machine``/``frozen_time`` fixture, or
``monkeypatch.setattr`` of ``time.time``/``datetime``), and a line marked ``# clock-ok: <reason>``.

The single-shot timing half of the original proposal (``assert elapsed < 5.0``) is pyutilz's ``wall_clock_assertion``
scanner, run by ``code_audit_meta``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "assert_no_clock_day_boundary", "find_clock_day_boundary"]

REFRESH_FLAG = "--refresh-clock-day-boundary-baseline"
GATE = "clock-day-boundary"
RULE = "clock-crosses-day"
_CLOCKS = frozenset({"time.time", "time.time_ns", "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today", "datetime.date.today"})
_HOUR = 3600
_DAY = 86400
_TIMEDELTA_SECONDS = {"weeks": 7 * _DAY, "days": _DAY, "hours": _HOUR, "minutes": 60, "seconds": 1}
_LIFETIME_CLAIMS = frozenset({"exp", "iat", "nbf"})
_NORMALISERS = re.compile(r"//\s*86400|\.replace\(\s*hour|\.date\(\)")
_FREEZER_DECORATORS = re.compile(r"freeze_time|time_machine|travel|freezegun")
_FREEZER_FIXTURES = frozenset({"freezer", "time_machine", "frozen_time", "mock_time", "fake_clock"})
_MARKER = re.compile(r"#\s*clock-ok:\s*\S")
#: Words that say a test is about calendar days: without one, an hour-shift is an mtime or a duration, not a boundary.
_DAY_WORDS = frozenset({"day", "days", "daily", "midnight", "today", "yesterday", "tomorrow", "calendar", "cursor", "utc_day", "dayofweek"})
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _is_clock(node: ast.AST, aliases: ImportAliases, clock_names: set[str]) -> bool:
    if isinstance(node, ast.Name) and node.id in clock_names:
        return True
    if isinstance(node, ast.Call):
        if aliases.qualified_name(node.func) in _CLOCKS:
            return True
        # int(time.time()), float(...), round(...): a conversion keeps the reading a reading
        if isinstance(node.func, ast.Name) and node.func.id in ("int", "float", "round") and node.args:
            return _is_clock(node.args[0], aliases, clock_names)
    return False


def _offset_seconds(node: ast.AST, aliases: ImportAliases) -> Optional[float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(abs(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _offset_seconds(node.operand, aliases)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _offset_seconds(node.left, aliases), _offset_seconds(node.right, aliases)
        return left * right if left is not None and right is not None else None
    if isinstance(node, ast.Call) and aliases.qualified_name(node.func) == "datetime.timedelta":
        total = 0.0
        for kw in node.keywords:
            factor = _TIMEDELTA_SECONDS.get(kw.arg or "")
            value = _offset_seconds(kw.value, aliases)
            if factor is None or value is None:
                return None
            total += value * factor
        if node.args:
            days = _offset_seconds(node.args[0], aliases)
            if days is None:
                return None
            total += days * _DAY
        return total
    return None


def _crosses_part_of_a_day(seconds: Optional[float]) -> bool:
    return seconds is not None and seconds >= _HOUR and seconds % _DAY != 0


def _is_frozen(fn: _FunctionNode, aliases: ImportAliases) -> bool:
    if any(_FREEZER_DECORATORS.search(ast.unparse(d)) for d in fn.decorator_list):
        return True
    if any(a.arg in _FREEZER_FIXTURES for a in fn.args.args):
        return True
    for n in _fast_walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "setattr" and n.args:
            target = ast.unparse(n.args[0]) + (ast.unparse(n.args[1]) if len(n.args) > 1 else "")
            if "time" in target or "datetime" in target:
                return True
    return False


def _text_of(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _speaks_of_days(fn: _FunctionNode) -> bool:
    """The test's name, docstring, identifiers or strings use a calendar-day word (split on ``_`` and non-letters)."""
    words = {w for n in _fast_walk(fn) for w in re.split(r"[^a-z]+", _text_of(n).lower()) if w}
    return bool(words & _DAY_WORDS)


def _clock_names(fn: _FunctionNode, aliases: ImportAliases) -> set[str]:
    names: set[str] = set()
    for n in _fast_walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and _is_clock(n.value, aliases, names):
            names.add(n.targets[0].id)
    return names


def _in_lifetime_claim(fn: _FunctionNode, node: ast.AST) -> bool:
    for holder in _fast_walk(fn):
        if isinstance(holder, ast.Dict):
            for key, value in zip(holder.keys, holder.values):
                if isinstance(key, ast.Constant) and key.value in _LIFETIME_CLAIMS and any(sub is node for sub in _fast_walk(value)):
                    return True
    return False


def _fn_findings(rel: str, fn: _FunctionNode, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    if not fn.name.startswith("test") or _is_frozen(fn, aliases) or not _speaks_of_days(fn):
        return []
    clocks = _clock_names(fn, aliases)
    out: list[Finding] = []
    for node in _fast_walk(fn):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))):
            continue
        for reading, offset in ((node.left, node.right), (node.right, node.left)):
            if not _is_clock(reading, aliases, clocks) or not _crosses_part_of_a_day(_offset_seconds(offset, aliases)):
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if _NORMALISERS.search(line) or _MARKER.search(line) or _in_lifetime_claim(fn, node):
                continue
            message = f"{fn.name} shifts the real clock by part of a day (`{ast.unparse(node)[:80]}`): pin both ends to a fixed instant"
            out.append(Finding(rel, node.lineno, RULE, message))
            break
    return out


def find_clock_day_boundary(
    tests_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = (),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` over the test files under *tests_root*."""
    scan = scan_python(tests_root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        aliases = ImportAliases.from_tree(f.tree)
        lines = f.source.splitlines()
        for fn in (n for n in _fast_walk(f.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            findings.extend(_fn_findings(f.rel, fn, aliases, lines))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_no_clock_day_boundary(
    tests_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = (),
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding not accepted by *baseline_path*. Missing baseline fails; refresh with ``REFRESH_FLAG`` or
    ``PY_CI_SHARED_REFRESH=clock-day-boundary``."""
    findings, scan = find_clock_day_boundary(tests_root, exclude_parts=exclude_parts, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="pin both instants (or freeze the clock) so the test means the same thing at 23:30 UTC",
    )
