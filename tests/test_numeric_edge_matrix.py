"""Tests for py_ci_shared.numeric_edge_matrix."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from py_ci_shared.numeric_edge_matrix import (
    EDGE_CASE_NAMES,
    assert_edge_contract,
    assert_population_covered,
    edge_cases,
    public_functions,
)


def mean_abs_error(y, p):
    """Well behaved: NaN on empty input, NaN scores propagate."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if y.size == 0:
        return float("nan")
    return float(np.abs(y - p).mean())


def sum_over_len(y, p):
    """ZeroDivision on empty."""
    return sum(abs(a - b) for a, b in zip(y.tolist(), p.tolist())) / len(y)


def brier_zero_on_empty(y, p):
    """A perfect-looking score on no data."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    return float(((y - p) ** 2).sum() / max(y.size, 1))


def raw_mean(y, p):
    """numpy mean of an empty array warns (RuntimeWarning) and returns NaN."""
    return float(np.mean(np.asarray(p, dtype=float)))


def test_the_matrix_has_every_named_case_with_equal_lengths():
    cases = edge_cases()
    assert [c.name for c in cases] == list(EDGE_CASE_NAMES)
    for c in cases:
        assert len(c.y_true) == len(c.y_pred) == len(c.weight), c.name
    assert len(cases[0].y_true) == 0 and len(cases[1].y_true) == 1
    assert set(np.unique(cases[3].y_true)) == {1}
    assert np.isnan(cases[4].y_pred).all() and np.isinf(cases[6].y_pred).any()
    assert cases[8].y_pred.dtype == np.float32 and float(np.abs(cases[8].y_pred).max()) > 3e38


def test_an_unknown_case_name_is_refused():
    with pytest.raises(ValueError, match="unknown edge case"):
        edge_cases(["nope"])


def test_a_well_behaved_metric_passes():
    assert_edge_contract(mean_abs_error, expect={"inf": "any", "float32_extreme": "any"})


def test_zero_division_on_empty_fails_and_names_the_case():
    with pytest.raises(AssertionError, match=r"empty: raised ZeroDivisionError"):
        assert_edge_contract(sum_over_len, cases=["empty", "one_row"])


def test_a_finite_score_on_no_data_fails_the_empty_contract():
    with pytest.raises(AssertionError, match=r"empty: returned 0\.0, wanted NaN"):
        assert_edge_contract(brier_zero_on_empty, cases=["empty"])


def test_the_contract_can_be_overridden_to_accept_zero():
    assert_edge_contract(brier_zero_on_empty, cases=["empty"], expect={"empty": lambda r: r == 0.0})


def test_a_numpy_runtime_warning_is_a_failure():
    with pytest.raises(AssertionError, match="empty: raised RuntimeWarning"):
        assert_edge_contract(raw_mean, cases=["empty"])


def test_valueerror_is_an_accepted_refusal_but_other_errors_are_not():
    def refuses(y, p):
        raise ValueError("no data")

    def explodes(y, p):
        raise KeyError("boom")

    assert_edge_contract(refuses, cases=["empty"])
    with pytest.raises(AssertionError, match="KeyError"):
        assert_edge_contract(explodes, cases=["empty"])
    assert_edge_contract(refuses, cases=["empty"], expect={"empty": "raise:ValueError"})
    with pytest.raises(AssertionError, match="wanted raise:ValueError"):
        assert_edge_contract(mean_abs_error, cases=["empty"], expect={"empty": "raise:ValueError"})


def test_twin_agreement_is_checked_for_values_nan_pattern_and_raising():
    assert_edge_contract(mean_abs_error, twin=mean_abs_error, expect={"inf": "any", "float32_extreme": "any"})

    def off_by_a_hair(y, p):
        return mean_abs_error(y, p) * 1.1

    with pytest.raises(AssertionError, match="twin disagrees: values differ"):
        assert_edge_contract(mean_abs_error, twin=off_by_a_hair, cases=["one_row", "ties_heavy"])

    def nan_always(y, p):
        return float("nan")

    with pytest.raises(AssertionError, match="NaN patterns differ"):
        assert_edge_contract(mean_abs_error, twin=nan_always, cases=["constant"])

    with pytest.raises(AssertionError, match="one raised and one returned"):
        assert_edge_contract(mean_abs_error, twin=sum_over_len, cases=["empty"])


def test_the_tolerance_is_honoured():
    def slightly_off(y, p):
        return mean_abs_error(y, p) + 1e-7

    assert_edge_contract(mean_abs_error, twin=slightly_off, cases=["constant"], atol=1e-6, rtol=0.0)
    with pytest.raises(AssertionError, match="twin disagrees"):
        assert_edge_contract(mean_abs_error, twin=slightly_off, cases=["constant"], atol=1e-9, rtol=0.0)


def test_make_args_feeds_extra_arguments():
    seen = []

    def with_weight(y, p, w):
        seen.append(len(w))
        return float("nan") if len(y) == 0 else 1.0

    assert_edge_contract(with_weight, make_args=lambda c: (c.y_true, c.y_pred, c.weight), cases=["empty", "one_row"])
    assert seen == [0, 1]


def test_population_must_be_covered_or_skipped_with_a_reason():
    assert_population_covered(["a", "b"], ["a"], {"b": "returns a plot object, no numbers"})
    with pytest.raises(AssertionError, match=r"neither covered nor skipped: \['b'\]"):
        assert_population_covered(["a", "b"], ["a"])
    with pytest.raises(AssertionError, match="skip reason too short"):
        assert_population_covered(["a", "b"], ["a"], {"b": "n/a"})
    with pytest.raises(AssertionError, match=r"not public.*\['gone'\]"):
        assert_population_covered(["a"], ["a", "gone"])


def test_public_functions_lists_only_this_modules_own_public_functions():
    import types

    mod = types.ModuleType("fake")
    exec("def pub(): pass\ndef _priv(): pass\n", mod.__dict__)
    assert public_functions(mod) == ["pub"]
