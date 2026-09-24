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
