"""Tests for py_ci_shared.launch_dtype_audit. Every test runs on a CPU-only runner: arrays are fakes (dtype/shape/flags), the kernel is a Python callable, cupy is a namespace stand-in."""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

from py_ci_shared.launch_dtype_audit import (
    LaunchDtypeError,
    Mismatch,
    assert_launch_dtypes,
    audit_cupy,
    audited_kernel,
    check_launch_args,
    kernel_parameters,
    main,
)

TEMPLATE = """
extern "C" __global__ void row_argmax(const {REAL}* x, int* out, const int n, const {ACC} scale) { out[0] = 0; }
"""


class Arr:
    """A device-array stand-in: only what the audit reads."""

    def __init__(self, dtype, shape=(4,), contiguous=True):
        self.dtype = dtype
        self.shape = shape
        self.flags = types.SimpleNamespace(c_contiguous=contiguous, f_contiguous=False)


class Scalar(float):
    """A numpy-scalar stand-in: a Number with a dtype."""

    def __new__(cls, value, dtype):
        obj = super().__new__(cls, value)
        obj.dtype = dtype
        return obj


def test_a_templated_kernel_is_parsed_only_after_the_placeholders_are_filled():
    unfilled = kernel_parameters(TEMPLATE, "row_argmax")
    assert [p.dtype for p in unfilled] == [None, "int32", "int32", None]
    filled = kernel_parameters(TEMPLATE, "row_argmax", {"REAL": "float", "ACC": "double"})
    assert [(p.is_pointer, p.dtype) for p in filled] == [(True, "float32"), (True, "int32"), (False, "int32"), (False, "float64")]


def test_the_float32_buffer_bound_to_the_double_instance_is_the_reported_mismatch():
    args = (Arr("float32"), Arr("int32"), 4, 1.0)
    with pytest.raises(LaunchDtypeError, match=r"argument #0: expects float64\*, got a float32 array"):
        assert_launch_dtypes(TEMPLATE, "row_argmax", args, {"REAL": "double", "ACC": "double"})
    assert_launch_dtypes(TEMPLATE, "row_argmax", args, {"REAL": "float", "ACC": "double"})


def test_unresolved_macro_parameters_are_skipped_not_guessed():
    params = kernel_parameters(TEMPLATE, "row_argmax")
    assert check_launch_args(params, (Arr("float64"), Arr("int32"), 4, 1.0), "row_argmax") == []


def test_typedef_and_define_in_the_source_resolve():
    src = "typedef float REAL;\n#define IDX long long\n__global__ void k(const REAL* a, IDX* b) {}\n"
    assert [p.dtype for p in kernel_parameters(src, "k")] == ["float32", "int64"]


def test_pointer_checks_array_dtype_contiguity_and_scalar_in_array_slot():
    params = kernel_parameters("__global__ void k(const float* a) {}", "k")
    assert check_launch_args(params, (Arr("float32"),)) == []
    assert check_launch_args(params, (Arr("float32", contiguous=False),))[0].got == "a strided view"
    assert check_launch_args(params, (3.0,))[0].got == "a scalar float"
    assert check_launch_args(params, ("text",))[0].got == "a str"


def test_scalar_checks_kind_and_width():
    params = kernel_parameters("__global__ void k(int n, double s) {}", "k")
    assert check_launch_args(params, (Scalar(4, "int32"), Scalar(1.0, "float64"))) == []
    assert check_launch_args(params, (Scalar(4, "int64"), 1.0)) == []
    kinds = {m.position: m.got for m in check_launch_args(params, (Scalar(4, "float32"), Scalar(1.0, "float32")))}
    assert kinds == {0: "float32", 1: "the narrower float32"}
    assert check_launch_args(params, (2.5, 1.0))[0].got == "a python float"


def test_a_wrong_argument_count_is_reported():
    params = kernel_parameters("__global__ void k(const float* a, int n) {}", "k")
    (problem,) = check_launch_args(params, (Arr("float32"),), "k")
    assert problem == Mismatch("k", -1, "2 argument(s)", "1")


def test_unknown_kernel_name_is_an_error_not_a_pass():
    with pytest.raises(LaunchDtypeError, match="not declared"):
        assert_launch_dtypes(TEMPLATE, "missing", ())
    with pytest.raises(LaunchDtypeError, match="not declared"):
        audited_kernel(lambda *a, **k: None, TEMPLATE, "missing")


def test_audited_kernel_refuses_before_the_launch_when_strict():
    launched = []
    kernel = audited_kernel(lambda g, b, a, **kw: launched.append(a), TEMPLATE, "row_argmax", {"REAL": "double", "ACC": "double"})
    with pytest.raises(LaunchDtypeError, match="launch refused"):
        kernel((1,), (32,), (Arr("float32"), Arr("int32"), 4, 1.0))
    assert launched == []
    assert set(kernel.mismatches) == {("row_argmax", 0, "float64*", "a float32 array")}


def test_audited_kernel_records_and_launches_when_not_strict_and_forwards_attributes():
    launched = []

    def raw(g, b, a, **kw):
        launched.append((g, b, kw))

    raw.max_threads = 1024
    kernel = audited_kernel(raw, TEMPLATE, "row_argmax", {"REAL": "double", "ACC": "double"}, strict=False)
    kernel((1,), (32,), (Arr("float32"), Arr("int32"), 4, 1.0), shared_mem=8)
    assert launched == [((1,), (32,), {"shared_mem": 8})]
    assert len(kernel.mismatches) == 1 and kernel.max_threads == 1024


def test_a_matching_launch_passes_through_the_return_value():
    kernel = audited_kernel(lambda g, b, a: "ok", "__global__ void k(const float* a) {}", "k")
    assert kernel(1, 1, (Arr("float32"),)) == "ok"


def _fake_cupy():
    class RawKernel:
        def __init__(self, source, name, *a, **k):
            self.name = name

        def __call__(self, grid, block, args, **kw):
            return "launched"

    class RawModule:
        def __init__(self, *a, **k):
            self.code = k.get("code", a[0] if a else "")

        def get_function(self, name):
            return lambda grid, block, args, **kw: "module-launched"

    return types.SimpleNamespace(RawKernel=RawKernel, RawModule=RawModule)


def test_audit_cupy_wraps_both_entry_points_records_and_restores_the_namespace():
    cp = _fake_cupy()
    originals = (cp.RawKernel, cp.RawModule)
    with audit_cupy(cupy_module=cp) as found:
        k = cp.RawKernel("__global__ void kk(const float* a) {}", "kk")
        assert k((1,), (1,), (Arr("float64"),)) == "launched"
        fn = cp.RawModule(code="__global__ void mm(double* a) {}").get_function("mm")
        assert fn((1,), (1,), (Arr("float32"),)) == "module-launched"
        assert fn((1,), (1,), (Arr("float64"),)) == "module-launched"
    assert set(found) == {("kk", 0, "float32*", "a float64 array"), ("mm", 0, "float64*", "a float32 array")}
    assert (cp.RawKernel, cp.RawModule) == originals


def test_audit_cupy_strict_raises_and_still_restores():
    cp = _fake_cupy()
    originals = (cp.RawKernel, cp.RawModule)
    with pytest.raises(LaunchDtypeError):
        with audit_cupy(strict=True, cupy_module=cp):
            cp.RawKernel("__global__ void kk(const float* a) {}", "kk")((1,), (1,), (Arr("float64"),))
    assert (cp.RawKernel, cp.RawModule) == originals


def test_importing_and_checking_never_imports_cupy_or_numpy():
    code = (
        "import sys\n"
        "sys.modules['cupy'] = None\n"
        "import py_ci_shared.launch_dtype_audit as m\n"
        "class A:\n"
        "    dtype = 'float32'\n"
        "    shape = (2,)\n"
        "m.assert_launch_dtypes('__global__ void k(const float* a) {}', 'k', (A(),))\n"
        "assert 'numpy' not in sys.modules, 'numpy imported'\n"
    )
    # The probe child must not write into the parent's coverage data (statement data next to the parent's branch data breaks the combine).
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE_", "COVERAGE_"))}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
    assert done.returncode == 0, done.stderr


def test_main_runs_a_script_under_the_audit_and_exits_one_on_a_mismatch(tmp_path, monkeypatch, capsys):
    cp = _fake_cupy()
    monkeypatch.setitem(sys.modules, "cupy", cp)
    script = tmp_path / "launch.py"
    script.write_text(
        "import cupy\n"
        "class A:\n"
        "    dtype = 'float64'\n"
        "    shape = (2,)\n"
        "cupy.RawKernel('__global__ void kk(const float* a) {}', 'kk')((1,), (1,), (A(),))\n",
        encoding="utf-8",
    )
    assert main([str(script)]) == 1
    out = capsys.readouterr().out
    assert "ARG_DTYPE_MISMATCHES 1" in out and "kernel=kk arg#0: expects float32*, got a float64 array" in out
    script.write_text("import cupy\n", encoding="utf-8")
    assert main([str(script)]) == 0


def test_real_numpy_arrays_and_scalars_are_judged_like_the_fakes():
    np = pytest.importorskip("numpy")
    src = "__global__ void k(const {REAL}* x, long long* out, const int n, const double s) {}"
    ok = (np.zeros(4, np.float32), np.zeros(1, np.int64), np.int32(4), np.float64(1.0))
    assert_launch_dtypes(src, "k", ok, {"REAL": "float"})
    with pytest.raises(LaunchDtypeError, match=r"argument #0: expects float64\*, got a float32 array"):
        assert_launch_dtypes(src, "k", ok, {"REAL": "double"})
    strided = (np.zeros(8, np.float32)[::2], ok[1], ok[2], ok[3])
    with pytest.raises(LaunchDtypeError, match="strided view"):
        assert_launch_dtypes(src, "k", strided, {"REAL": "float"})
    narrow = (ok[0], ok[1], np.float32(4), np.float32(1.0))
    with pytest.raises(LaunchDtypeError, match=r"argument #2: expects scalar int32, got float32"):
        assert_launch_dtypes(src, "k", narrow, {"REAL": "float"})
