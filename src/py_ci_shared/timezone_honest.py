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

import posixpath
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
#: Any located diagnostic ruff prints: `bad.py:2:7: invalid-syntax: Expected ...`. One that is not a DTZ finding
#: means ruff could not check that file (a syntax error it cannot parse past), so it must not read as clean.
_DIAGNOSTIC = re.compile(r"^(?P<path>.+?):\d+:\d+: (?P<code>[\w-]+)\b")


def _normalise(name: str) -> str:
    """``./scripts/`` and ``scripts`` are the same directory; so are ``scripts\\x`` and ``scripts/x``."""
    cleaned = posixpath.normpath(name.replace("\\", "/").strip())
    return "." if cleaned in ("", ".") else cleaned


def _ruff_excludes(root: Path) -> list[str]:
    """``exclude`` + ``extend-exclude`` from every ruff config at *root*: ``[tool.ruff]`` in ``pyproject.toml`` and the
    top level of ``ruff.toml`` / ``.ruff.toml`` (ruff reads the dot-file first; any of them can hide a directory)."""
    names: list[str] = []
    sources = [(root / "pyproject.toml", True), (root / "ruff.toml", False), (root / ".ruff.toml", False)]
    for path, nested in sources:
        if not path.is_file():
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        table = data.get("tool", {}).get("ruff", {}) if nested else data
        names += [*table.get("exclude", []), *table.get("extend-exclude", [])]
    return names


def excluded_code_dirs(root: Path) -> list[str]:
    """Directories named in ruff's `exclude` / `extend-exclude` (pyproject, `ruff.toml`, `.ruff.toml`) that contain
    Python files, normalised (`./scripts/` is `scripts`).

    Glob patterns (`*.md`) and dot-directories are skipped: neither is a source directory someone
    forgot to scan.
    """
    out = []
    for raw in _ruff_excludes(root):
        name = _normalise(raw)
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
    scan = tuple(_normalise(p) for p in scan_paths)
    missing = [p for p in scan if not (root / p).exists()]
    if missing:
        raise RuntimeError(f"scan path(s) {missing} do not exist under {root}. ruff would report them as clean.")

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "DTZ", "--no-force-exclude", "--output-format", "concise", *scan],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"ruff exited {result.returncode}: {result.stderr.strip()[:500]}")
    # Exit 1 means "findings", so it must come with at least one. Without this, a ruff that is not
    # installed (`No module named ruff`, also exit 1, nothing on stdout) read as a clean result: on
    # py-ci-shared's own CI, whose test job had no ruff, every test here saw an empty set.
    if result.returncode == 1 and not any(_FINDING.match(line.strip()) for line in result.stdout.splitlines()):
        raise RuntimeError(f"ruff exited 1 without reporting a single finding, so it checked nothing: {(result.stderr or result.stdout).strip()[:500]}")
    if "Failed to lint" in result.stderr:
        raise RuntimeError(f"ruff skipped input it was given: {result.stderr.strip()[:500]}")

    unchecked = sorted({line.strip() for line in result.stdout.splitlines() if _DIAGNOSTIC.match(line.strip()) and not _FINDING.match(line.strip())})
    if unchecked:
        raise RuntimeError("ruff could not check file(s) it was given, so their DTZ result is unknown:\n  " + "\n  ".join(unchecked[:50]))

    tests = set(test_dir_names)
    found = set()
    for line in result.stdout.splitlines():
        match = _FINDING.match(line.strip())
        if not match:
            continue
        path = match["path"].replace("\\", "/").removeprefix("./")
        if tests.intersection(path.split("/")[:-1]):
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
    scan = tuple(_normalise(p) for p in scan_paths)
    allowed = dict(allowed or {})
    tests = tuple(test_dir_names)
    problems = []
    declared_not_code = {_normalise(n) for n in not_code}

    unscanned = [
        d
        for d in excluded_code_dirs(root)
        if not any(d == s or d.startswith(s + "/") for s in scan if s != ".") and d not in declared_not_code and not set(d.split("/")) & set(tests)
    ]
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
