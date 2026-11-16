"""49-T2: a module ``main`` must not crash with a traceback on a console whose codepage lacks a character in a path."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared._core.console import write_text

SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_write_text_escapes_characters_the_stream_cannot_encode() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1251", errors="strict", newline="")
    write_text("src/数据.py:4: x\n", stream=stream)
    stream.flush()
    assert raw.getvalue().decode("cp1251") == "src/\\u6570\\u636e.py:4: x\n"


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252", "ascii"])
def test_import_cycles_main_survives_a_narrow_console(tmp_path: Path, encoding: str) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from . import 数据\n", encoding="utf-8")
    (pkg / "数据.py").write_text("from . import a\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": encoding, "PYTHONPATH": SRC}
    done = subprocess.run(
        [sys.executable, "-m", "py_ci_shared.import_cycles", str(tmp_path)],
        capture_output=True,
        env=env,
        timeout=120,
    )
    out = done.stdout.decode(encoding, "replace") + done.stderr.decode(encoding, "replace")
    assert "Traceback" not in out, out
    assert "finding(s)" in out
    assert done.returncode == 1
