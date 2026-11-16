"""Audit 2026-11-16 round 42 (core baseline semantics): one test per finding 42-T1..T5."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.baseline_ratchet import Baseline as RatchetBaseline
from py_ci_shared.meta_test_utils import findings_ratchet
from py_ci_shared._core.baseline import UNJUSTIFIED_MARKER, Baseline, is_unjustified
from py_ci_shared._core.errors import BaselineError


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# 42-T1: a growing refresh with no explicit new_note must not write a blank note that enforce accepts.
def test_grow_refresh_default_note_is_unjustified(tmp_path: Path) -> None:
    path = tmp_path / "c2.json"
    Baseline(path, gate="c2").regenerate(["k", "k"], grow=True)
    note = json.loads(path.read_text(encoding="utf-8"))["entries"]["k"]["note"]
    assert is_unjustified(note), note
    outcome = Baseline(path, gate="c2").enforce(["k", "k"])
    assert outcome.unjustified == ["k"]
    assert not outcome.ok


# 42-T2: a real key starting with "_" is kept by every reader, as _core does.
def test_underscore_key_kept_by_baseline_ratchet_bare_form(tmp_path: Path) -> None:
    _write(tmp_path / "x.json", {"_core/x.py: bad": "reason", "_comment": "meta"})
    assert RatchetBaseline("x", directory=str(tmp_path)).load() == {"_core/x.py: bad": "reason"}


@pytest.mark.parametrize("shape", ["bare", "accepted"])
def test_underscore_key_kept_by_findings_ratchet(tmp_path: Path, shape: str) -> None:
    entries = {"_core/x.py: bad": "reason", "_comment": "meta"}
    path = _write(tmp_path / "b.json", entries if shape == "bare" else {"accepted": entries})
    assert findings_ratchet(["_core/x.py: bad"], path) == ([], [])


# 42-T3: findings_ratchet reads a baseline the way the other readers do.
def test_findings_ratchet_accepts_bom(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": "reason"}).encode("utf-8"))
    assert findings_ratchet(["a"], path) == ([], [])


def test_findings_ratchet_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text('{"a": "x", "a": "y"}', encoding="utf-8")
    with pytest.raises(BaselineError):
        findings_ratchet(["a"], path)


def test_findings_ratchet_rejects_marked_note(tmp_path: Path) -> None:
    path = _write(tmp_path / "b.json", {"accepted": {"a": UNJUSTIFIED_MARKER + ": explain"}})
    with pytest.raises(BaselineError, match=UNJUSTIFIED_MARKER):
        findings_ratchet(["a"], path)


# 42-T4: baseline_ratchet sees the marker and blank notes in the {"count","note"} shape too.
def test_ratchet_marker_in_dict_entry_fails(tmp_path: Path) -> None:
    _write(tmp_path / "x.json", {"accepted": {"k": {"count": 1, "note": UNJUSTIFIED_MARKER}}})
    code = RatchetBaseline("x", directory=str(tmp_path)).enforce({"k": "d"}, label="L", guidance="g")
    assert code == 1


@pytest.mark.parametrize("note", ["", None])
def test_ratchet_blank_note_fails(tmp_path: Path, note: object) -> None:
    _write(tmp_path / "x.json", {"accepted": {"k": note}})
    code = RatchetBaseline("x", directory=str(tmp_path)).enforce({"k": "d"}, label="L", guidance="g")
    assert code == 1


def test_ratchet_non_string_note_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "x.json", {"accepted": {"k": 5}})
    with pytest.raises(ValueError):
        RatchetBaseline("x", directory=str(tmp_path)).load()


# 42-T5: a _core baseline whose gate or schema header does not match is refused on load.
def test_core_load_rejects_other_gate_header(tmp_path: Path) -> None:
    path = _write(tmp_path / "b.json", {"schema": 1, "gate": "A", "entries": {"k": {"count": 1, "note": "r"}}})
    with pytest.raises(BaselineError):
        Baseline(path, gate="B").load()


def test_core_load_rejects_newer_schema(tmp_path: Path) -> None:
    path = _write(tmp_path / "b.json", {"schema": 2, "gate": "B", "entries": {"k": {"count": 1, "note": "r"}}})
    with pytest.raises(BaselineError):
        Baseline(path, gate="B").load()


def test_core_load_accepts_matching_or_absent_gate(tmp_path: Path) -> None:
    path = _write(tmp_path / "b.json", {"schema": 1, "gate": "B", "entries": {"k": {"count": 1, "note": "r"}}})
    assert Baseline(path, gate="B").load()[0] == {"k": 1}
    path2 = _write(tmp_path / "c.json", {"entries": {"k": {"count": 1, "note": "r"}}})
    assert Baseline(path2, gate="B").load()[0] == {"k": 1}
