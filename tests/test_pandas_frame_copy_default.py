"""Tests for py_ci_shared.pandas_frame_copy_default."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.pandas_frame_copy_default import PandasFrameCopyWarning, assert_pandas_frame_copy_default, find_pandas_frame_copy_default

BOM = b"\xef\xbb\xbf"
VIOLATION = b"import numpy as np\nimport pandas as pd\n\nbig = np.zeros((10, 3))\nframe = pd.DataFrame(big, columns=['a', 'b', 'c'])\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def test_reports_the_seeded_violation(tmp_path):
    found = find_pandas_frame_copy_default(_corpus(tmp_path, {"m.py": VIOLATION}), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 5, "pandas-frame-copy-default")]
    assert "without copy=" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    clean = (
        b"import pandas as pd\nimport pandas as pd2\nbig = 1\n"
        b"a = pd.DataFrame(big, copy=False)\n"
        b"b = pd.DataFrame([[1, 2], [3, 4]])\n"
        b"c = pd.DataFrame({'x': [1, 2], 'y': (3, 4)})\n"
        b"d = pd.DataFrame(columns=['a'])\n"
        b"e = pd.DataFrame(index=[1])\n"
        b"f = pd.DataFrame(big, **kw)\n"
        b"g = pd.DataFrame(big)  # copy-ok: small\n"
        b"h = other.DataFrame(big)\n"
    )
    assert find_pandas_frame_copy_default(_corpus(tmp_path, {"m.py": clean}), use_git=False) == []


def test_aliased_import_and_data_keyword_are_reported(tmp_path):
    src = b"from pandas import DataFrame as DF\nx = DF(data=arr)\n"
    found = find_pandas_frame_copy_default(_corpus(tmp_path, {"m.py": src}), use_git=False)
    assert [f.line for f in found] == [2]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_pandas_frame_copy_default(_corpus(tmp_path / "a", {"m.py": VIOLATION}), use_git=False)
    bom = find_pandas_frame_copy_default(_corpus(tmp_path / "b", {"m.py": BOM + VIOLATION}), use_git=False)
    assert [(f.line, f.rule) for f in plain] == [(f.line, f.rule) for f in bom] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": VIOLATION, "broken.py": b"x = pd.DataFrame(big, columns=[\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_pandas_frame_copy_default(root, use_git=False)
    assert len(find_pandas_frame_copy_default(root, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_pandas_frame_copy_default(tmp_path, use_git=False)


def test_advisory_warns_and_strict_mode_fails(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION})
    with pytest.warns(PandasFrameCopyWarning, match="copy="):
        assert_pandas_frame_copy_default(root, use_git=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(AssertionError, match="1 pandas-frame-copy-default finding"):
            assert_pandas_frame_copy_default(root, advisory=False, use_git=False)
