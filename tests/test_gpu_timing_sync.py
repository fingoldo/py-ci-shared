"""Unit tests for the GPU-timing synchronization check.

Real scratch source files, same no-mocking convention as this package's other tests. Nothing here
needs a CUDA device (or even cupy/numba installed): the check is a pure AST scan over source text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.gpu_timing_sync import (
    SUPPRESSION_MARKER,
    assert_no_unsynchronized_gpu_timings,
    find_unsynchronized_gpu_timings,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content.lstrip("\n"), encoding="utf-8")
    return p


def test_direct_cupy_call_without_sync_is_flagged(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "kern.py",
        """
import time
import cupy as cp


def measure(a, b):
    t0 = time.perf_counter()
    cp.matmul(a, b)
    return time.perf_counter() - t0
""",
    )
    findings = find_unsynchronized_gpu_timings([path])
    assert [f.shape for f in findings] == ["direct-gpu-call"]
    assert findings[0].function == "measure"


def test_direct_cupy_call_with_sync_is_clean(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "kern.py",
        """
import time
import cupy as cp


def measure(a, b):
    t0 = time.perf_counter()
    cp.matmul(a, b)
    cp.cuda.runtime.deviceSynchronize()
    return time.perf_counter() - t0
""",
    )
    assert find_unsynchronized_gpu_timings([path]) == []


def test_numba_kernel_launch_without_sync_is_flagged(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "launch.py",
        """
import time
from numba import cuda


def measure(kern, blocks, threads, arr):
    start = time.monotonic()
    kern[blocks, threads](arr)
    return time.monotonic() - start
""",
    )
    findings = find_unsynchronized_gpu_timings([path])
    assert [f.shape for f in findings] == ["direct-gpu-call"]


def test_numba_kernel_launch_with_cuda_synchronize_is_clean(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "launch.py",
        """
import time
from numba import cuda


def measure(kern, blocks, threads, arr):
    start = time.monotonic()
    kern[blocks, threads](arr)
    cuda.synchronize()
    return time.monotonic() - start
""",
    )
    assert find_unsynchronized_gpu_timings([path]) == []


def test_injected_callable_in_gpu_module_is_flagged(tmp_path: Path) -> None:
    """The benchmark-harness shape: the timed callable is supplied by the caller, so no name-based
    rule can see it is a kernel -- yet an unsynchronized GPU-backend timer is exactly the bug."""
    path = _write(
        tmp_path,
        "bench.py",
        """
import time


def time_backend(fn, make_inputs, timer=time.perf_counter):
    \"\"\"Median wall time of a CPU or GPU backend.\"\"\"
    samples = []
    for _ in range(3):
        args = make_inputs()
        t0 = timer()
        fn(*args)
        samples.append(timer() - t0)
    return min(samples)
""",
    )
    findings = find_unsynchronized_gpu_timings([path])
    assert [f.shape for f in findings] == ["injected-callable-in-gpu-module"]
    assert "fn()" in findings[0].detail


def test_injected_callable_in_non_gpu_module_is_ignored(tmp_path: Path) -> None:
    """Same harness shape with nothing GPU-related anywhere in the file: a plain CPU timer is not a
    finding, or the rule would fire on every benchmark helper in every repo."""
    path = _write(
        tmp_path,
        "bench_cpu.py",
        """
import time


def time_it(fn, args, timer=time.perf_counter):
    t0 = timer()
    fn(*args)
    return timer() - t0
""",
    )
    assert find_unsynchronized_gpu_timings([path]) == []


def test_injected_callable_with_project_sync_wrapper_is_clean(tmp_path: Path) -> None:
    """The real fix shape: a project-named sync wrapper (``_gpu_sync()``) inside the timed region
    counts as a synchronization -- the check must not demand a literal ``cuda.synchronize``."""
    path = _write(
        tmp_path,
        "bench.py",
        """
import time


def time_backend(fn, make_inputs, _gpu_sync, timer=time.perf_counter):
    \"\"\"Median wall time of a cuda/gpu backend.\"\"\"
    args = make_inputs()
    t0 = timer()
    fn(*args)
    _gpu_sync()
    return timer() - t0
""",
    )
    assert find_unsynchronized_gpu_timings([path]) == []


def test_nested_helper_sees_outer_function_parameters(tmp_path: Path) -> None:
    """The timed call lives in a nested closure whose own signature does not mention ``fn`` -- the
    scan must carry the enclosing function's parameters in, or it misses the real-world shape."""
    path = _write(
        tmp_path,
        "bench.py",
        """
import time


def time_backend(fn, timer=time.perf_counter):
    \"\"\"Times a gpu backend.\"\"\"

    def _run(inputs, out):
        for args in inputs:
            t0 = timer()
            fn(*args)
            out.append(timer() - t0)

    return _run
""",
    )
    findings = find_unsynchronized_gpu_timings([path])
    assert [(f.function, f.shape) for f in findings] == [("_run", "injected-callable-in-gpu-module")]


def test_timer_parameter_alone_is_not_timed_work(tmp_path: Path) -> None:
    """``now = timer()`` followed by a later ``timer()`` read, with the injected timer as the only
    call, is a clock comparison and not a measurement of anything -- it must not be flagged."""
    path = _write(
        tmp_path,
        "idle.py",
        """
import time


def hardware_busy(cache, timer=time.perf_counter):
    \"\"\"Cached gpu/cpu busy probe.\"\"\"
    now = timer()
    if cache is not None and (timer() - cache[0]) < 5.0:
        return cache[1]
    return False
""",
    )
    assert find_unsynchronized_gpu_timings([path]) == []


def test_suppression_marker_silences_deliberate_launch_measurement(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "launch_latency.py",
        """
import time
import cupy as cp


def measure_launch_overhead(a, b):
    t0 = time.perf_counter()
    cp.matmul(a, b)  # gpu-timing-async-intentional: launch overhead is the quantity under test
    return time.perf_counter() - t0
""",
    )
    assert SUPPRESSION_MARKER in path.read_text(encoding="utf-8")
    assert find_unsynchronized_gpu_timings([path]) == []


def test_relative_paths_and_allowlist_key(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    path = _write(
        pkg,
        "kern.py",
        """
import time
import cupy as cp


def measure(a, b):
    t0 = time.perf_counter()
    cp.matmul(a, b)
    return time.perf_counter() - t0
""",
    )
    (finding,) = find_unsynchronized_gpu_timings([path], root=tmp_path)
    assert finding.key == "pkg/kern.py::measure"


def test_assert_helper_fails_and_respects_allowlist(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "kern.py",
        """
import time
import cupy as cp


def measure(a, b):
    t0 = time.perf_counter()
    cp.matmul(a, b)
    return time.perf_counter() - t0
""",
    )
    with pytest.raises(BaseException, match="without a device synchronize"):
        assert_no_unsynchronized_gpu_timings([path], root=tmp_path)
    assert_no_unsynchronized_gpu_timings([path], root=tmp_path, allowlist=frozenset({"kern.py::measure"}))


def test_unparseable_file_is_skipped(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.py", "def measure(:\n")
    assert find_unsynchronized_gpu_timings([path]) == []


_BODY = """
import time
import cupy as cp
import torch


def measure(a, b):
    t0 = time.perf_counter()
{lines}
    return time.perf_counter() - t0
"""


def _measure(tmp_path: Path, *lines: str) -> list:
    path = _write(tmp_path, f"m{len(list(tmp_path.glob('m*.py')))}.py", _BODY.format(lines="\n".join("    " + line for line in lines)))
    return find_unsynchronized_gpu_timings([path])


def test_a_sync_before_the_gpu_work_does_not_count(tmp_path: Path) -> None:
    assert [f.shape for f in _measure(tmp_path, "cp.cuda.Device().synchronize()", "cp.matmul(a, b)")] == ["direct-gpu-call"]
    assert _measure(tmp_path, "cp.matmul(a, b)", "cp.cuda.Device().synchronize()") == []


def test_a_sync_reached_through_a_call_is_recognised(tmp_path: Path) -> None:
    assert _measure(tmp_path, "cp.matmul(a, b)", "torch.cuda.current_stream().synchronize()") == []


def test_a_file_flush_named_sync_is_not_a_device_sync(tmp_path: Path) -> None:
    assert [f.shape for f in _measure(tmp_path, "cp.matmul(a, b)", "_sync_to_disk(a)")] == ["direct-gpu-call"]
    assert _measure(tmp_path, "cp.matmul(a, b)", "_gpu_sync()") == []


@pytest.mark.parametrize(
    "imports,start,stop",
    [
        ("from time import perf_counter as pc", "pc()", "pc()"),
        ("import timeit", "timeit.default_timer()", "timeit.default_timer()"),
        ("from timeit import default_timer as clock_fn", "clock_fn()", "clock_fn()"),
    ],
)
def test_aliased_and_timeit_timers_are_timers(tmp_path: Path, imports: str, start: str, stop: str) -> None:
    source = f"{imports}\nimport cupy as cp\n\n\ndef measure(a, b):\n    t0 = {start}\n    cp.matmul(a, b)\n    return {stop} - t0\n"
    path = _write(tmp_path, "m.py", source)
    assert [f.shape for f in find_unsynchronized_gpu_timings([path])] == ["direct-gpu-call"]


def test_module_level_benchmark_is_scanned(tmp_path: Path) -> None:
    path = _write(tmp_path, "bench.py", "import time\nimport cupy as cp\n\nt0 = time.perf_counter()\ncp.matmul(a, b)\nprint(time.perf_counter() - t0)\n")
    findings = find_unsynchronized_gpu_timings([path])
    assert [(f.function, f.shape) for f in findings] == [("<module>", "direct-gpu-call")]
    fixed = _write(
        tmp_path,
        "bench_fixed.py",
        "import time\nimport cupy as cp\n\nt0 = time.perf_counter()\ncp.matmul(a, b)\ncp.cuda.Device().synchronize()\nprint(time.perf_counter() - t0)\n",
    )
    assert find_unsynchronized_gpu_timings([fixed]) == []


def test_bom_and_unparsable_files_and_the_floor(tmp_path: Path) -> None:
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + _BODY.format(lines="    cp.matmul(a, b)").lstrip("\n").encode("utf-8"))
    assert [f.shape for f in find_unsynchronized_gpu_timings([bom])] == ["direct-gpu-call"]
    ok = _write(tmp_path, "ok.py", "x = 1\n")
    assert_no_unsynchronized_gpu_timings([ok], root=tmp_path)
    broken = _write(tmp_path, "broken.py", "def measure(:\n")
    with pytest.raises(BaseException, match=r"broken.py"):
        assert_no_unsynchronized_gpu_timings([ok, broken], root=tmp_path)
    with pytest.raises(BaseException, match="parsed"):
        assert_no_unsynchronized_gpu_timings([], root=tmp_path)


def test_a_blocking_device_to_host_copy_after_a_synchronize_ends_the_region(tmp_path):
    src = (
        "import time\nimport cupy as cp\n\n\ndef bench(d_X, d_W):\n    t0 = time.perf_counter()\n    d_R = d_X @ d_W\n"
        "    cp.cuda.runtime.deviceSynchronize()\n    _ = cp.asnumpy(d_R)\n    return time.perf_counter() - t0\n"
    )
    (tmp_path / "b.py").write_text(src, encoding="utf-8")
    assert find_unsynchronized_gpu_timings([tmp_path / "b.py"]) == []


def test_a_kernel_after_the_last_blocking_copy_is_still_reported(tmp_path):
    src = (
        "import time\nimport cupy as cp\n\n\ndef bench(d_X):\n    t0 = time.perf_counter()\n    _ = cp.asnumpy(d_X)\n"
        "    cp.matmul(d_X, d_X)\n    return time.perf_counter() - t0\n"
    )
    (tmp_path / "b.py").write_text(src, encoding="utf-8")
    assert [f.detail.split("(")[0] for f in find_unsynchronized_gpu_timings([tmp_path / "b.py"])] == ["times cp.matmul"]


def test_a_host_copy_method_ends_the_timed_region_like_a_sync(tmp_path: Path) -> None:
    assert _measure(tmp_path, "r = cp.matmul(a, b)", "r.cpu()") == []
    assert _measure(tmp_path, "r = cp.matmul(a, b)", "r.item()") == []
    assert [f.shape for f in _measure(tmp_path, "r = cp.matmul(a, b)", "r.shape")] == ["direct-gpu-call"]


# --- round 2026-11-16 (audit 53-T2, 53-T3, 53-T9) -------------------------------------------------------------------------------------------------


def _shapes(tmp_path: Path, body: str) -> list:
    return [f.shape for f in find_unsynchronized_gpu_timings([_write(tmp_path, "m.py", body)])]


_TIMED = """
import time
import cupy as cp
{prelude}

def measure(x, n):
    t0 = time.perf_counter()
{work}
    return time.perf_counter() - t0
"""


def _timed(tmp_path: Path, prelude: str, work: str) -> list:
    return _shapes(tmp_path, _TIMED.format(prelude=prelude, work="\n".join("    " + line for line in work.splitlines())))


_RAW = 'K = cp.RawKernel("src", "k")'


def test_launch_through_a_rawkernel_variable_is_flagged(tmp_path: Path) -> None:
    """53-T2: `K = cp.RawKernel(...)`, then `K((n,), (256,), (x, n))` between two timer reads is device work."""
    assert _timed(tmp_path, _RAW, "K((n,), (256,), (x, n))") == ["direct-gpu-call"]


def test_launch_through_an_imported_kernel_is_flagged(tmp_path: Path) -> None:
    """53-T2: the `(grid_tuple, block_tuple, args)` call shape is a launch whatever the callee is called."""
    assert _timed(tmp_path, "from mykernels import kern", "kern((n,), (256,), (x, n))") == ["direct-gpu-call"]


def test_loose_launch_with_grid_in_a_name_is_flagged_for_a_known_kernel(tmp_path: Path) -> None:
    """53-T2: `K(grid, (256,), args)` where K is bound to a RawKernel."""
    assert _timed(tmp_path, _RAW, "grid = (n,)\nK(grid, (256,), (x, n))") == ["direct-gpu-call"]


def test_call_of_a_kernel_object_built_from_get_function_is_flagged(tmp_path: Path) -> None:
    """53-T2: `f = module.get_function("k")` is a kernel object, and so are ElementwiseKernel/ReductionKernel values."""
    prelude = 'mod = cp.RawModule(code="src")\nk1 = mod.get_function("k")\newise = cp.ElementwiseKernel("T x", "T y", "y = x", "e")'
    assert _timed(tmp_path, prelude, "k1((n,), (256,), (x, n))") == ["direct-gpu-call"]
    assert _timed(tmp_path, prelude, "ewise(x)") == ["direct-gpu-call"]


def test_launch_through_get_function_called_inline_is_flagged(tmp_path: Path) -> None:
    """53-T2: the form mlframe writes, `module.get_function("k")((g,), (b,), args)`."""
    assert _timed(tmp_path, "mod = cp.RawModule(code='s')", 'mod.get_function("k")((n,), (256,), (x, n))') == ["direct-gpu-call"]


def test_kernel_launch_followed_by_a_device_sync_is_clean(tmp_path: Path) -> None:
    assert _timed(tmp_path, _RAW, "K((n,), (256,), (x, n))\ncp.cuda.Device().synchronize()") == []


def test_an_ordinary_call_is_not_gpu_work(tmp_path: Path) -> None:
    assert _timed(tmp_path, "", "helper(x, n)") == []


def test_dict_dispatch_between_timer_reads_is_not_gpu_work(tmp_path: Path) -> None:
    """53-T3: `d[k](x)` is a dict of callables, not a numba launch (that has `[blocks, threads]`)."""
    assert _timed(tmp_path, "d = {}", "d[n](x)") == []


def test_numba_launch_subscript_is_still_gpu_work(tmp_path: Path) -> None:
    assert _timed(tmp_path, "", "kern[n, 256](x)") == ["direct-gpu-call"]


def test_a_synchronize_with_a_non_device_receiver_does_not_end_the_region(tmp_path: Path) -> None:
    """53-T3: `log.synchronize_with_remote()` is not a device barrier."""
    assert _timed(tmp_path, "", "cp.matmul(x, x)\nlog.synchronize_with_remote()") == ["direct-gpu-call"]


def test_device_flavoured_sync_names_still_end_the_region(tmp_path: Path) -> None:
    for call in ("ev.synchronize()", "stream.synchronize()", "synchronize_gpu_if_available()", "self._gpu_synchronize()", "cp.cuda.runtime.deviceSynchronize()"):
        assert _timed(tmp_path, "", f"cp.matmul(x, x)\n{call}") == [], call


def test_marker_without_a_reason_does_not_suppress(tmp_path: Path) -> None:
    """53-T9: the marker is the review; a bare marker is not."""
    assert _timed(tmp_path, "", "cp.matmul(x, x)  # gpu-timing-async-intentional") == ["direct-gpu-call"]
    assert _timed(tmp_path, "", "cp.matmul(x, x)  # gpu-timing-async-intentional:") == ["direct-gpu-call"]
    assert _timed(tmp_path, "", "cp.matmul(x, x)  # gpu-timing-async-intentional: measuring launch latency") == []
