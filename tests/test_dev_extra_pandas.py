"""The ``dev`` extra lists pandas, because tests/test_estimator_invariants.py importorskips it at module level (audit 44-T6)."""

from __future__ import annotations

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _dev_extra() -> str:
    match = re.search(r"^dev = \[(.*?)^\]", PYPROJECT.read_text(encoding="utf-8"), re.S | re.M)
    assert match, "no dev extra in pyproject.toml"
    return match.group(1)


def test_dev_extra_lists_pandas():
    assert re.search(r'"pandas[<>=~!\]"]', _dev_extra()), "pandas is missing from the dev extra"
