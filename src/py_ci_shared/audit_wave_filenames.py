"""Shared check: a test file is named after what it covers, not after the audit wave that produced it.

``test_wave97_reporting_probabilistic_split.py`` could just as well be ``test_reporting_module_split.py``;
the ``wave97`` says nothing about the test and everything about the process, which git history already
records. mlframe and glossum each carried a copy of this check with a different pattern list; this is the
union, plus a baseline for repos that already hold such names (the social projects hold dozens of
``test_audit_2026_*`` files), so adopting it blocks NEW offenders without failing on the old ones.

A baseline that is never drained is a list nobody reads, so an entry that no longer names an offender
(the file was renamed or deleted) fails too -- ``fail_on_stale=False`` only while migrating. A baseline path
that does not exist fails (rewrite it with ``--refresh-audit-wave-filenames-baseline`` or
``PY_CI_SHARED_REFRESH=audit-wave-filenames``), and so does a tests directory that is missing or holds no
test file: a check that scanned nothing is not a pass. Patterns match the stem case-insensitively.

Usage::

    from py_ci_shared.audit_wave_filenames import assert_no_new_audit_wave_filenames

    def test_no_new_audit_wave_filenames():
        assert_no_new_audit_wave_filenames(TESTS_DIR, baseline=META_DIR / "_audit_wave_filenames_baseline.json")
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import Baseline, iter_files, refresh_requested, write_ratchet

REFRESH_FLAG = "--refresh-audit-wave-filenames-baseline"

#: Stems that carry process metadata instead of a topic. Matched against the file stem, ignoring case.
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"^test_audit_\d{4}_",
    r"^test_audit_round\d+",
    r"^test_round\d+_",
    r"^test_wave\d+_",
    r"^test_waves\d+_",
    r"^test_w\d+_",
    r"^test_p\d+_coverage_batch",
    r"^test_prod_log_fixes_\d{4}_",
    r"^test_misc_fixes_",
    r"^test_untested_",
)


def scan_audit_wave_test_files(tests_dir: Path, *, extra_patterns: Iterable[str] = (), exclude_names: Iterable[str] = ()) -> "tuple[list[str], int]":
    """``(offenders, number of test files scanned)``. A missing *tests_dir* raises ``_core.CorpusError``."""
    compiled = [re.compile(p, re.IGNORECASE) for p in (*DEFAULT_PATTERNS, *extra_patterns)]
    skip = set(exclude_names)
    tests_dir = Path(tests_dir)
    files = [p for p in iter_files(tests_dir, ("test_*.py",)) if p.name not in skip]
    out = [p.relative_to(tests_dir).as_posix() for p in files if any(c.match(p.stem) for c in compiled)]
    return sorted(out), len(files)


def find_audit_wave_test_files(tests_dir: Path, *, extra_patterns: Iterable[str] = (), exclude_names: Iterable[str] = ()) -> list[str]:
    """Every ``test_*.py`` under *tests_dir* whose stem matches a pattern, as sorted posix paths relative to it."""
    return scan_audit_wave_test_files(tests_dir, extra_patterns=extra_patterns, exclude_names=exclude_names)[0]


def _read_baseline(baseline: "Path | None") -> set[str]:
    if baseline is None or not baseline.is_file():
        return set()
    data = json.loads(baseline.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"{baseline} must hold a JSON list of test-file paths relative to the tests directory")
    return set(data)


def write_baseline(baseline: Path, offenders: Iterable[str], *, grow: Optional[bool] = None) -> None:
    """Record the current offenders as grandfathered. For adoption only; not called by the assertion. Shrink-only unless
    growth is allowed, so adopting (seeding) needs ``grow=True`` or ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``."""
    previous = dict.fromkeys(_read_baseline(baseline), 1) if baseline.is_file() else None
    write_ratchet(
        baseline,
        dict.fromkeys(offenders, 1),
        gate="audit-wave-filenames",
        previous=previous,
        render=lambda kept: json.dumps(sorted(kept), indent=2) + "\n",
        grow=grow,
    )


def assert_no_new_audit_wave_filenames(
    tests_dir: Path,
    *,
    baseline: "Path | None" = None,
    extra_patterns: Iterable[str] = (),
    exclude_names: Iterable[str] = (),
    fail_on_stale: bool = True,
    min_files: int = 1,
    request: Any = None,
) -> None:
    """Fail on an offender not in *baseline*, (by default) on a baseline entry that is no longer one, on a missing
    baseline file, and when fewer than *min_files* test files were scanned."""
    import pytest

    current, scanned = scan_audit_wave_test_files(tests_dir, extra_patterns=extra_patterns, exclude_names=exclude_names)
    if scanned < min_files:
        pytest.fail(f"only {scanned} test_*.py file(s) under {tests_dir}; expected at least {min_files} -- the check is reading nothing")
    guidance = "test file(s) named after an audit wave rather than what they cover; rename to the topic (git history keeps the process metadata)"
    if baseline is None:
        if current:
            pytest.fail(f"{len(current)} {guidance}:\n    " + "\n    ".join(current[:30]))
        return
    Baseline(baseline, gate="audit-wave-filenames", refresh_command=f"pytest {REFRESH_FLAG}").enforce(
        current, refresh=refresh_requested(REFRESH_FLAG, request), guidance=guidance, request=request
    ).raise_for_pytest(fail_on_stale=fail_on_stale)
