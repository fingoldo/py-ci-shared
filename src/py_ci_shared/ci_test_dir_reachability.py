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
# A pytest invocation with NO positional path argument runs whatever `testpaths`/rootdir
# resolves to -- i.e. the whole `tests/` tree -- so it covers every subdir just as a literal
# `pytest tests/` does. Without this, a repo whose CI runs `pytest -m "not gpu" --cov=...`
# (no path at all, the most common shape) was reported as reaching NO test subdir whatsoever,
# turning the check into a guaranteed false positive on exactly the repos it should pass.
_TOKEN_RE = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s|&;><]+")
# Short flags that consume the following token as their VALUE (`-m "not gpu"`), which must not
# be mistaken for a positional path. Long flags carry their value as `--flag=value` in every
# CI invocation shape seen here, so they need no such table.
_VALUE_TAKING_SHORT_FLAGS = frozenset({"-m", "-k", "-p", "-n", "-o", "-c", "-W", "-r", "--deselect", "--ignore", "--ignore-glob"})


def _all_ci_run_lines(workflows_dir: Path) -> str:
    if not workflows_dir.is_dir():
        return ""
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    text = "\n".join(wf.read_text(encoding="utf-8") for wf in workflow_files)
    # Fold shell line-continuations so a multi-line `pytest ... \` + newline + `tests/foo` invocation is
    # analysed as the single command it is -- otherwise its first line looks pathless and its
    # continuation lines look like commands of their own.
    return re.sub(r"\\\s*\n\s*", " ", text)


def _has_pathless_pytest_invocation(ci_text: str) -> bool:
    """True if any ``pytest`` command in ``ci_text`` passes no positional path.

    Such a run collects from ``testpaths``/rootdir, so it reaches every ``tests/`` subdir.
    """
    for m in re.finditer(r"(?:^|[\s;&|])pytest\s+(.*)$", ci_text, re.MULTILINE):
        tokens = _TOKEN_RE.findall(m.group(1))
        skip_next = False
        has_positional = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in _VALUE_TAKING_SHORT_FLAGS:
                skip_next = True
                continue
            if tok.startswith("-") or tok == "\\":
                continue
            has_positional = True
            break
        if not has_positional:
            return True
    return False


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
        ignored_paths = {ip.rstrip("/\\").replace("\\", "/") for ip in _PYTEST_IGNORE_RE.findall(ci_text)}
        bare_targets = {t.rstrip("/").rstrip("\\") for t in _PYTEST_INVOKE_RE.findall(ci_text)}
        covered_by_bare_tests_invoke = "tests" in bare_targets or _has_pathless_pytest_invocation(ci_text)
        # An --ignore must name the directory ITSELF, or a --ignore-glob
        # covering everything under it (`tests/<d>/*` or `/**`), to count as
        # excluding the whole subdir -- an --ignore of one specific FILE
        # inside the directory (e.g. tests/test_smoke/test_llm_providers_live.py)
        # only excludes that file, leaving the rest of the directory's tests
        # reachable via the bare `tests/` sweep.
        whole_dir_ignore_targets = {f"tests/{d}", f"tests/{d}/*", f"tests/{d}/**"}
        ignored_everywhere = bool(ignored_paths & whole_dir_ignore_targets)
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
