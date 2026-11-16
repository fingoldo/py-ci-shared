"""49-T5: a PASS says what it checked: files read, findings accepted, unparsed files allowed."""

from __future__ import annotations

from py_ci_shared._core.outcome import collect_notes, pass_note
from py_ci_shared._core.scan import ScanResult
from py_ci_shared._gate_run import enforce_findings


def test_pass_note_lists_what_was_checked() -> None:
    assert (
        pass_note(files_read=12, accepted=2, baseline="x.json", unparsed_allowed=1)
        == "read 12 file(s); 2 finding(s) accepted by x.json; 1 unparsed file(s) allowed"
    )


def test_pass_note_omits_unparsed_when_none_were_allowed() -> None:
    assert pass_note(files_read=3, accepted=0) == "read 3 file(s); 0 finding(s) accepted"


def test_a_gate_pass_records_its_note(tmp_path) -> None:
    scan = ScanResult(root=tmp_path, files=[], min_files=0)
    with collect_notes() as notes:
        enforce_findings(
            [],
            scan,
            gate="demo",
            flag="--refresh-demo-baseline",
            min_files=0,
            baseline_path=None,
            refresh=False,
            request=None,
            guidance="g",
        )
    assert notes and notes[0].startswith("read 0 file(s)")
