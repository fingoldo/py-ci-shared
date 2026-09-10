"""Shared check: non-test code states its time frame in the expression, and the scan cannot miss a directory.

The rules are ruff's own `DTZ` family (naive `datetime.now()`, `date.today()`, `datetime(...)` without a
`tzinfo`, ...). This module does not restate them. What it adds is the two things a bare
`ruff check --select DTZ .` got wrong three times in one audit round, each time reporting a clean number:

1. **An excluded directory is silently skipped.** `[tool.ruff] exclude` drops a directory from every scan
   that walks the tree, so `ruff check .` over a package that excludes `scripts` says nothing about
   `scripts`. production_scrapers measured "production DTZ is zero" that way over a tree omitting the
   directory the defect had been found in the day before, and then realtime_applications was recorded
   as "0" the same way while `scripts/` held a local date written into a stored column. Silence from an
   excluded scope reads as absence. So this check READS the exclude list, and fails when a listed
   directory contains Python and is neither scanned nor declared not-code. Explicit paths override the
   exclusion, and `--no-force-exclude` keeps it that way even in a repo that sets `force-exclude`.

2. **A counted allowance hides the next finding.** An allowance here is a `(path, rule)` pair mapped to
   the reason it is correct, never a number, so a new finding cannot sit behind an old one's count. And
   an allowance that no longer matches anything fails too: headroom nobody is using gets used.

Tests are out of scope by default. A fixture's naive `datetime(2026, 1, 1)` is correct, and including
tests means dozens of allowances, which is the shape that gets a gate suppressed.

Usage::

    from py_ci_shared.timezone_honest import assert_timezone_honest

    def test_production_code_is_timezone_honest():
        assert_timezone_honest(
            PACKAGE_DIR,
            scan_paths=(".", "scripts"),
            allowed={("scripts/rollup.py", "DTZ007"): "strptime of a %Y-%m CLI argument, which has no zone"},
        )
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from ._toml_compat import tomllib

#: Excluded names that are never source even when they contain `.py` files: build artefacts and
#: environments. Anything starting with a dot is treated the same way (`.venv`, `.tox`, `.git`).
_NEVER_CODE = frozenset({"build", "dist", "__pycache__", "node_modules", "site-packages"})

#: `scripts\rollup.py:698:19: DTZ007 Naive datetime constructed ...` (ruff's `concise` format).
_FINDING = re.compile(r"^(?P<path>.+?):\d+:\d+: (?P<rule>DTZ\d{3})\b")


def excluded_code_dirs(root: Path) -> list[str]:
    """Directories named in `[tool.ruff] exclude` / `extend-exclude` that contain Python files.

    Glob patterns (`*.md`) and dot-directories are skipped: neither is a source directory someone
    forgot to scan.
    """
    config_path = root / "pyproject.toml"
    if not config_path.is_file():
        return []
    ruff = tomllib.loads(config_path.read_text(encoding="utf-8")).get("tool", {}).get("ruff", {})
    names = [*ruff.get("exclude", []), *ruff.get("extend-exclude", [])]

    out = []
    for name in names:
        if any(ch in name for ch in "*?[") or name.startswith(".") or name in _NEVER_CODE:
            continue
        directory = root / name
        if directory.is_dir() and any("__pycache__" not in p.parts for p in directory.rglob("*.py")):
            out.append(name)
    return sorted(set(out))


def dtz_findings(root: Path, scan_paths: Iterable[str] = (".",), *, test_dir_names: Iterable[str] = ("tests",)) -> set[tuple[str, str]]:
    """`(path, rule)` for every DTZ finding under *scan_paths*, excluding test directories.

    Raises `RuntimeError` when ruff did not look at everything it was given: an empty result from a
    ruff that failed would otherwise pass every check built on this for free.

    A MISSING PATH IS THE CASE THAT NEEDS THIS. Measured: `ruff check does_not_exist_dir` prints
    `warning: Failed to lint does_not_exist_dir`, then `All checks passed!`, and exits 0. So a typo in
    a scan path (`script` for `scripts`) is a clean result over a directory nobody opened, which is the
    same silence this module exists to refuse. An invalid ruff config does exit 2 and is caught by the
    return code; the missing path is caught before ruff runs, and ruff's own warning is treated as an
    error in case some other path fails to lint for a different reason.
    """
    scan = tuple(scan_paths)
    missing = [p for p in scan if not (root / p).exists()]
    if missing:
        raise RuntimeError(f"scan path(s) {missing} do not exist under {root}. ruff would report them as clean.")

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "DTZ", "--no-force-exclude", "--output-format", "concise", *scan],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"ruff exited {result.returncode}: {result.stderr.strip()[:500]}")
    if "Failed to lint" in result.stderr:
        raise RuntimeError(f"ruff skipped input it was given: {result.stderr.strip()[:500]}")

    tests = set(test_dir_names)
    found = set()
    for line in result.stdout.splitlines():
        match = _FINDING.match(line.strip())
        if not match:
            continue
        path = match["path"].replace("\\", "/").removeprefix("./")
        if path.split("/", 1)[0] in tests:
            continue
        found.add((path, match["rule"]))
    return found


def timezone_problems(
    root: Path,
    *,
    scan_paths: Iterable[str] = (".",),
    allowed: Mapping[tuple[str, str], str] | None = None,
    not_code: Iterable[str] = (),
    test_dir_names: Iterable[str] = ("tests",),
) -> list[str]:
    """Every reason the check fails, as sentences. Empty means it passes."""
    scan = tuple(scan_paths)
    allowed = dict(allowed or {})
    tests = tuple(test_dir_names)
    problems = []

    unscanned = [d for d in excluded_code_dirs(root) if d not in scan and d not in set(not_code) and d not in tests]
    if unscanned:
        problems.append(
            f"{unscanned} {'is' if len(unscanned) == 1 else 'are'} excluded from ruff and contain Python, but "
            "are not in `scan_paths`. A scan of `.` skips an excluded directory silently, so the result would "
            "read as clean over code nobody looked at. Add them to `scan_paths`, or to `not_code` if they "
            "genuinely hold no source."
        )

    unreasoned = sorted(key for key, reason in allowed.items() if not str(reason).strip())
    if unreasoned:
        problems.append(f"allowance(s) with no reason: {unreasoned}. An allowance is a claim that the finding is correct; say why.")

    found = dtz_findings(root, scan, test_dir_names=tests)

    unexpected = sorted(found - set(allowed))
    if unexpected:
        problems.append(
            "naive-datetime finding(s) in non-test code. Elapsed time is `time.monotonic()`; a stored date is "
            "`datetime.now(datetime.UTC).date()`; a naive-UTC column wants "
            f"`datetime.now(datetime.UTC).replace(tzinfo=None)`: {unexpected}"
        )

    stale = sorted(set(allowed) - found)
    if stale:
        problems.append(
            f"allowance(s) that no longer match any finding: {stale}. Remove them, so a different finding "
            "cannot later arrive under an allowance written for something else."
        )
    return problems


def assert_timezone_honest(
    root: Path,
    *,
    scan_paths: Iterable[str] = (".",),
    allowed: Mapping[tuple[str, str], str] | None = None,
    not_code: Iterable[str] = (),
    test_dir_names: Iterable[str] = ("tests",),
) -> None:
    """Fail with every problem at once, so one run shows the whole picture."""
    import pytest

    problems = timezone_problems(root, scan_paths=scan_paths, allowed=allowed, not_code=not_code, test_dir_names=test_dir_names)
    if problems:
        pytest.fail(f"{len(problems)} timezone problem(s):\n  - " + "\n  - ".join(problems))
