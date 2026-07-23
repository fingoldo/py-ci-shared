"""Shared check: every subdirectory under a consumer repo's ``tests/`` must be
reachable by at least one CI job -- or be explicitly whitelisted as
intentionally excluded (e.g. a paid-API "live" tier).

Generalizes a pattern first written directly in glossum_backend_scripts
(``tests/test_meta/test_all_test_dirs_reachable_in_ci.py``, 2026-07-23
audit-verification meta-test round): a new test subdirectory can be added
and never wired into any CI job's ``pytest`` invocation, so its tests only
ever run locally -- CI stays green while a whole test category silently
stops gating merges. Text-based (not a YAML parser), matching this
package's other ``ci_*``/``code_audit`` scanners' established convention.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.ci_test_dir_reachability import assert_every_test_subdir_reachable

    def test_every_test_subdir_reachable_by_some_ci_job():
        assert_every_test_subdir_reachable(
            repo_root=Path(__file__).resolve().parents[2],
            workflows_dir=Path(__file__).resolve().parents[2] / ".github" / "workflows",
            intentionally_unreached={"live"},
        )
"""

from __future__ import annotations

import re
from pathlib import Path

_PYTEST_INVOKE_RE = re.compile(r"pytest\s+([^\s|&><;]+)")
_PYTEST_IGNORE_RE = re.compile(r"--ignore(?:-glob)?=(\S+)")


def _all_ci_run_lines(workflows_dir: Path) -> str:
    if not workflows_dir.is_dir():
        return ""
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    return "\n".join(wf.read_text(encoding="utf-8") for wf in workflow_files)


def _test_subdirs(repo_root: Path) -> set[str]:
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return set()
    return {p.name for p in tests_dir.iterdir() if p.is_dir() and p.name != "__pycache__" and not p.name.startswith(".")}


def find_unreachable_test_subdirs(
    repo_root: Path,
    workflows_dir: Path,
    intentionally_unreached: set[str] | None = None,
) -> list[str]:
    """Return every ``tests/<subdir>`` name not invoked (directly or by not
    being universally ``--ignore``'d) by any workflow file, minus the
    caller's explicit whitelist."""
    intentionally_unreached = intentionally_unreached or set()
    ci_text = _all_ci_run_lines(workflows_dir)
    # Strip --ignore(-glob)=<path> flags before substring-matching for direct
    # invocation, else a subdir excluded via --ignore is wrongly counted as
    # "directly invoked" just because its name appears inside the flag value.
    ci_text_sans_ignores = _PYTEST_IGNORE_RE.sub("", ci_text)
    unreachable = []
    for d in sorted(_test_subdirs(repo_root) - intentionally_unreached):
        directly_invoked = f"tests/{d}" in ci_text_sans_ignores or f"tests\\{d}" in ci_text_sans_ignores
        if directly_invoked:
            continue
        # Not directly named -- reachable only if some job runs the whole
        # `tests/` (or `tests` with no path) without an --ignore covering it.
        ignored_paths = set(_PYTEST_IGNORE_RE.findall(ci_text))
        bare_targets = {t.rstrip("/").rstrip("\\") for t in _PYTEST_INVOKE_RE.findall(ci_text)}
        covered_by_bare_tests_invoke = "tests" in bare_targets
        ignored_everywhere = any(f"tests/{d}" in ip or f"tests\\{d}" in ip for ip in ignored_paths)
        if covered_by_bare_tests_invoke and not ignored_everywhere:
            continue
        unreachable.append(d)
    return unreachable


def assert_every_test_subdir_reachable(
    repo_root: Path,
    workflows_dir: Path,
    intentionally_unreached: set[str] | None = None,
) -> None:
    """Pytest-friendly assertion wrapper. Raises ``AssertionError`` (via
    ``pytest.fail``) listing every unreachable subdir, or does nothing if
    all are covered.
    """
    import pytest

    unreachable = find_unreachable_test_subdirs(repo_root, workflows_dir, intentionally_unreached)
    if unreachable:
        pytest.fail(
            f"{len(unreachable)} tests/<subdir> not invoked by any CI workflow and not in "
            f"intentionally_unreached: {unreachable} -- either add it to a workflow's pytest "
            f"invocation, or whitelist it explicitly with a reason if it's deliberately "
            f"excluded (e.g. a paid-API live tier)."
        )
