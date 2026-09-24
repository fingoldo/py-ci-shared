"""The ``assert_*`` tail every AST gate shares: floor, unparsed files, then a baseline ratchet or a zero-tolerance fail.

A gate computes its findings over a :class:`~py_ci_shared._core.ScanResult` and hands both here. The order is fixed:
a walk that parsed too few files, or could not parse some, fails BEFORE the baseline is consulted, so a broken walk can
never rewrite a baseline or pass on nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, Finding, ScanResult, refresh_requested

__all__ = ["enforce_findings", "scan_problems"]


def scan_problems(scan: ScanResult, *, min_files: int) -> list[str]:
    """The floor (parsed files) and the unparsed list as messages; empty when the walk is sound."""
    scan.min_files = min_files
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    try:
        scan.check_unparsed()
    except AssertionError as exc:
        problems.append(str(exc))
    return problems


def enforce_findings(
    findings: Sequence[Finding],
    scans: Union[ScanResult, Iterable[ScanResult]],
    *,
    gate: str,
    flag: str,
    min_files: int,
    baseline_path: Optional[Union[str, Path]],
    refresh: Optional[bool],
    request: Optional[Any],
    guidance: str,
    extra_problems: Iterable[str] = (),
) -> None:
    """Fail the calling test on a broken walk, then on findings (zero tolerance, or new against *baseline_path*).

    *flag* is the pytest refresh option (``--refresh-<gate>-baseline``); ``PY_CI_SHARED_REFRESH=<gate>`` works too.
    A missing baseline fails and names the refresh command; it is written only when a refresh is requested.
    """
    import pytest

    scan_list = [scans] if isinstance(scans, ScanResult) else list(scans)
    problems = list(extra_problems)
    for scan in scan_list:
        problems.extend(scan_problems(scan, min_files=min_files))
    if baseline_path is None:
        if findings:
            problems.append(guidance + ":\n    " + "\n    ".join(f.render() for f in findings))
        if problems:
            pytest.fail(f"{gate}:\n" + "\n".join(problems), pytrace=False)
        return
    if problems:  # never write a baseline from a broken walk
        pytest.fail(f"{gate}:\n" + "\n".join(problems), pytrace=False)
    do_refresh = refresh if refresh is not None else refresh_requested(flag, request)
    baseline = Baseline(baseline_path, gate=gate, refresh_command=f"pytest {flag} (or PY_CI_SHARED_REFRESH={gate})")
    baseline.enforce(findings, refresh=do_refresh, guidance=guidance).raise_for_pytest()
