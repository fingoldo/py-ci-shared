"""Count device allocations under a GPU opt-out, and check the opt-out variable is honoured.

A global GPU opt-out (``MYPKG_DISABLE_GPU=1`` or ``CUDA_VISIBLE_DEVICES=""``) fails silently when broken: cupy's own detection ignores both, so a dispatch gated only on cupy PRESENCE still routes to the
device, nothing raises, and the only symptom is wall-clock (in mlframe a weak GPU spent about 37 percent of a 300k fit in cupy argsort). A structural gate does not work (most CUDA modules are leaf kernels
whose caller owns the decision); the observable contract does: run a workload under the opt-out and require zero device allocations. Origin: mlframe
``test_gpu_optout_makes_no_device_allocations.py`` (audit 2026-11-16, finding 55-T4).

* :func:`count_device_allocations` - a context manager yielding a list that receives every device allocation made inside the block: cupy's allocator (``cupy.cuda.set_allocator``, which every
  ``cp.*`` entry point funnels through) and numba's ``to_device`` / ``device_array`` / ``device_array_like`` / ``pinned_array`` (which bypass cupy). The hooks are removed on exit, even on error.
  *probe_frames* names functions whose allocations are capability probes (a one-time "is cupy broken" check), not dispatch; they are recognised by walking ``sys._getframe`` chains. Do NOT use
  ``traceback.extract_stack`` here: it reads the source of every frame on every allocation and turned the original test into a multi-hour hang.
* :func:`assert_no_device_allocations` - runs ``fn()`` inside the counter and raises ``AssertionError`` with *advice* when anything was allocated. Raises :class:`NoDeviceBackendError` when neither cupy nor
  numba.cuda is importable (the consumer skips: nothing could allocate anyway).
* :func:`assert_optout_env_honoured` - the cheap companion: *is_disabled* (the consumer's policy function) is true for ``<flag>=1``, true for ``CUDA_VISIBLE_DEVICES=""``, false for ``"0"`` and false
  with both unset, so a regression that honours only the explicit flag cannot pass. Restores the environment afterwards.

Usage in a consumer's test::

    from py_ci_shared.cuda_optout_allocations import NoDeviceBackendError, assert_no_device_allocations, assert_optout_env_honoured

    def test_optout_allocates_nothing(monkeypatch):
        monkeypatch.setenv("MYPKG_DISABLE_GPU", "1")
        try:
            assert_no_device_allocations(lambda: fit(X, y), probe_frames=("_ensure_gpu_runtime_configured",), advice="gate the path on gpu_globally_disabled()")
        except NoDeviceBackendError as exc:
            pytest.skip(str(exc))

    def test_cuda_visible_devices_empty_is_an_optout():
        assert_optout_env_honoured(gpu_globally_disabled, flag_var="MYPKG_DISABLE_GPU")
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Optional

__all__ = ["NoDeviceBackendError", "assert_no_device_allocations", "assert_optout_env_honoured", "count_device_allocations"]

_NUMBA_CONSTRUCTORS = ("to_device", "device_array", "device_array_like", "pinned_array")


class NoDeviceBackendError(RuntimeError):
    """Neither cupy nor numba.cuda is importable: no allocation could be observed."""


def _in_probe(probe_frames: "frozenset[str]") -> bool:
    frame: Any = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name in probe_frames:
            return True
        frame = frame.f_back
    return False


def _hook_cupy(calls: "list[int]", probes: "frozenset[str]") -> "Optional[Callable[[], None]]":
    try:
        import cupy as cp
    except Exception:  # best-effort: without cupy there is no cupy allocator to count; the numba hook and the frame-walk probe allowlist still run
        return None
    previous = cp.cuda.get_allocator()

    def counting(size: int) -> Any:
        if not _in_probe(probes):
            calls.append(int(size))
        return previous(size)

    cp.cuda.set_allocator(counting)
    return lambda: cp.cuda.set_allocator(previous)


def _wrap_numba(orig: "Callable[..., Any]", calls: "list[int]", probes: "frozenset[str]") -> "Callable[..., Any]":
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not _in_probe(probes):
            calls.append(-1)
        return orig(*args, **kwargs)

    return wrapped


def _hook_numba(calls: "list[int]", probes: "frozenset[str]") -> "list[Callable[[], None]]":
    try:
        from numba import cuda as nbcuda
    except Exception:  # best-effort: without numba.cuda there are no device-array constructors to wrap; the cupy hook still runs
        return []
    undo: "list[Callable[[], None]]" = []
    for name in _NUMBA_CONSTRUCTORS:
        original = getattr(nbcuda, name, None)
        if original is not None:
            setattr(nbcuda, name, _wrap_numba(original, calls, probes))
            undo.append(lambda n=name, o=original: setattr(nbcuda, n, o))  # type: ignore[misc]
    return undo


@contextmanager
def count_device_allocations(probe_frames: "Iterable[str]" = ()) -> "Iterator[list[int]]":
    """Yield a list that receives the size of every cupy device allocation (``-1`` for a numba device array) made inside the block, except those under a function named in *probe_frames*."""
    probes = frozenset(probe_frames)
    calls: "list[int]" = []
    undo = _hook_numba(calls, probes)
    restore_cupy = _hook_cupy(calls, probes)
    if restore_cupy is not None:
        undo.append(restore_cupy)
    if not undo:
        raise NoDeviceBackendError("neither cupy nor numba.cuda is importable - nothing could allocate on a device anyway")
    try:
        yield calls
    finally:
        for restore in reversed(undo):
            restore()


def assert_no_device_allocations(fn: "Callable[[], Any]", *, probe_frames: "Iterable[str]" = (), advice: str = "") -> Any:
    """Run ``fn()`` and fail when it made any device allocation; returns ``fn``'s result. *advice* is appended to the message (name the policy function the path must consult)."""
    with count_device_allocations(probe_frames) as calls:
        result = fn()
    if calls:
        raise AssertionError(
            f"{len(calls)} device allocation(s) ({sum(c for c in calls if c > 0)} cupy bytes) under the GPU opt-out. The opt-out is a hard contract, not a preference: some dispatch path is gated on "
            f"cupy/numba PRESENCE. {advice}".rstrip()
        )
    return result


def _require(condition: bool, message: str) -> None:
    """An explicit raise, so the check survives ``python -O`` (a bare ``assert`` is deleted there)."""
    if not condition:
        raise AssertionError(message)


def assert_optout_env_honoured(is_disabled: "Callable[[], bool]", *, flag_var: str, flag_value: str = "1") -> None:
    """Check *is_disabled* against the opt-out conventions, restoring ``os.environ`` afterwards (*flag_var* is the project's explicit switch)."""
    saved = {k: os.environ.get(k) for k in (flag_var, "CUDA_VISIBLE_DEVICES")}

    def state(flag: "str | None", visible: "str | None") -> bool:
        for key, value in ((flag_var, flag), ("CUDA_VISIBLE_DEVICES", visible)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return bool(is_disabled())

    try:
        _require(state(flag_value, None), f"{flag_var}={flag_value} is not read as an opt-out")
        _require(state(None, ""), 'CUDA_VISIBLE_DEVICES="" alone must disable the GPU (the documented convention)')
        _require(not state(None, "0"), "a real device list must NOT be read as an opt-out")
        _require(not state(None, None), "an unset variable is 'no preference', not an opt-out")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
