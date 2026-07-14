"""Shared harness for the "code-audit baseline" meta-test pattern.

Every consuming repo (glossum_backend_scripts, llm_bench,
realtime_applications, production_scrapers, pyutilz itself, mlframe,
algopacksimple) wired an identical ~90-line pytest test that runs
``pyutilz.dev.code_audit.run_all()`` against its own source and gates on a
committed baseline JSON so pre-existing debt doesn't block adoption --
only a NET-NEW finding fails the test. This module is that logic,
factored out to one place so a bug fix or format change lands everywhere
at once instead of needing seven synchronized edits.

Usage (in a consuming repo's ``tests/test_meta/test_code_audit_baseline.py``,
or an equivalent root-level file for flat-layout repos)::

    from pathlib import Path
    from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings

    import mypackage

    def test_no_new_code_audit_findings():
        assert_no_new_code_audit_findings(
            root=Path(mypackage.__file__).resolve().parent,
            baseline_path=Path(__file__).resolve().parent / "_code_audit_baseline.json",
            exclude_dirs=frozenset({"tests", "docs", "legacy"}),  # repo-specific, on top of the defaults
        )

And in the same directory's ``conftest.py`` (or the repo's root conftest.py
if there's only one), register the refresh flag so pytest accepts it::

    from py_ci_shared.code_audit_meta import register_refresh_option

    def pytest_addoption(parser):
        register_refresh_option(parser)

Deliberately dependency-light like the rest of this package: ``pyutilz``,
``pytest``, and ``orjson`` are imported LAZILY inside the functions below,
not at module level, so importing ``py_ci_shared`` itself never requires
them -- every actual caller already depends on ``pyutilz`` directly (it's
what's being scanned) and runs under ``pytest``, so this costs nothing in
practice while keeping this package's own install graph unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyutilz.dev.code_audit import Finding

REFRESH_FLAG = "--refresh-code-audit-baseline"

# The cache/vcs dirs every consumer needs regardless of its own layout.
# Repo-specific exclusions (tests/, legacy/, research/, ...) are passed in
# by the caller and merged with these, not a replacement for them.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules",
})


def register_refresh_option(parser) -> None:
    """Register ``--refresh-code-audit-baseline`` as a no-op boolean flag.

    pytest rejects unrecognized CLI options before test code runs
    (independent of ``--strict-config``, which only affects INI parsing),
    so every consuming repo's conftest.py must call this from its own
    ``pytest_addoption`` -- there is no way to register an option from
    inside a plugin/module that isn't itself a conftest.py or a real
    pytest plugin entry point.
    """
    try:
        parser.addoption(
            REFRESH_FLAG,
            action="store_true",
            default=False,
            help="rewrite the code-audit baseline JSON instead of comparing (intentional change)",
        )
    except ValueError:
        pass  # already registered (e.g. a repo with more than one conftest.py in the chain)


def _refresh_requested() -> bool:
    import sys

    return REFRESH_FLAG in sys.argv


def _key(f: "Finding") -> str:
    return f"{f.check}::{f.file}:{f.line}"


def assert_no_new_code_audit_findings(
    root: Path,
    baseline_path: Path,
    exclude_dirs: frozenset[str] = frozenset(),
    checks: list[str] | None = None,
) -> None:
    """Run ``pyutilz.dev.code_audit.run_all()`` against ``root`` and either
    seed/refresh ``baseline_path`` (first run, or ``--refresh-code-audit-baseline``
    passed), or ``pytest.fail()`` if any finding is NOT already in the
    baseline. Call this directly as the body of a ``test_*`` function.

    Args:
        root: directory to scan (a package dir, or a repo root for
            flat-layout projects).
        baseline_path: where the baseline JSON lives (and gets written on
            refresh) -- conventionally a sibling ``_code_audit_baseline.json``
            next to the test file.
        exclude_dirs: repo-specific directories to skip, merged with
            ``DEFAULT_EXCLUDE_DIRS`` (cache/vcs dirs every repo needs).
        checks: optional subset of check names to run (``None`` runs every
            registered scanner, the normal case).
    """
    import orjson
    import pytest
    from pyutilz.dev.code_audit import run_all

    merged_exclude = DEFAULT_EXCLUDE_DIRS | exclude_dirs
    current = run_all(root, checks=checks, exclude_dirs=merged_exclude)
    current_by_key = {_key(f): f for f in current}
    current_keys = set(current_by_key)

    if _refresh_requested() or not baseline_path.exists():
        baseline_path.write_text(
            orjson.dumps(sorted(current_keys), option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )
        pytest.skip(f"code-audit baseline refreshed at {baseline_path.name} " f"({len(current_keys)} existing finding(s))")

    baseline = set(orjson.loads(baseline_path.read_bytes()))
    new = sorted(current_keys - baseline)
    fixed = sorted(baseline - current_keys)

    if fixed:
        import sys

        sys.stderr.write(
            f"\n[assert_no_new_code_audit_findings] {len(fixed)} finding(s) DRAINED:\n  "
            + "\n  ".join(fixed[:15])
            + (f"\n  ... and {len(fixed) - 15} more" if len(fixed) > 15 else "")
            + f"\n  Refresh: pytest ... {REFRESH_FLAG}\n"
        )

    if new:
        detail_lines = []
        for k in new[:20]:
            f = current_by_key.get(k)
            detail_lines.append(f"{f.check} [{f.severity}] {f.file}:{f.line} -- {f.detail}" if f is not None else k)
        pytest.fail(
            f"{len(new)} new static-analysis finding(s) from pyutilz.dev.code_audit "
            f"(see pyutilz/src/pyutilz/dev/code_audit/__init__.py for check descriptions). "
            f"Fix the code, OR if this is a confirmed false positive, refresh the baseline "
            f"after review:\n  " + "\n  ".join(detail_lines) + (f"\n  ... and {len(new) - 20} more" if len(new) > 20 else "")
        )
