"""Tests for py_ci_shared.cuda_wide_load_alignment (byte-pointer casts to a wider type need a pitch parameter)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_wide_load_alignment import RULE, assert_cuda_wide_load_alignment, find_cuda_wide_load_alignment

CANARY_ROOT = Path(__file__).parent / "canary" / "cuda_wide_load_alignment"


def _canary(kind: str) -> Path:
    """A fresh directory holding the ``.canary`` fixtures of *kind* under their ``.py`` names (the scanner reads ``*.py`` only)."""
    dest = Path(tempfile.mkdtemp())
    for src in (CANARY_ROOT / kind).glob("*.canary"):
        (dest / src.name[: -len(".canary")]).write_bytes(src.read_bytes())
    return dest


def _find(root, **kw):
    return find_cuda_wide_load_alignment(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _kernel(signature: str, body: str) -> bytes:
    return ('_SRC = r"""\nextern "C" __global__ void k(' + signature + ") {\n" + body + "\n}\n" + '"""\n').encode()


def test_canary_violation_fires():
    assert [f.rule for f in _find(_canary("violation"))] == [RULE, RULE]


def test_canary_clean_is_silent():
    assert _find(_canary("clean")) == []


def test_canary_bom_still_parses_and_fires():
    # The BOM canary is the violation canary with a BOM prefix: it must fire the same findings.
    assert [f.rule for f in _find(_canary("bom"))] == [RULE, RULE]


def test_canary_unparsable_is_a_finding_not_a_pass():
    with pytest.raises(UnparsedFilesError):
        _find(_canary("unparsable"), min_files=0)


def test_byte_cast_without_pitch_is_reported(tmp_path):
    src = _kernel("const unsigned char* codes, int* out", "const unsigned int* c = (const unsigned int*)(codes + blockIdx.x * 7);\nout[0] = c[0];")
    found = _find(_corpus(tmp_path, {"m.py": b"_SRC = 1\n" + src}))
    assert [(f.path, f.rule) for f in found] == [("m.py::_SRC", RULE)]


@pytest.mark.parametrize("param", ["stride", "pitch", "ld", "n_pad", "row_stride4", "ld_codes"])
def test_pitch_parameter_names_clear_the_kernel(tmp_path, param):
    src = _kernel(
        f"const unsigned char* codes, const long long {param}, int* out",
        "const unsigned int* c = (const unsigned int*)(codes + blockIdx.x * " + param + ");\nout[0] = c[0];",
    )
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_reinterpret_cast_to_int4_is_reported(tmp_path):
    src = _kernel("const unsigned char* counts, const int M", "const int4* c = reinterpret_cast<const int4*>(counts + blockIdx.x * M);")
    assert [f.rule for f in _find(_corpus(tmp_path, {"m.py": src}))] == [RULE]


def test_word_array_cast_is_not_a_byte_cast(tmp_path):
    src = _kernel("const unsigned int* bits, int* out", "const unsigned long long* w = (const unsigned long long*)(bits + blockIdx.x);\nout[0] = (int)w[0];")
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_bare_base_cast_then_word_indexing_is_not_reported(tmp_path):
    """Narrowing (measured: 4 of the first 5 corpus hits): a bare base cast indexed in words is aligned when the base is."""
    src = _kernel("const unsigned char* ybits, int* out", "const unsigned int* yb = (const unsigned int*)ybits;\nout[0] = (int)yb[blockIdx.x * 3];")
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_byte_cast_to_byte_is_not_reported(tmp_path):
    src = _kernel("const unsigned char* codes, int* out", "const unsigned char* c = (const unsigned char*)(codes + blockIdx.x);\nout[0] = c[0];")
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_marker_with_reason_suppresses(tmp_path):
    src = _kernel(
        "const unsigned char* codes, const long long n, int* out",
        "const unsigned int* c = (const unsigned int*)(codes + blockIdx.x * n); // align-ok: host pads rows to a multiple of 4",
    )
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_marker_without_reason_does_not_suppress(tmp_path):
    src = _kernel(
        "const unsigned char* codes, const long long n, int* out",
        "const unsigned int* c = (const unsigned int*)(codes + blockIdx.x * n); // align-ok:",
    )
    assert len(_find(_corpus(tmp_path, {"m.py": src}))) == 1


def test_empty_corpus_is_an_error(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=5)


def test_assert_fails_on_a_finding(tmp_path):
    src = _kernel("const unsigned char* codes, int* out", "const unsigned int* c = (const unsigned int*)(codes + blockIdx.x);\nout[0] = c[0];")
    with pytest.raises(AssertionError, match="cuda-wide-load-alignment"):
        assert_cuda_wide_load_alignment(_corpus(tmp_path, {"m.py": src}), use_git=False)
