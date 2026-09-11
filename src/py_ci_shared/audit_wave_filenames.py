"""Shared check: a test file is named after what it covers, not after the audit wave that produced it.

``test_wave97_reporting_probabilistic_split.py`` could just as well be ``test_reporting_module_split.py``;
the ``wave97`` says nothing about the test and everything about the process, which git history already
records. mlframe and glossum each carried a copy of this check with a different pattern list; this is the
union, plus a baseline for repos that already hold such names (the social projects hold dozens of
``test_audit_2026_*`` files), so adopting it blocks NEW offenders without failing on the old ones.

A baseline that is never drained is a list nobody reads, so an entry that no longer names an offender
(the file was renamed or deleted) fails too -- ``fail_on_stale=False`` only while migrating.

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

#: Stems that carry process metadata instead of a topic. Matched against the file stem.
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


def find_audit_wave_test_files(tests_dir: Path, *, extra_patterns: Iterable[str] = (), exclude_names: Iterable[str] = ()) -> list[str]:
    """Every ``test_*.py`` under *tests_dir* whose stem matches a pattern, as sorted posix paths relative to it."""
    compiled = [re.compile(p) for p in (*DEFAULT_PATTERNS, *extra_patterns)]
    skip = set(exclude_names)
    out = []
    for path in tests_dir.rglob("test_*.py"):
        if "__pycache__" in path.parts or path.name in skip:
            continue
        if any(p.match(path.stem) for p in compiled):
            out.append(path.relative_to(tests_dir).as_posix())
    return sorted(out)


def _read_baseline(baseline: "Path | None") -> set[str]:
    if baseline is None or not baseline.is_file():
        return set()
    data = json.loads(baseline.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"{baseline} must hold a JSON list of test-file paths relative to the tests directory")
    return set(data)


def write_baseline(baseline: Path, offenders: Iterable[str]) -> None:
    """Record the current offenders as grandfathered. For adoption only; not called by the assertion."""
    baseline.write_text(json.dumps(sorted(offenders), indent=2) + "\n", encoding="utf-8")


def assert_no_new_audit_wave_filenames(
    tests_dir: Path,
    *,
    baseline: "Path | None" = None,
    extra_patterns: Iterable[str] = (),
    exclude_names: Iterable[str] = (),
    fail_on_stale: bool = True,
) -> None:
    """Fail on an offender not in *baseline*, and (by default) on a baseline entry that is no longer one."""
    import pytest

    current = set(find_audit_wave_test_files(tests_dir, extra_patterns=extra_patterns, exclude_names=exclude_names))
    grandfathered = _read_baseline(baseline)
    problems = []
    new = sorted(current - grandfathered)
    if new:
        problems.append(
            f"{len(new)} test file(s) named after an audit wave rather than what they cover; rename to the topic "
            "(git history keeps the process metadata):\n    " + "\n    ".join(new[:30])
        )
    stale = sorted(grandfathered - current)
    if stale and fail_on_stale:
        problems.append(f"{len(stale)} baseline entr(y/ies) no longer name an offender; delete them from {baseline}:\n    " + "\n    ".join(stale[:30]))
    if problems:
        pytest.fail("\n  ".join(problems))
