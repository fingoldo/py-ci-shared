"""Unit tests for the GPU-timing synchronization check.

Real scratch source files, same no-mocking convention as this package's other tests. Nothing here
needs a CUDA device (or even cupy/numba installed): the check is a pure AST scan over source text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
