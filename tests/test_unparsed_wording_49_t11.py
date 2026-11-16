"""49-T11: the unparsed-file error names the switch that allows skipping such files, and the way to fix them."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import UnparsedFilesError
from py_ci_shared._core.scan import scan_python


def test_unparsed_error_names_the_allow_unparsed_switch(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    scan = scan_python(tmp_path, min_files=0)
    with pytest.raises(UnparsedFilesError) as info:
        scan.assert_ok()
    message = str(info.value)
    assert "allow_unparsed" in message
    assert "broken.py" in message
