"""Tests for py_ci_shared.cuda_kernel_pointer_types."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_kernel_pointer_types import (
    RULE_DOUBLE,
    RULE_FLOAT,
    RULE_PROBE,
    assert_cuda_kernel_pointer_types,
    find_cuda_kernel_pointer_types,
)

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_kernel_pointer_types(root, use_git=False, **kw)


def _assert(root, **kw):
    return assert_cuda_kernel_pointer_types(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _kernel(signature: str, *, lead: str = "") -> bytes:
    return f'K_SRC = r"""\n{lead}extern "C" __global__ void k({signature}) {{\n    out[0] = 1;\n}}\n"""\n'.encode()


def _found(tmp_path: Path, signature: str, **kw):
    lead = kw.pop("lead", "")
    return _find(_corpus(tmp_path, {"m.py": _kernel(signature, lead=lead)}), **kw)


_BARE = "const double* __restrict__ x, const long long n, double* __restrict__ out"


def test_reports_the_seeded_violation(tmp_path):
    """A kernel with `double*` parameters and no `{REAL}` is reported at its signature line, naming the parameters."""
    found = _found(tmp_path, _BARE)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 2, RULE_DOUBLE)]
    assert "kernel `k`" in found[0].message and "x, out" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """The same kernel with a `{REAL}` value pointer and an `{ACC}` output, and one with no floating pointer."""
    assert _found(tmp_path, "const {REAL}* __restrict__ x, const long long n, {ACC}* __restrict__ out") == []
    assert _found(tmp_path, "const int* __restrict__ x, const long long n, int* __restrict__ out") == []


def test_a_review_marker_on_the_signature_line_or_the_one_above_suppresses(tmp_path):
    """`// dtype-ok: reason` is the review: on the signature line or directly above it."""
    assert _found(tmp_path, _BARE, lead="// dtype-ok: timestamps need float64\n") == []
    src = f'K_SRC = r"""\nextern "C" __global__ void k({_BARE}) {{  // dtype-ok: float64 on purpose\n    out[0] = 1;\n}}\n"""\n'.encode()
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_a_marker_without_the_prefix_does_not_suppress(tmp_path):
    """Only `dtype-ok:` counts; a plain comment is not a review."""
    assert [f.rule for f in _found(tmp_path, _BARE, lead="// float64 on purpose\n")] == [RULE_DOUBLE]


def test_a_c_comment_naming_a_double_pointer_is_not_a_signature(tmp_path):
    """A `double*` in a comment, outside any `__global__` signature, is prose."""
    src = b'K_SRC = r"""\n// const double* x was the old signature\nextern "C" __global__ void k(const {REAL}* x, int n) {\n    ;\n}\n"""\n'
    assert _find(_corpus(tmp_path, {"m.py": src})) == []


def test_a_float_pointer_is_advisory(tmp_path):
    """`float*` is reported only with include_advisory: a float64 buffer read as float is wrong values, not an out-of-bounds read."""
    sig = "const float* __restrict__ x, const long long n, float* __restrict__ out"
    assert _found(tmp_path, sig) == []
    assert [f.rule for f in _found(tmp_path, sig, include_advisory=True)] == [RULE_FLOAT]


def test_a_kernel_class_argument_is_a_kernel_source_whatever_it_is_called(tmp_path):
    """A string passed straight to `cp.RawKernel` counts even when its constant name carries no `_SRC` suffix."""
    src = b'import cupy as cp\nTEXT = r"""\nextern "C" __global__ void k(const double* x, int n) {\n    ;\n}\n"""\nk = cp.RawKernel(TEXT, "k")\n'
    assert [f.rule for f in _find(_corpus(tmp_path, {"m.py": src}))] == [RULE_DOUBLE]


_PROBE = "def f(cp, mad, out):\n" "    k = get(cp, 'float' if mad.dtype == cp.float32 else 'double')\n" "    k((1,), (1,), (mad.astype(cp.float32), out))\n"


def test_a_type_probe_on_an_array_cast_before_the_launch_is_reported(tmp_path):
    """The probe names `mad`, but the launch tuple holds `mad.astype(...)`: a double kernel would run on a float32 buffer."""
    found = _find(_corpus(tmp_path, {"m.py": _PROBE.encode()}))
    assert [(f.line, f.rule) for f in found] == [(2, RULE_PROBE)]
    assert "`mad`" in found[0].message and "`f`" in found[0].message


def test_a_type_probe_on_the_launched_buffer_is_accepted(tmp_path):
    """Casting first and probing the cast array, which is then launched by name, is the correct shape."""
    good = "def f(cp, mad, out):\n    mad_c = mad.astype(cp.float32)\n    k = get(cp, 'float' if mad_c.dtype == cp.float32 else 'double')\n    k((1,), (1,), (mad_c, out))\n"
    assert _find(_corpus(tmp_path, {"m.py": good.encode()})) == []


def test_a_probe_launched_in_another_function_of_the_module_is_accepted(tmp_path):
    """The table is built in one method and launched in another: launch arguments are collected module-wide."""
    src = "class C:\n    def build(self, cp, x):\n        self.k = get(cp, 'float' if x.dtype == cp.float32 else 'double')\n    def run(self, x, out):\n        self.k((1,), (1,), (x, out))\n"
    assert _find(_corpus(tmp_path, {"m.py": src.encode()})) == []


def test_a_probe_marker_suppresses(tmp_path):
    """`# dtype-ok:` on the probe line is the review."""
    src = _PROBE.replace("else 'double')", "else 'double')  # dtype-ok: x is cast to float32 by the caller")
    assert _find(_corpus(tmp_path, {"m.py": src.encode()})) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """The BOM must not hide the kernel."""
    plain = _found(tmp_path / "a", _BARE)
    bom = _find(_corpus(tmp_path / "b", {"m.py": BOM + _kernel(_BARE)}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain] == [(2, RULE_DOUBLE)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A file the gate cannot read is a finding of its own, not a pass."""
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python files must not pass for a clean scan."""
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_raises_with_the_guidance_and_passes_a_clean_corpus(tmp_path):
    """The assertion names the fix; a corpus with only templated kernels passes."""
    with pytest.raises(AssertionError, match=r"\{REAL\}.*dtype-ok"):
        _assert(_corpus(tmp_path / "bad", {"m.py": _kernel(_BARE)}))
    _assert(_corpus(tmp_path / "good", {"m.py": _kernel("const {REAL}* x, {ACC}* out")}))


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    """With a baseline file, a recorded finding passes and an unrecorded one fails; refresh writes the baseline."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": _kernel(_BARE)})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):  # a refresh writes the baseline and skips the test that asked for it
        _assert(root, baseline_path=baseline, refresh=True)
    _assert(root, baseline_path=baseline)
    (root / "n.py").write_bytes(_kernel(_BARE).replace(b"void k(", b"void other("))
    with pytest.raises(pytest.fail.Exception):
        _assert(root, baseline_path=baseline)
