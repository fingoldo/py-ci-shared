"""Pins format_warn.py's hardcoded re-ignore list against configs/ruff-base.toml's own `ignore`
list, so the two can never silently drift apart (flagged in a 2026-07-09 CI/CD architecture
review: the duplication was previously kept in sync by a comment only; a 2026-07-16 incident
then showed a plain subset check isn't enough -- it let E501/E401/E402/E701/E702/I001/N802/N803/
N806 go missing from the re-ignore list for months, silently resurrecting rules the base config
had deliberately disabled, e.g. 3618 N806 + 2762 E501 "warnings" on mlframe alone that were never
real findings).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]

# format_warn.py's ``--select`` argument -- the reselected-ignore list must match the base
# ignore list restricted to exactly these code prefixes (an equality check, not a mere subset
# check: the 2026-07-16 incident was a MISSING-item drift, which a subset check cannot catch).
# Matched against each code's leading ALPHA RUN (not a raw substring startswith): ruff's "I"
# (isort) and "ISC" (implicit-string-concat) are distinct rule categories despite one being a
# string-prefix of the other, so "ISC001" must NOT be treated as selected by "--select ...,I,...".
_RESELECTED_PREFIXES = {"E", "W", "N", "UP", "I"}


def _code_prefix(code: str) -> str:
    return re.match(r"^[A-Z]+", code).group()  # type: ignore[union-attr]


def _format_warn_reselected_ignore() -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from py_ci_shared.format_warn import _RESELECTED_IGNORE

    return _RESELECTED_IGNORE


def _ruff_base_ignore() -> list[str]:
    with open(REPO_ROOT / "configs" / "ruff-base.toml", "rb") as f:
        data = tomllib.load(f)
    return data["lint"]["ignore"]


def test_format_warn_reselected_ignore_matches_ruff_base_ignore_subset():
    reselected_ignore = set(_format_warn_reselected_ignore())
    base_ignore = set(_ruff_base_ignore())
    base_reselected_subset = {c for c in base_ignore if _code_prefix(c) in _RESELECTED_PREFIXES}

    missing = base_reselected_subset - reselected_ignore
    extra = reselected_ignore - base_reselected_subset
    assert not missing and not extra, (
        f"format_warn.py's _RESELECTED_IGNORE has drifted from configs/ruff-base.toml's own "
        f"[lint] ignore list restricted to the {_RESELECTED_PREFIXES} prefixes it re-selects on "
        f"the CLI. missing (base ignores these but format_warn.py doesn't re-ignore them, so its "
        f"--select resurrects them as false 'warnings'): {missing or None}. extra (format_warn.py "
        f"re-ignores these but the base config no longer does): {extra or None}. Update "
        f"_RESELECTED_IGNORE to match."
    )
