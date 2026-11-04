"""Tests for py_ci_shared.estimator_invariants."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from py_ci_shared.estimator_invariants import (
    assert_column_order_invariant,
    assert_frame_type_parity,
    assert_n_jobs_invariant,
)


class ByPosition:
    """Predicts from the first two columns by POSITION: shifts under reordering."""

    def fit(self, X, y):
        return self

    def predict(self, X):
        v = np.asarray(X.values if hasattr(X, "values") else X, dtype=float)
        return v[:, 0] * 2 + v[:, 1]


class ByName(ByPosition):
    def predict(self, X):
        return X["a"].to_numpy(dtype=float) * 2 + X["b"].to_numpy(dtype=float)


class Refuses(ByPosition):
    def predict(self, X):
        if list(X.columns) != ["a", "b", "c"]:
            raise ValueError("columns differ from fit time")
        return super().predict(X)


class WorkerSeeded:
    def __init__(self, n_jobs=1):
        self.n_jobs = n_jobs

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.arange(len(X)) + self.n_jobs


class WorkerInvariant(WorkerSeeded):
    def predict(self, X):
        return np.arange(len(X)) * 1.0


class NanVsNull:
    """Treats NaN as 0 only for pandas input (the polars-style null path keeps it)."""

    def fit(self, X, y):
        return self

    def predict(self, X):
        v = np.asarray(X["a"], dtype=float)
        return np.nan_to_num(v) if hasattr(X, "iloc") else v


FRAME = pd.DataFrame({"a": [1.0, np.nan, 3.0, 4.0], "b": [0.5, 1.5, 2.5, 3.5], "c": [9.0, 8.0, 7.0, 6.0]})
Y = np.array([0, 1, 0, 1])


def test_a_position_indexed_predictor_fails_the_column_order_check():
    with pytest.raises(AssertionError, match=r"ByPosition.predict changes when the columns are reordered"):
        assert_column_order_invariant(ByPosition, FRAME.fillna(0.0), Y)


def test_selecting_by_name_passes():
    assert_column_order_invariant(ByName, FRAME.fillna(0.0), Y)


def test_refusing_with_a_clear_error_is_accepted():
    assert_column_order_invariant(Refuses, FRAME.fillna(0.0), Y)


def test_an_unexpected_exception_type_is_not_swallowed():
    class Boom(ByPosition):
        def predict(self, X):
            if list(X.columns) != ["a", "b", "c"]:
                raise ZeroDivisionError
            return super().predict(X)

    with pytest.raises(ZeroDivisionError):
        assert_column_order_invariant(Boom, FRAME.fillna(0.0), Y)


def test_n_jobs_dependent_output_fails():
    with pytest.raises(AssertionError, match=r"depends on n_jobs \(n_jobs=1 vs 2\)"):
        assert_n_jobs_invariant(WorkerSeeded, FRAME, Y)


def test_n_jobs_invariant_output_passes_and_checks_every_count():
    assert_n_jobs_invariant(WorkerInvariant, FRAME, Y, n_jobs=(1, 2, 4))
    with pytest.raises(AssertionError, match="n_jobs=1 vs 4"):
        assert_n_jobs_invariant(lambda n_jobs: WorkerSeeded(n_jobs if n_jobs == 4 else 1), FRAME, Y, n_jobs=(1, 2, 4))


def test_frame_type_parity_detects_null_versus_nan_handling():
    as_numpy_dict = lambda df: {c: df[c].to_numpy() for c in df.columns}  # noqa: E731

    class DictFrame(dict):
        pass

    with pytest.raises(AssertionError, match="differs between DataFrame and DictFrame"):
        assert_frame_type_parity(NanVsNull, FRAME, Y, lambda df: DictFrame(as_numpy_dict(df)))


def test_frame_type_parity_passes_for_equivalent_handling():
    assert_frame_type_parity(ByName, FRAME.fillna(0.0), Y, lambda df: df.copy())


def test_tolerance_is_honoured():
    class Noisy(ByName):
        def predict(self, X):
            return super().predict(X) + (1e-9 if "c" in X.columns and X.columns[0] == "c" else 0.0)

    assert_column_order_invariant(Noisy, FRAME.fillna(0.0), Y, atol=1e-6)
    with pytest.raises(AssertionError):
        assert_column_order_invariant(Noisy, FRAME.fillna(0.0), Y, atol=1e-12, rtol=0.0)
