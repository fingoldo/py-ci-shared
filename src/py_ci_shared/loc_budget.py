"""Shared harness for the "no file over N LOC" meta-test pattern.

realtime_applications and mlframe each independently wrote a near-identical
~75-line pytest test: walk production ``.py`` files, flag anything over a
line-count ceiling, grandfather pre-existing offenders via a committed
baseline JSON (path -> LOC at capture time) so existing debt doesn't block
adoption, and allow a small per-file growth slack so trivial edits to an
already-oversized file don't trip the gate while a real expansion still
does. This module is that logic, factored out to one place -- mirrors
``code_audit_meta.py``'s exact API shape (lazy imports, a ``--refresh-*``
CLI flag registered via ``register_refresh_option``, ``assert_*`` as the
test body) so a project already using that pattern for code-audit findings
recognizes this one immediately.

Usage (in a consuming repo's ``tests/test_meta/test_no_file_over_1k_loc.py``,
or an equivalent root-level file for flat-layout repos)::

    from pathlib import Path
    from py_ci_shared.loc_budget import assert_no_new_oversized_file

    def _production_py_files():
        # project-specific: whatever "production .py files" means here
        ...

    def test_no_new_file_over_1k_loc():
        assert_no_new_oversized_file(
            files=_production_py_files(),
            root=Path(__file__).resolve().parents[2],
            baseline_path=Path(__file__).resolve().parent / "_loc_over_1k_baseline.json",
        )

And in the same directory's ``conftest.py`` (or the repo's root
conftest.py)::

    from py_ci_shared.loc_budget import register_refresh_option

    def pytest_addoption(parser):
        register_refresh_option(parser)

Deliberately dependency-light: ``pytest`` and ``orjson`` are imported
LAZILY inside the functions below, matching ``code_audit_meta.py``'s own
convention, so importing ``py_ci_shared`` itself never requires them.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

REFRESH_FLAG = "--refresh-loc-budget-baseline"

DEFAULT_LOC_LIMIT = 1000
DEFAULT_GROWTH_SLACK = 50


def register_refresh_option(parser) -> None:
    """Register ``--refresh-loc-budget-baseline`` as a no-op boolean flag.

    Same rationale as ``code_audit_meta.register_refresh_option``: pytest
    rejects unrecognized CLI options before test code runs, so every
    consuming repo's conftest.py must call this from its own
    ``pytest_addoption``.
    """
    try:
        parser.addoption(
            REFRESH_FLAG,
            action="store_true",
            default=False,
            help="rewrite the LOC-budget baseline JSON instead of comparing (intentional split/shrink)",
        )
    except ValueError:
        pass  # already registered (e.g. a repo with more than one conftest.py in the chain)


def _refresh_requested() -> bool:
    import sys

    return REFRESH_FLAG in sys.argv


def _loc(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def assert_no_new_oversized_file(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    limit: int = DEFAULT_LOC_LIMIT,
    growth_slack: int = DEFAULT_GROWTH_SLACK,
) -> None:
    """Fail if any file in ``files`` exceeds ``limit`` lines UNLESS it's
    already in the baseline (grandfathered), or fail if a grandfathered
    file grew by more than ``growth_slack`` lines past its captured size.
    Seeds/refreshes ``baseline_path`` (first run, or the ``--refresh-loc-budget-baseline``
    flag) and ``pytest.skip()``s that run instead of comparing. Call this
    directly as the body of a ``test_*`` function.

    Args:
        files: every production ``.py`` file to consider -- the caller
            decides what "production" means for its own layout (a
            ``src/`` tree, a flat-layout repo root, an exclusion list of
            tests/docs/etc.), this function only applies the LOC policy.
        root: used to compute each file's path RELATIVE to it for the
            baseline keys/messages (so the baseline is portable across
            machines/checkouts, not tied to an absolute path).
        baseline_path: where the baseline JSON lives (and gets written on
            refresh) -- conventionally a sibling ``_loc_over_1k_baseline.json``
            next to the test file.
        limit: LOC ceiling; a file at or under this is never flagged.
        growth_slack: how many lines a grandfathered file may grow before
            it's flagged too -- 0 means any growth at all fails.
    """
    import orjson
    import pytest

    current = {p.relative_to(root).as_posix(): _loc(p) for p in files if _loc(p) > limit}

    if _refresh_requested() or not baseline_path.exists():
        baseline_path.write_text(
            orjson.dumps(dict(sorted(current.items())), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode("utf-8"),
            encoding="utf-8",
        )
        pytest.skip(f"LOC-budget baseline refreshed at {baseline_path.name} ({len(current)} grandfathered file(s))")

    baseline: dict[str, int] = orjson.loads(baseline_path.read_bytes())

    problems: list[str] = []
    for rel, loc in sorted(current.items()):
        if rel not in baseline:
            problems.append(f"NEW oversized: {rel} ({loc} LOC > {limit})")
        elif loc > baseline[rel] + growth_slack:
            problems.append(f"GREW: {rel} ({baseline[rel]} -> {loc} LOC, slack {growth_slack})")

    if problems:
        pytest.fail(
            f"{len(problems)} module(s) violate the {limit}-LOC budget. Put new "
            f"functionality in a new focused module, or carve via sibling "
            f"re-export. Refresh the baseline only after a real split/shrink:\n  " + "\n  ".join(problems)
        )
