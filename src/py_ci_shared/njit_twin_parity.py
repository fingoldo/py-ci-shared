"""Run a numba kernel and its pure-Python twin on the same seeded inputs and require the same result, bit for bit.

mlframe 1b56cfb90: the pure-Python frac-diff fallback divided by ``weights[0]`` at every step, the njit kernel multiplied by ``1.0 / weights[0]``; the two rounded differently, 205 of 400 elements differed and
the error compounded through the recurrence. Machines without numba (and the ``NUMBA_DISABLE_JIT`` coverage job) silently computed other numbers than production. mlframe 1822e5a0d is the same family
(a GPU stage accumulating in double to match the njit twin). This module is the comparison a consumer's test calls instead of hand-writing it per kernel; the static inventory of where it is owed is
:mod:`py_ci_shared.njit_twin_untested`.

``assert_njit_twin_parity(kernel, inputs_factory)`` calls ``kernel`` (the compiled dispatcher) and ``kernel.py_func`` (the interpreter run of the same source) on identical, separately copied arguments
for *cases* seeds and compares: the return value (nested tuples, lists, dicts, scalars, arrays) AND every array argument after the call (kernels that write into an output buffer). A hand-written
fallback is compared the same way with ``twin=``, and a dispatcher that picks its path from a flag with ``twin=`` plus ``setup=``/``twin_setup=`` context managers (or just call the helper twice with ``twin=``).

Default equality is bitwise: equal dtype and shape, and for floats equal bit patterns, with every NaN equal to every NaN (payloads are not compared) and ``-0.0`` different from ``0.0``. Pass ``rtol``/``atol``
(``numpy.isclose`` semantics, ``equal_nan=True``) only where the twin is documented to differ in order of summation; the failure message then still says how far apart the values are.

Failure message: the case index and seed, the path to the differing element in the result (``result[1]`` or ``arg[2]`` for a mutated argument), how many elements differ, the first differing index with both values
and their hex bit patterns, and the largest absolute difference.

Skips (``pytest.skip``) when *twin* is not given and numba is not importable, or when the kernel has no ``py_func`` and no *twin*: ``NUMBA_DISABLE_JIT=1`` makes the kernel its own twin, which proves nothing. It is
never a silent pass. numpy is imported lazily; py-ci-shared does not depend on it. A fake with a ``py_func`` attribute works on a runner without numba.

Usage in a consumer's test::

    from py_ci_shared.njit_twin_parity import assert_njit_twin_parity

    def test_frac_diff_kernel_matches_its_python_twin():
        def inputs(rng):
            return rng.normal(size=400), 3, np.array([0.4, -0.2, 0.1, -0.05]), rng.normal(size=3)

        assert_njit_twin_parity(_frac_diff_inverse_kernel_prefix, inputs, cases=5)
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from typing import Any, Optional

__all__ = ["TwinParityError", "assert_njit_twin_parity", "diff_results"]


class TwinParityError(AssertionError):
    """The kernel and its twin disagree; the message names the case, the element and both values."""


def _skip(reason: str) -> None:
    import pytest

    pytest.skip(reason)


def _resolve_twin(kernel: Callable[..., Any], twin: Optional[Callable[..., Any]]) -> Optional[Callable[..., Any]]:
    """The callable to compare against, or None after a skip."""
    if twin is not None:
        return twin
    py_func = getattr(kernel, "py_func", None)
    if py_func is not None:
        return py_func  # type: ignore[no-any-return]
    try:
        import numba  # noqa: F401
    except ImportError:
        _skip("numba is not installed: no compiled kernel to compare with its pure-Python twin")
    else:
        _skip("the kernel has no py_func (NUMBA_DISABLE_JIT=1 or not a numba function): pass twin= explicitly")
    return None


def _bits(array: Any) -> Any:
    """A float array as unsigned integers of the same width, every NaN replaced by one canonical NaN (payloads and signs of NaN are not compared)."""
    import numpy as np

    arr = np.ascontiguousarray(array)
    arr = np.where(np.isnan(arr), np.array(np.nan, dtype=arr.dtype), arr)
    return arr.view(np.dtype(f"u{arr.dtype.itemsize}"))


def _differs(a: Any, b: Any, rtol: float, atol: float) -> Any:
    """Boolean array, True where *a* and *b* (same shape) differ under the chosen equality."""
    import numpy as np

    if rtol or atol:
        if a.dtype.kind in "fc":
            return ~np.isclose(a, b, rtol=rtol, atol=atol, equal_nan=True)
        return a != b
    if a.dtype.kind == "f":
        return _bits(a) != _bits(b)
    if a.dtype.kind == "c":
        return (_bits(a.real) != _bits(b.real)) | (_bits(a.imag) != _bits(b.imag))
    return a != b


def _describe(a: Any, b: Any, mask: Any, path: str) -> str:
    import numpy as np

    count = int(mask.sum())
    first = np.unravel_index(int(np.argmax(mask.reshape(-1))), mask.shape)
    va, vb = a[first].item(), b[first].item()
    text = f"{path}: {count} of {mask.size} element(s) differ; first at index {tuple(int(i) for i in first)}: kernel={va!r} twin={vb!r}"
    if a.dtype.kind == "f":
        text += f" (bits {np.float64(va).view(np.uint64):#018x} vs {np.float64(vb).view(np.uint64):#018x} as float64)"
    if a.dtype.kind in "fiu":
        with np.errstate(all="ignore"):
            gap = np.abs(a.astype(np.float64) - b.astype(np.float64))[mask]
        finite = gap[np.isfinite(gap)]
        if finite.size:
            text += f"; largest absolute difference {float(finite.max())!r}"
    return text


def _compare_arrays(a: Any, b: Any, rtol: float, atol: float, path: str) -> Iterator[str]:
    if a.dtype != b.dtype:
        yield f"{path}: dtype differs, kernel={a.dtype} twin={b.dtype}"
    elif a.shape != b.shape:
        yield f"{path}: shape differs, kernel={a.shape} twin={b.shape}"
    else:
        mask = _differs(a, b, rtol, atol)
        if mask.any():
            yield _describe(a, b, mask, path)


def _is_array(x: Any) -> bool:
    return hasattr(x, "dtype") and hasattr(x, "shape") and not isinstance(x, (str, bytes))


def _compare_sequences(a: Any, b: Any, rtol: float, atol: float, path: str) -> Iterator[str]:
    if len(a) != len(b):
        yield f"{path}: length differs, kernel={len(a)} twin={len(b)}"
        return
    for i, (x, y) in enumerate(zip(a, b)):
        yield from _compare(x, y, rtol, atol, f"{path}[{i}]")


def _compare_mappings(a: Mapping[Any, Any], b: Mapping[Any, Any], rtol: float, atol: float, path: str) -> Iterator[str]:
    if set(a) != set(b):
        yield f"{path}: keys differ, kernel={sorted(map(repr, a))} twin={sorted(map(repr, b))}"
        return
    for key in a:
        yield from _compare(a[key], b[key], rtol, atol, f"{path}[{key!r}]")


def _compare_scalars(a: Any, b: Any, rtol: float, atol: float, path: str) -> Iterator[str]:
    import numpy as np

    if not (_is_scalar_number(a) and _is_scalar_number(b)):
        if type(a) is not type(b):
            yield f"{path}: type differs, kernel={type(a).__name__} twin={type(b).__name__}"
        elif a != b:
            yield f"{path}: kernel={a!r} twin={b!r}"
        return
    x, y = np.asarray(a), np.asarray(b)
    if x.dtype != y.dtype:
        yield f"{path}: dtype differs, kernel={x.dtype} twin={y.dtype}"
    elif _differs(np.atleast_1d(x), np.atleast_1d(y), rtol, atol).any():
        yield f"{path}: kernel={a!r} twin={b!r}"


def _is_scalar_number(x: Any) -> bool:
    import numbers

    return isinstance(x, (numbers.Number,)) or (hasattr(x, "dtype") and getattr(x, "shape", None) == ())


def _compare(a: Any, b: Any, rtol: float, atol: float, path: str) -> Iterator[str]:
    if _is_array(a) and _is_array(b) and getattr(a, "shape", None) != ():
        yield from _compare_arrays(a, b, rtol, atol, path)
    elif isinstance(a, Mapping) and isinstance(b, Mapping):
        yield from _compare_mappings(a, b, rtol, atol, path)
    elif isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        yield from _compare_sequences(a, b, rtol, atol, path)
    else:
        yield from _compare_scalars(a, b, rtol, atol, path)


def diff_results(kernel_result: Any, twin_result: Any, *, rtol: float = 0.0, atol: float = 0.0, path: str = "result") -> "list[str]":
    """One line per difference between two results (empty when they agree); bitwise unless *rtol*/*atol* is given."""
    return list(_compare(kernel_result, twin_result, rtol, atol, path))


def _arrays_of(args: Sequence[Any]) -> "list[tuple[int, Any]]":
    return [(i, a) for i, a in enumerate(args) if _is_array(a) and getattr(a, "shape", ()) != ()]


def _cases(inputs_factory: Any, cases: int, seed: int) -> "Iterator[tuple[int, Any, Any]]":
    """``(case index, seed, args tuple)``: a callable factory is called with ``numpy.random.default_rng(seed + i)``; an iterable of tuples is used as given."""
    if callable(inputs_factory):
        import numpy as np

        for i in range(cases):
            yield i, seed + i, inputs_factory(np.random.default_rng(seed + i))
    else:
        for i, args in enumerate(inputs_factory):
            yield i, None, args


def _as_tuple(args: Any) -> "tuple[Any, ...]":
    return tuple(args) if isinstance(args, (tuple, list)) else (args,)


def _enter(contexts: "Sequence[AbstractContextManager[Any]]") -> ExitStack:
    stack = ExitStack()
    for ctx in contexts:
        stack.enter_context(ctx)
    return stack


def _run_one(fn: Callable[..., Any], args: "tuple[Any, ...]", contexts: "Sequence[AbstractContextManager[Any]]") -> "tuple[Any, tuple[Any, ...]]":
    own = copy.deepcopy(args)
    with _enter(contexts):
        result = fn(*own)
    return result, own


def assert_njit_twin_parity(
    kernel: Callable[..., Any],
    inputs_factory: Any,
    *,
    twin: Optional[Callable[..., Any]] = None,
    cases: int = 5,
    seed: int = 0,
    rtol: float = 0.0,
    atol: float = 0.0,
    check_mutated_args: bool = True,
    kernel_contexts: "Sequence[AbstractContextManager[Any]]" = (),
    twin_contexts: "Sequence[AbstractContextManager[Any]]" = (),
) -> None:
    """Fail with a precise message when *kernel* and its twin (default ``kernel.py_func``) differ on any seeded case.

    *inputs_factory* is ``rng -> args`` (a tuple, or one argument) called once per case with ``default_rng(seed + case)``, or an iterable of argument tuples. Each side gets its own deep copy, so a kernel
    that writes into an argument is compared on the mutated arrays as well (*check_mutated_args*). *kernel_contexts* / *twin_contexts* are context managers entered around each call (for example
    ``monkeypatch``-style flag toggles for a dispatcher compared against itself with numba off).
    """
    other = _resolve_twin(kernel, twin)
    if other is None:
        return
    seen = 0
    for index, case_seed, raw in _cases(inputs_factory, cases, seed):
        seen += 1
        args = _as_tuple(raw)
        got, got_args = _run_one(kernel, args, kernel_contexts)
        want, want_args = _run_one(other, args, twin_contexts)
        problems = diff_results(got, want, rtol=rtol, atol=atol)
        if check_mutated_args:
            for i, _ in _arrays_of(args):
                problems += diff_results(got_args[i], want_args[i], rtol=rtol, atol=atol, path=f"arg[{i}] (after the call)")
        if problems:
            where = f"case {index}" + (f" (seed {case_seed})" if case_seed is not None else "")
            name = getattr(kernel, "__name__", repr(kernel))
            raise TwinParityError(
                f"{name} and its twin differ at {where}; same arithmetic order is required for a bit-identical result:\n  " + "\n  ".join(problems)
            )
    if seen == 0:
        raise TwinParityError("inputs_factory produced no cases: nothing was compared")
