"""Tests for py_ci_shared.cuda_kernel_names."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_kernel_names import RULE, assert_cuda_kernel_names, find_cuda_kernel_names

BOM = b"\xef\xbb\xbf"

TWO_KERNELS = b'A = r"""\nextern "C" __global__ void alpha_kernel(float* x) {}\n"""\nB = r"""\nextern "C" __global__ void beta_kernel(float* x) {}\n"""\n'


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root, **kw):
    return find_cuda_kernel_names(root, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    """A file with two kernels of which the compile check built one: the other is reported at its line, with the rule and the name."""
    root = _corpus(tmp_path, {"k.py": TWO_KERNELS})
    found = _find(root, compiled={"k.py": ['extern "C" __global__ void alpha_kernel(float* x) {}']})
    assert [(f.path, f.rule) for f in found] == [("k.py", RULE)]
    assert "beta_kernel" in found[0].message and found[0].line == 5


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """Every name compiled (in one source or spread over several), a file without kernels, and an excluded path produce nothing."""
    root = _corpus(tmp_path, {"k.py": TWO_KERNELS, "plain.py": b"x = 1\n", "_benchmarks/b.py": TWO_KERNELS})
    both = ["__global__ void alpha_kernel(float* x) {}", "__global__ void beta_kernel(float* x) {}"]
    assert _find(root, compiled={"k.py": both}, exclude=("_benchmarks",)) == []
    one_source = ["__global__ void alpha_kernel(float* x) {}\n__global__ void beta_kernel(float* x) {}"]
    assert _find(root, compiled={"k.py": one_source}, exclude=("_benchmarks",)) == []


def test_a_file_with_no_compiled_source_reports_every_kernel(tmp_path):
    """A file the compile check never built is the extreme case of the gap: each of its kernels is reported."""
    root = _corpus(tmp_path, {"k.py": TWO_KERNELS})
    assert len(_find(root, compiled={})) == 2


def test_launch_bounds_and_void_placement_do_not_hide_the_name(tmp_path):
    """``__global__ __launch_bounds__(128) void name(`` and ``__global__ void __launch_bounds__(128) name(`` both name the kernel."""
    src = b'S = """\n__global__ __launch_bounds__(128) void first(int* a) {}\n__global__ void __launch_bounds__(128, 4) second(int* a) {}\n"""\n'
    root = _corpus(tmp_path, {"k.py": src})
    found = _find(root, compiled={})
    assert sorted(f.message.split()[2] for f in found) == ["first", "second"]


def test_a_template_name_is_matched_by_its_fixed_prefix(tmp_path):
    """``bin_codes_TYPENAME`` is compiled under concrete names: a compiled name that starts with ``bin_codes_`` covers it; a short prefix covers nothing."""
    src = b'S = """\n__global__ void bin_codes_TYPENAME(int* a) {}\n__global__ void TYPENAME_x(int* a) {}\n"""\n'
    root = _corpus(tmp_path, {"k.py": src})
    found = _find(root, compiled={"k.py": ["__global__ void bin_codes_i8(int* a) {}", "__global__ void anything(int* a) {}"]})
    assert [f.message.split()[2] for f in found] == ["TYPENAME_x"]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """The BOM must not hide the kernels."""
    plain = _find(_corpus(tmp_path / "a", {"k.py": TWO_KERNELS}), compiled={})
    bom = _find(_corpus(tmp_path / "b", {"k.py": BOM + TWO_KERNELS}), compiled={})
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain]
    assert len(plain) == 2


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A file with a syntax error raises ``UnparsedFilesError`` naming it; ``allow_unparsed=True`` skips it."""
    root = _corpus(tmp_path, {"good.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root, compiled={})
    assert _find(root, compiled={}, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python files raises ``EmptyScanError``."""
    with pytest.raises(EmptyScanError):
        _find(_corpus(tmp_path, {"notes.txt": b"x"}), compiled={}, min_files=1)


def test_the_assert_names_every_missing_kernel(tmp_path):
    """``assert_cuda_kernel_names`` raises with the findings and passes once they are compiled."""
    root = _corpus(tmp_path, {"k.py": TWO_KERNELS})
    with pytest.raises(AssertionError, match=r"never compiled offline"):
        assert_cuda_kernel_names(root, compiled={}, use_git=False)
    both = {"k.py": ["__global__ void alpha_kernel(float* x) {}", "__global__ void beta_kernel(float* x) {}"]}
    assert_cuda_kernel_names(root, compiled=both, use_git=False)
