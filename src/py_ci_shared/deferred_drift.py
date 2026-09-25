"""Deferred-debt lists in a meta-test directory may not grow, and a list that shrank must say so.

Every meta-test that tolerates known debt keeps it in a named collection (``_USER_DEFERRED_*``,
``_GRANDFATHERED``, a repo's own ``_KNOWN_*``). Adding an entry costs nothing and removing one costs a fix, so
left alone they only grow. mlframe, pyutilz and production_scrapers each carried the same ~90-line tracker over
``pyutilz.dev.meta_test_utils.count_user_deferred_entries``; this is that tracker, once.

It is stricter than the copies in one way, and deliberately. They reported a SHRUNK list on stderr and passed,
so the baseline kept the old, higher count and the list could grow back to it unnoticed -- the same hole
``loc_budget`` closed for grandfathered files. Here a list that shrank, or disappeared, fails until the baseline
is refreshed to the smaller number (``fail_on_shrink=False`` restores the old behaviour).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import Baseline, BaselineError, BaselineGrowthError, dump_json, refresh_requested, write_ratchet

__all__ = ["DEFAULT_REFRESH_FLAG", "assert_deferred_lists_not_grown", "drift_problems"]

DEFAULT_REFRESH_FLAG = "--refresh-debt-baseline"


def drift_problems(baseline: dict[str, int], current: dict[str, int], *, fail_on_shrink: bool = True) -> list[str]:
    """Every way *current* disagrees with *baseline*, one line each. Pure, so each rule is testable on its own."""
    problems: list[str] = []
    for key, count in sorted(current.items()):
        old = baseline.get(key)
        if old is None:
            problems.append(f"NEW list {key}: {count} entr(ies)")
        elif count > old:
            problems.append(f"GREW {key}: {old} -> {count} (+{count - old})")
        elif fail_on_shrink and count < old:
            problems.append(f"SHRANK {key}: {old} -> {count} -- lower the baseline so it cannot grow back")
    if fail_on_shrink:
        problems.extend(f"GONE {key}: the list no longer exists -- drop it from the baseline" for key in sorted(set(baseline) - set(current)))
    return problems


def assert_deferred_lists_not_grown(
    meta_dir: Path,
    baseline_path: Path,
    *,
    extra_prefixes: Iterable[str] = (),
    fail_on_shrink: bool = True,
    refresh_flag: str = DEFAULT_REFRESH_FLAG,
    min_lists: int = 1,
    refresh: Optional[bool] = None,
    request: Any = None,
) -> None:
    """Compare every deferred list under *meta_dir* with *baseline_path* and fail on any drift.

    A refresh (*refresh*, *refresh_flag* on the pytest command line or in ``request.config``, or the
    ``PY_CI_SHARED_REFRESH`` env var, which xdist workers inherit) writes the current counts and skips. A missing
    baseline FAILS: it is only created by a refresh. *min_lists* is the floor, checked before anything is written:
    a counter that finds no list at all has stopped reading the directory.
    """
    import pytest
    from pyutilz.dev.meta_test_utils import count_user_deferred_entries

    current = count_user_deferred_entries(meta_dir, extra_prefixes=tuple(extra_prefixes))
    if len(current) < min_lists:
        pytest.fail(f"only {len(current)} deferred list(s) found under {meta_dir}; expected at least {min_lists} -- the counter lost its subject")
    if refresh if refresh is not None else refresh_requested(refresh_flag, request):
        previous = dict(Baseline(baseline_path, gate="deferred-debt").load()[0]) if Path(baseline_path).is_file() else None
        try:
            write_ratchet(baseline_path, dict(current), gate="deferred-debt", previous=previous, render=dump_json, request=request)
        except BaselineGrowthError as exc:
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(f"debt baseline written: {len(current)} list(s), {sum(current.values())} entr(ies) in {baseline_path.name}")
    if not Path(baseline_path).is_file():
        pytest.fail(f"debt baseline {baseline_path} does not exist, so nothing was compared. Create it with: pytest ... {refresh_flag}")
    try:
        baseline = dict(Baseline(baseline_path, gate="deferred-debt").load()[0])
    except BaselineError as exc:
        pytest.fail(str(exc))
    problems = drift_problems(baseline, current, fail_on_shrink=fail_on_shrink)
    if problems:
        net = sum(current.values()) - sum(baseline.values())
        pytest.fail(
            f"deferred-debt lists drifted from {baseline_path.name} (net {net:+d} entries):\n    "
            + "\n    ".join(problems)
            + f"\nDrain the lists that grew, or refresh after an intentional change: pytest ... {refresh_flag}"
        )
