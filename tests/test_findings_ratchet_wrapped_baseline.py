"""findings_ratchet reads the entries of a wrapped baseline, not its header keys (audit 42-F15)."""

from __future__ import annotations

import json
from pathlib import Path

from py_ci_shared import meta_test_utils as mtu


def test_a_core_shaped_baseline_gives_its_entries_not_its_header(tmp_path: Path) -> None:
    """Negative control (42-F15): the old reader took the header keys, so a matching entry was reported as new and the header as drained."""
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"schema": 1, "gate": "x", "entries": {"a": "reason"}}), encoding="utf-8")
    assert mtu.findings_ratchet(["a"], baseline) == ([], [])


def test_a_comment_key_is_not_an_entry(tmp_path: Path) -> None:
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"_comment": "why", "accepted": {"a": "reason"}}), encoding="utf-8")
    assert mtu.findings_ratchet(["a"], baseline) == ([], [])


def test_a_bare_list_of_keys_still_reads_as_before(tmp_path: Path) -> None:
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps(["keep", "gone"]), encoding="utf-8")
    assert mtu.findings_ratchet(["keep", "added"], baseline) == (["added"], ["gone"])
