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
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import DEFAULT_EXCLUDE, Baseline, Finding, ScanResult, refresh_requested, scan_python

__all__ = [
    "REFRESH_FLAG",
    "assert_no_value_bearing_asserts",
    "collect_value_bearing_asserts",
    "find_value_bearing_asserts",
    "is_narrowing_assert",
]

REFRESH_FLAG = "--refresh-value-asserts-baseline"
RULE = "value-bearing-assert"
#: Scoping for THIS gate (test/probe/script code may assert values freely), on top of ``_core.DEFAULT_EXCLUDE``.
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "probes", "scripts", ".venv", "venv", "__pycache__", "build", "dist"})
#: Keys written before 2026-09-24 truncated the expression to this many characters; still honoured when read.
_LEGACY_KEY_EXPR_LEN = 90


def is_narrowing_assert(test: ast.expr, *, allow_isinstance: bool = False, strict: bool = False) -> bool:
    """True for the forms that exist to narrow a type and carry no runtime meaning.

    With *strict*, a bare name/attribute/subscript (``assert self.enabled``) is a VALUE check -- it tests
    truthiness, which ``-O`` deletes -- and only ``is None``/``is not None`` (and ``and``/``or`` of them) narrow.
    """
    if isinstance(test, ast.BoolOp):
        return all(is_narrowing_assert(value, allow_isinstance=allow_isinstance, strict=strict) for value in test.values)
    if isinstance(test, (ast.Name, ast.Attribute, ast.Subscript)):
        return not strict
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
        return isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None
    if allow_isinstance and isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id in ("isinstance", "callable"):
        return True
    return False


def _key(rel: str, expr: str) -> str:
    """``rel::expr`` -- the baseline key, stable across line shifts, full expression (no truncation)."""
    return f"{rel}::{expr}"


def collect_value_bearing_asserts(
    package_root: Path,
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    allow_isinstance: bool = False,
    strict: bool = False,
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], int, ScanResult]:
    """``(findings, asserts seen, scan)``. One Finding per assert: two identical asserts are two findings."""
    scan = scan_python(package_root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    seen = 0
    for f in scan:
        for node in ast.walk(f.tree):
            if isinstance(node, ast.Assert):
                seen += 1
                if not is_narrowing_assert(node.test, allow_isinstance=allow_isinstance, strict=strict):
                    expr = ast.unparse(node.test)
                    findings.append(Finding(f.rel, node.lineno, RULE, expr, key=_key(f.rel, expr)))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, seen, scan


def find_value_bearing_asserts(
    package_root: Path,
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    allow_isinstance: bool = False,
    strict: bool = False,
    use_git: Optional[bool] = None,
) -> tuple[list[str], int]:
    """``(["rel:lineno  expr", ...], asserts seen)`` over the production files under *package_root*.

    A file that could not be read or parsed is listed as ``rel:line  <unparsable: why>`` instead of being
    skipped: a BOM or a syntax error used to return ``([], 0)`` for that file.
    """
    findings, seen, scan = collect_value_bearing_asserts(
        package_root, exclude_parts=exclude_parts, allow_isinstance=allow_isinstance, strict=strict, use_git=use_git
    )
    out = [f"{f.path}:{f.line}  {f.message}" for f in findings] + [f"{p.rel}:{p.line}  <{p.kind}: {p.message}>" for p in scan.unparsed]
    return out, seen


def _with_legacy_keys(findings: list[Finding], baseline: Baseline) -> list[Finding]:
    """Map a finding onto its pre-2026-09-24 truncated key when only that key is in the baseline."""
    if not baseline.exists():
        return findings
    accepted = baseline.load()[0]
    out = []
    for f in findings:
        legacy = _key(f.path, f.message[:_LEGACY_KEY_EXPR_LEN])
        if len(f.message) > _LEGACY_KEY_EXPR_LEN and f.key not in accepted and legacy in accepted:
            f = Finding(f.path, f.line, f.rule, f.message, key=legacy)
        out.append(f)
    return out


def assert_no_value_bearing_asserts(
    package_root: Path,
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    allow_isinstance: bool = False,
    min_asserts_seen: int = 1,
    baseline_path: "Path | None" = None,
    strict: bool = False,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a value-bearing assert not in *baseline_path*, and on a baseline entry that no longer matches one.

    *min_asserts_seen* and *min_files* are floors: a walk that found no asserts, or parsed no files, is a broken
    walk, not clean code. An unparsable file fails the gate. The baseline is a MULTISET: accepting one
    ``assert n > 0`` does not accept a second copy. A missing *baseline_path* FAILS (it used to be written and
    skipped, which turned a wrong path into a permanent pass); it is written only when a refresh is requested:
    ``refresh=True``, ``REFRESH_FLAG`` via the pytest *request*/command line, or env ``PY_CI_SHARED_REFRESH``
    naming ``value-asserts`` or ``all``. An entry whose note starts with ``NEEDS-JUSTIFICATION`` fails.
    """
    import pytest

    findings, seen, scan = collect_value_bearing_asserts(
        package_root, exclude_parts=exclude_parts, allow_isinstance=allow_isinstance, strict=strict, use_git=use_git
    )
    scan.min_files = min_files
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if seen < min_asserts_seen:
        problems.append(f"only {seen} assert(s) seen under {package_root}; expected at least {min_asserts_seen} -- the walk is not reaching the code")
    if scan.unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so their asserts were not checked:\n    " + "\n    ".join(p.render() for p in scan.unparsed)
        )
    guidance = "these check a VALUE, so `python -O` deletes the check. Use an explicit `raise`"
    if baseline_path is None:
        if findings:
            problems.append(guidance + ":\n    " + "\n    ".join(f"{f.path}:{f.line}  {f.message}" for f in findings))
        if problems:
            pytest.fail("\n".join(problems))
        return
    if problems:  # never write a baseline from a broken walk
        pytest.fail("\n".join(problems))
    do_refresh = refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request)
    baseline = Baseline(baseline_path, gate="value-asserts", refresh_command=f"pytest {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=value-asserts)")
    outcome = baseline.enforce(
        _with_legacy_keys(findings, baseline),
        refresh=do_refresh,
        describe={f.key: f"{f.path}:{f.line}  {f.message}" for f in findings},
        guidance=guidance,
    )
    outcome.raise_for_pytest()
