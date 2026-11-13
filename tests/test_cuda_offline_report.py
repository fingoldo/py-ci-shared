"""Tests for py_ci_shared.cuda_offline_report: synthetic reports always, real NVRTC and ptxas builds where the toolkit exists."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared.cuda_offline_report import (
    ARCH_LIMITS,
    REGISTER_FILE,
    build_ptx_and_report,
    entry_reports,
    find_ptxas,
    launch_bounds_of,
    launch_bounds_violations,
    markdown,
    occupancy,
    required_arch,
)

PTX = ".visible .entry k(\n)\n.maxntid 128, 1, 1\n.minnctapersm 16\n{\n  add.f64 %fd1, %fd2, %fd3;\n  fma.rn.f32 %f1, %f2, %f3, %f4;\n  ret;\n}\n"


def _toolchain():
    """``(ptxas path)`` or skip: the real-build tests need NVRTC through cupy and the toolkit's ptxas."""
    pytest.importorskip("cupy_backends.cuda.libs.nvrtc")
    ptxas = find_ptxas()
    if ptxas is None:
        pytest.skip("no ptxas on PATH or under CUDA_PATH")
    return ptxas


def test_launch_bounds_are_read_from_the_ptx():
    """``.maxntid`` and ``.minnctapersm`` of an entry are returned by entry name."""
    assert launch_bounds_of(PTX) == {"k": (128, 16)}


def test_launch_bounds_violation_is_detected_on_synthetic_resource_report():
    """A register count above REGISTER_FILE // (threads * blocks) is reported; one at or under the cap is not (the mutant check of the launch-bounds rule)."""
    cap = REGISTER_FILE // (128 * 16)
    over = f"Compiling entry function 'k' for 'sm_61'\nUsed {cap + 8} registers"
    under = f"Compiling entry function 'k' for 'sm_61'\nUsed {cap} registers"
    assert launch_bounds_violations(PTX, over) == [("k", cap + 8, cap)]
    assert launch_bounds_violations(PTX, under) == []


def test_entry_reports_join_resource_lines_and_opcode_counts_by_entry_name():
    """Registers, shared memory, stack and spill bytes come from the ptxas lines; the f64 and f32 arithmetic counts from the PTX body of the same entry."""
    report = (
        "ptxas info    : Function properties for k\n"
        "    8 bytes stack frame, 16 bytes spill stores, 4 bytes spill loads\n"
        "ptxas info    : Compiling entry function 'k' for 'sm_61'\n"
        "ptxas info    : Used 40 registers, 1536 bytes smem\n"
    )
    rows = entry_reports("v[a]", 61, PTX, report)
    assert [(r.entry, r.registers, r.static_shared, r.stack, r.spill, r.fp64_ops, r.fp32_ops) for r in rows] == [("k", 40, 1536, 8, 20, 1, 1)]
    assert rows[0].fp64_share == pytest.approx(0.5)


def test_failure_table_lists_each_failing_variant_per_arch():
    """The report's failure table names every recorded failure with its arch, and says so when there is none."""
    text = markdown([], {}, failures=[("pkg/x.py::_SRC[float]", 61, "NVRTC failed on sm_61")])
    assert "| pkg/x.py::_SRC[float] | sm_61 | NVRTC failed on sm_61 |" in text
    assert "none: every checked variant assembled" in markdown([], {}, failures=[])


def test_occupancy_is_a_fraction_and_falls_with_registers():
    """Theoretical occupancy stays in [0, 1] and never rises when a kernel uses more registers, on every listed architecture."""
    for arch in ARCH_LIMITS:
        low, _ = occupancy(arch, 32, 0, 256)
        high, _ = occupancy(arch, 128, 0, 256)
        assert 0.0 <= high <= low <= 1.0


def test_the_minimum_architecture_of_an_intrinsic_is_read_from_the_source():
    """``__dp4a`` needs sm_61 and ``cp.async`` sm_80; a kernel with neither reports the floor."""
    assert required_arch("int x = __dp4a(a, b, c);") == 61
    assert required_arch('asm volatile("cp.async.ca.shared.global [%0], [%1], 4;");') == 80
    assert required_arch("float y = x * 2.0f;") <= 61


def test_find_ptxas_honours_cuda_path(tmp_path, monkeypatch):
    """A ptxas under ``CUDA_PATH/bin`` is found, and nothing is returned when neither it nor PATH has one."""
    exe = "ptxas.exe" if os.name == "nt" else "ptxas"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / exe).write_bytes(b"")
    monkeypatch.setenv("CUDA_PATH", str(tmp_path))
    assert Path(str(find_ptxas())).parent == tmp_path / "bin"
    monkeypatch.delenv("CUDA_PATH")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert find_ptxas() is None


def test_an_nvrtc_warning_of_a_successful_build_reaches_the_report(tmp_path):
    """A kernel with an unused variable builds, and its NVRTC warning is in the report (the log of a successful build used to be dropped)."""
    ptxas = _toolchain()
    src = 'extern "C" __global__ void k(float* o) { int unused = 3; o[threadIdx.x] = 1.0f; }'
    _ptx, report = build_ptx_and_report(src, (), 75, ptxas, tmp_path)
    assert any(line.startswith("nvrtc:") and "unused" in line and "warning" in line.lower() for line in report.splitlines()), report[:400]


def test_a_variant_that_names_its_own_standard_is_built_with_that_one_only(tmp_path):
    """The harness adds ``-std=c++14`` only when the options carry no ``-std=``; a second standard would draw an NVRTC warning of its own."""
    ptxas = _toolchain()
    src = 'extern "C" __global__ void k(float* o) { o[threadIdx.x] = 1.0f; }'
    _ptx, report = build_ptx_and_report(src, ("-std=c++11",), 75, ptxas, tmp_path)
    assert "followed by" not in report


def test_a_failed_build_names_the_nvrtc_errors(tmp_path):
    """A kernel with an undefined identifier fails with the NVRTC error text in the exception, not a bare status."""
    ptxas = _toolchain()
    with pytest.raises(RuntimeError, match=r"NVRTC failed on sm_75"):
        build_ptx_and_report('extern "C" __global__ void k(float* o) { o[0] = MISSING_MACRO; }', (), 75, ptxas, tmp_path)


def test_a_real_kernel_reports_its_registers_and_no_spill(tmp_path):
    """A small kernel builds, has one entry with a positive register count, and does not spill."""
    ptxas = _toolchain()
    src = 'extern "C" __global__ void k(float* o, const float* x) { o[threadIdx.x] = x[threadIdx.x] * 2.0f; }'
    ptx, report = build_ptx_and_report(src, (), 61, ptxas, tmp_path)
    rows = entry_reports("t[k]", 61, ptx, report)
    assert [r.entry for r in rows] == ["k"] and rows[0].registers > 0 and rows[0].spill == 0
