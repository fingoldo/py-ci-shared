"""Tests for py_ci_shared.cuda_launch_bounds_literal."""

from __future__ import annotations

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_launch_bounds_literal import RULE, assert_cuda_launch_bounds_literal, find_cuda_launch_bounds_literal


def _src(tmp_path, line, name="k.py"):
    (tmp_path / name).write_text('SRC = """\n' + line + '\n"""\n', encoding="utf-8")
    return tmp_path


def _find(root, **kw):
    return find_cuda_launch_bounds_literal(root, use_git=False, **kw)


@pytest.mark.parametrize("line", ["__global__ void __launch_bounds__(256, 8) k()", "__global__ void __launch_bounds__(MLF_T, 4) k()", "__launch_bounds__(512,3)"])
def test_literal_min_blocks_that_does_not_fit_is_reported(tmp_path, line):
    found = _find(_src(tmp_path, line))
    assert [f.rule for f in found] == [RULE] and found[0].line == 2


@pytest.mark.parametrize(
    "line",
    ["__launch_bounds__(128, 4)", "__launch_bounds__(256, MLF_THREADS_PER_SM / 256)", "// __launch_bounds__(256, 8)", "__launch_bounds__(256)", "__launch_bounds__(256, 8) // launch-bounds-ok: sm_80 only"],
)
def test_portable_computed_commented_or_marked_passes(tmp_path, line):
    assert _find(_src(tmp_path, line)) == []


def test_empty_reason_does_not_suppress(tmp_path):
    assert _find(_src(tmp_path, "__launch_bounds__(256, 8) // launch-bounds-ok:"))


def test_resident_limit_parameter_and_exclude(tmp_path):
    root = _src(tmp_path, "__launch_bounds__(256, 8)")
    assert not _find(root, max_resident_threads=2048)
    (tmp_path / "_benchmarks").mkdir()
    _src(tmp_path / "_benchmarks", "__launch_bounds__(256, 8)", "b.py")
    assert len(_find(root)) == 2 and len(_find(root, exclude=("_benchmarks",))) == 1


def test_floor_unparsed_and_message(tmp_path):
    root = _src(tmp_path, "__launch_bounds__(256, 8)")
    with pytest.raises(EmptyScanError):
        _find(root, min_files=3)
    with pytest.raises(AssertionError, match=r"k\.py:2: \[cuda-literal-min-blocks\]"):
        assert_cuda_launch_bounds_literal(root, use_git=False)
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError):
        _find(root)
