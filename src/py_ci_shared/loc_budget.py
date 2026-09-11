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


def oversized_files(files: Iterable[Path], root: Path, limit: int = DEFAULT_LOC_LIMIT) -> "dict[str, int]":
    """``{path relative to root: loc}`` for every file over *limit* -- the map a baseline records."""
    out: "dict[str, int]" = {}
    for p in files:
        n = _loc(p)
        if n > limit:
            out[p.relative_to(root).as_posix()] = n
    return out


def write_loc_baseline(path: Path, current: "dict[str, int]") -> None:
    """Write *current* as the baseline, keys sorted, for a repo's own ``regenerate_baseline`` or a refresh."""
    import orjson

    path.write_text(orjson.dumps(dict(sorted(current.items())), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode("utf-8"), encoding="utf-8")


def ratchet_problems(
    baseline: "dict[str, int]",
    current: "dict[str, int]",
    *,
    limit: int = DEFAULT_LOC_LIMIT,
    growth_slack: int = DEFAULT_GROWTH_SLACK,
    one_way: bool = True,
) -> "list[str]":
    """Every way the committed baseline and the tree disagree, as reviewer-readable lines.

    A pure function over two ``{relpath: loc}`` maps, so each rule can be exercised on inputs that do not
    exist in any tree. ``one_way`` (the default) makes the ratchet turn in one direction only:

    * a grandfathered file that SHRANK by more than ``growth_slack`` must have its ceiling lowered, or the
      headroom it left gets used -- production_scrapers watched a file carved from 1136 to 1021 lines grow
      back under a ceiling of 1136 + 50 without the gate objecting once (audit 2026-09-05 TEST-7);
    * a grandfathered file that dropped under ``limit`` entirely keeps its old, higher ceiling unless the
      entry FAILS -- it used to print to stderr in a green run, which nobody reads.

    ``one_way=False`` reproduces the original two rules (new file, growth past slack) for a repo that has
    not yet refreshed its baseline.
    """
    problems: "list[str]" = []
    for rel, loc in sorted(current.items()):
        if rel not in baseline:
            problems.append(f"NEW oversized: {rel} ({loc} LOC > {limit})")
        elif loc > baseline[rel] + growth_slack:
            problems.append(f"GREW: {rel} ({baseline[rel]} -> {loc} LOC, slack {growth_slack})")
        elif one_way and loc < baseline[rel] - growth_slack:
            problems.append(f"SHRANK: {rel} ({baseline[rel]} -> {loc} LOC) -- refresh the baseline to lock in the smaller ceiling")
    if one_way:
        drained = sorted(set(baseline) - set(current))
        if drained:
            problems.append(
                f"{len(drained)} grandfathered file(s) dropped under {limit} LOC and still hold their old ceiling -- "
                f"refresh the baseline ({REFRESH_FLAG}):\n    " + "\n    ".join(drained)
            )
    return problems


def assert_no_new_oversized_file(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    limit: int = DEFAULT_LOC_LIMIT,
    growth_slack: int = DEFAULT_GROWTH_SLACK,
    one_way: bool = True,
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
        one_way: also fail when a grandfathered file shrank by more than the slack
            or dropped under the limit, so its ceiling follows it down (see
            ``ratchet_problems``). On by default; False keeps the two original rules.
    """
    import orjson
    import pytest

    current = oversized_files(files, root, limit)

    if _refresh_requested() or not baseline_path.exists():
        write_loc_baseline(baseline_path, current)
        pytest.skip(f"LOC-budget baseline refreshed at {baseline_path.name} ({len(current)} grandfathered file(s))")

    baseline: dict[str, int] = orjson.loads(baseline_path.read_bytes())

    problems = ratchet_problems(baseline, current, limit=limit, growth_slack=growth_slack, one_way=one_way)

    if problems:
        pytest.fail(
            f"{len(problems)} module(s) violate the {limit}-LOC budget. Put new "
            f"functionality in a new focused module, or carve via sibling "
            f"re-export. Refresh the baseline only after a real split/shrink:\n  " + "\n  ".join(problems)
        )
