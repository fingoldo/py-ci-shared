"""comment_cites_absolute_line skips the tokenize pass for files with no line citation (audit 2026-11-16 48-T3)."""

from __future__ import annotations

from pathlib import Path

import py_ci_shared.code_audit.comment_names_missing_symbol as cnms
from py_ci_shared.code_audit import scan_comment_cites_absolute_line

from ._helpers import _write


def test_files_without_a_line_citation_are_not_tokenized(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "plain.py", "# nothing to cite here\nx = 1\n")
    _write(tmp_path, "other.py", "y = 2  # just prose\n")
    seen: list[Path] = []
    real = cnms._comment_texts

    def counting(py: Path):
        seen.append(py)
        return real(py)

    monkeypatch.setattr(cnms, "_comment_texts", counting)
    assert scan_comment_cites_absolute_line(tmp_path) == []
    assert seen == []


def test_a_citation_is_still_reported(tmp_path: Path) -> None:
    _write(tmp_path, "cite.py", "x = 1  # see line 1 for the cause\n")
    findings = scan_comment_cites_absolute_line(tmp_path)
    assert len(findings) == 1, findings
    assert findings[0].check == "comment_cites_absolute_line"
