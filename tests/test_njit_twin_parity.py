"""Tests for py_ci_shared.njit_twin_parity: fakes run on any runner, the real-numba tests skip cleanly without numba."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from py_ci_shared.njit_twin_parity import TwinParityError, assert_njit_twin_parity, diff_results


class _Fake:
    """A stand-in for a numba dispatcher: callable, with the interpreter version in ``py_func``."""

    def __init__(self, compiled, py_func):
        self._compiled = compiled
        self.py_func = py_func
        self.__name__ = "fake_kernel"

    def __call__(self, *args):
        return self._compiled(*args)


def _inputs(rng):
    return rng.normal(size=50), 0.4


def _div(x, w):
    return x / w


def _mul_reciprocal(x, w):
    return x * (1.0 / w)


def test_identical_twins_pass():
    assert_njit_twin_parity(_Fake(_div, _div), _inputs, cases=3)


def test_the_frac_diff_shape_division_vs_reciprocal_is_reported_with_the_element_and_bits():
    with pytest.raises(TwinParityError) as err:
        assert_njit_twin_parity(_Fake(_mul_reciprocal, _div), _inputs, cases=3)
    text = str(err.value)
    assert "fake_kernel" in text and "case 0 (seed 0)" in text
    assert "result: " in text and "element(s) differ; first at index (" in text
    assert "kernel=" in text and "twin=" in text and "bits 0x" in text and "largest absolute difference" in text


def test_a_tolerance_accepts_the_one_ulp_difference_that_the_default_rejects():
    assert_njit_twin_parity(_Fake(_mul_reciprocal, _div), _inputs, cases=3, rtol=1e-12)
    with pytest.raises(TwinParityError):
        assert_njit_twin_parity(_Fake(_mul_reciprocal, _div), _inputs, cases=3)


def test_nan_equals_nan_but_a_nan_against_a_number_differs():
    def with_nan(x, w):
        out = x.copy()
        out[3] = np.nan
        return out

    def with_zero(x, w):
        out = x.copy()
        out[3] = 0.0
        return out

    assert_njit_twin_parity(_Fake(with_nan, with_nan), _inputs, cases=1)
    with pytest.raises(TwinParityError, match=r"first at index \(3,\)"):
        assert_njit_twin_parity(_Fake(with_nan, with_zero), _inputs, cases=1)


def test_signed_zero_is_a_bitwise_difference_unless_a_tolerance_is_given():
    pos = lambda x, w: np.array([0.0])  # noqa: E731
    neg = lambda x, w: np.array([-0.0])  # noqa: E731
    with pytest.raises(TwinParityError):
        assert_njit_twin_parity(_Fake(pos, neg), _inputs, cases=1)
    assert_njit_twin_parity(_Fake(pos, neg), _inputs, cases=1, atol=0.0, rtol=1e-12)


def test_dtype_and_shape_differences_are_named():
    f32 = lambda x, w: x.astype(np.float32)  # noqa: E731
    f64 = lambda x, w: x  # noqa: E731
    with pytest.raises(TwinParityError, match="dtype differs, kernel=float32 twin=float64"):
        assert_njit_twin_parity(_Fake(f32, f64), _inputs, cases=1)
    short = lambda x, w: x[:-1]  # noqa: E731
    with pytest.raises(TwinParityError, match="shape differs"):
        assert_njit_twin_parity(_Fake(short, f64), _inputs, cases=1)


def test_tuples_are_compared_element_by_element_with_the_path():
    left = lambda x, w: (x, 1)  # noqa: E731
    right = lambda x, w: (x, 2)  # noqa: E731
    with pytest.raises(TwinParityError, match=r"result\[1\]: kernel=1 twin=2"):
        assert_njit_twin_parity(_Fake(left, right), _inputs, cases=1)


def test_an_output_buffer_written_in_place_is_compared_after_the_call():
    def good(out, x):
        out[:] = x * 2.0

    def bad(out, x):
        out[:] = x * 2.0
        out[0] += 1e-9

    def factory(rng):
        return np.zeros(8), rng.normal(size=8)

    assert_njit_twin_parity(_Fake(good, good), factory, cases=2)
    with pytest.raises(TwinParityError, match=r"arg\[0\] \(after the call\)"):
        assert_njit_twin_parity(_Fake(bad, good), factory, cases=2)
    assert_njit_twin_parity(_Fake(bad, good), factory, cases=2, check_mutated_args=False)


def test_the_caller_arguments_are_not_mutated_and_each_side_gets_its_own_copy():
    seen = []

    def kernel(buf):
        seen.append(buf.copy())
        buf[:] = 0.0

    assert_njit_twin_parity(_Fake(kernel, kernel), lambda rng: (np.ones(3),), cases=1)
    assert all(np.array_equal(s, np.ones(3)) for s in seen) and len(seen) == 2


def test_an_iterable_of_argument_tuples_is_used_as_given():
    assert_njit_twin_parity(_Fake(_div, _div), [(np.arange(3.0), 0.4), (np.ones(2), 3.0)])
    with pytest.raises(TwinParityError, match="at case 1;"):
        assert_njit_twin_parity(_Fake(_mul_reciprocal, _div), [(np.ones(1), 2.0), (np.linspace(0.1, 3.0, 40), 0.4)])


def test_an_explicit_twin_is_used_instead_of_py_func():
    assert_njit_twin_parity(_div, _inputs, twin=_div, cases=2)
    with pytest.raises(TwinParityError):
        assert_njit_twin_parity(_mul_reciprocal, _inputs, twin=_div, cases=2)


def test_no_cases_is_a_failure_not_a_pass():
    with pytest.raises(TwinParityError, match="no cases"):
        assert_njit_twin_parity(_Fake(_div, _div), [])


def test_a_plain_function_without_a_twin_skips_instead_of_passing_vacuously():
    with pytest.raises(pytest.skip.Exception):
        assert_njit_twin_parity(_div, _inputs)


def test_context_managers_wrap_each_side():
    import contextlib

    log = []

    @contextlib.contextmanager
    def tag(name):
        log.append(name + ":in")
        yield
        log.append(name + ":out")

    assert_njit_twin_parity(_div, _inputs, twin=_div, cases=1, kernel_contexts=[tag("k")], twin_contexts=[tag("t")])
    assert log == ["k:in", "k:out", "t:in", "t:out"]


def test_diff_results_scalars_and_types():
    assert diff_results(1.5, 1.5) == []
    assert diff_results(np.float64(1.0), 1.0) == []
    assert "dtype differs" in diff_results(np.float32(1.0), 1.0)[0]
    assert diff_results(1.0, 1.0000000000000002) == ["result: kernel=1.0 twin=1.0000000000000002"]
    assert diff_results(float("nan"), float("nan")) == []
    assert diff_results(None, 1)[0].startswith("result: type differs")


numba = pytest.importorskip("numba", reason="numba is not installed: the real-dispatcher tests need it (the fakes above run everywhere)")


def test_a_real_njit_kernel_matches_its_py_func():
    @numba.njit(cache=False)
    def cumulative(x):
        out = np.empty_like(x)
        acc = 0.0
        for i in range(x.size):
            acc += x[i]
            out[i] = acc
        return out

    assert_njit_twin_parity(cumulative, lambda rng: (rng.normal(size=200),), cases=3)


def test_a_real_njit_kernel_against_a_hand_written_twin_that_rounds_differently_is_caught():
    @numba.njit(cache=False)
    def scale(x, w):
        inv = 1.0 / w
        out = np.empty_like(x)
        for i in range(x.size):
            out[i] = x[i] * inv
        return out

    def fallback_divides_each_step(x, w):
        out = np.empty_like(x)
        for i in range(x.size):
            out[i] = x[i] / w
        return out

    def factory(rng):
        return rng.normal(size=400), 0.4

    assert_njit_twin_parity(scale, factory, cases=2)
    with pytest.raises(TwinParityError, match=r"element\(s\) differ"):
        assert_njit_twin_parity(scale, factory, twin=fallback_divides_each_step, cases=2)
