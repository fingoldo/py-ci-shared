"""49-T3: a text-ratchet failure carries its whole report in the exception, and a pass says so under its row."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared import _text_ratchet
from py_ci_shared._core.outcome import collect_notes
from py_ci_shared._core.scan import take_files_read

GUIDANCE = "A guard that prints SKIPPED and exits 0 passes with nothing checked. Exit non-zero with the reason."


def _write(root: Path, accepted: dict[str, str]) -> None:
    directory = root / "tool" / "meta" / "baselines"
    directory.mkdir(parents=True)
    (directory / "demo_gate.json").write_text(json.dumps({"accepted": accepted}), encoding="utf-8")


def _enforce(root: Path, found: dict[str, str]) -> AssertionError:
    with pytest.raises(AssertionError) as info:
        _text_ratchet.enforce_findings(
            "demo_gate", found, repo_root=root, baseline_dir=_text_ratchet.DEFAULT_BASELINE_DIR, label="demo: check", guidance=GUIDANCE
        )
    return info.value


def test_new_violations_are_listed_one_per_line_with_guidance(tmp_path: Path) -> None:
    _write(tmp_path, {})
    message = str(_enforce(tmp_path, {"a.py:1": "first", "b.py:2": "second"}))
    assert message.startswith("demo: check: 2 new violation(s):")
    assert "\n  a.py:1 - first\n" in message
    assert "\n  b.py:2 - second\n" in message
    assert GUIDANCE in message
    assert "with a note saying why" in message


def test_stale_entry_is_not_reported_as_zero_new_violations(tmp_path: Path) -> None:
    _write(tmp_path, {"tool/gone.sh:3": "old note", "other.sh:1": "kept on purpose"})
    message = str(_enforce(tmp_path, {"other.sh:1": "still here"}))
    assert "0 new violation" not in message
    assert "no longer violate the rule" in message
    assert "tool/gone.sh:3" in message


def test_unjustified_entry_says_it_needs_a_note(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py:1": ""})
    message = str(_enforce(tmp_path, {"a.py:1": "x"}))
    assert "no note" in message
    assert "a.py:1" in message
    assert "0 new violation" not in message


def test_pass_is_a_note_not_a_stdout_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, {"a.py:1": "reason"})
    # A scan in an earlier test leaves its file count unconsumed; this test has no scan of its own.
    take_files_read()
    with collect_notes() as notes:
        _text_ratchet.enforce_findings(
            "demo_gate",
            {"a.py:1": "x"},
            repo_root=tmp_path,
            baseline_dir=_text_ratchet.DEFAULT_BASELINE_DIR,
            label="demo: check",
            guidance=GUIDANCE,
        )
    assert capsys.readouterr().out == ""
    assert notes == ["demo: check: no new violations (1 accepted, baselined)"]
