"""Pins format_warn.py's hardcoded UP-ignore list against configs/ruff-base.toml's own `ignore`
list, so the two can never silently drift apart (flagged in a 2026-07-09 CI/CD architecture
review: the duplication was previously kept in sync by a comment only).
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]


def _format_warn_up_ignore() -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from py_ci_shared.format_warn import _UP_IGNORE

    return _UP_IGNORE


def _ruff_base_ignore() -> list[str]:
    with open(REPO_ROOT / "configs" / "ruff-base.toml", "rb") as f:
        data = tomllib.load(f)
    return data["lint"]["ignore"]


def test_format_warn_up_ignore_is_a_subset_of_ruff_base_ignore():
    up_ignore = set(_format_warn_up_ignore())
    base_ignore = set(_ruff_base_ignore())
    missing = up_ignore - base_ignore
    assert not missing, (
        f"format_warn.py's _UP_IGNORE contains codes not in configs/ruff-base.toml's own "
        f"[lint] ignore list: {missing}. The whole point of format_warn.py re-ignoring these "
        f"on the CLI is to mirror what ruff-base.toml already ignores project-wide (an explicit "
        f"--select UP would otherwise re-enable them); if ruff-base.toml's ignore list changes, "
        f"this hardcoded list must be updated to match."
    )
