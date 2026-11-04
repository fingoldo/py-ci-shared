"""Tests for py_ci_shared.target_leak_canary."""

from __future__ import annotations


import math

import pytest

np = pytest.importorskip("numpy")

from py_ci_shared.target_leak_canary import assert_no_train_target_leak, assert_oof_scores_at_chance, spearman_columns


class BinTargetMean:
    """Target mean per quantile bin of each column. In-sample (leaky) unless folds > 1, then out of fold."""

    def __init__(self, folds=1, bins=50):
        self.folds, self.bins = folds, bins

    def _encode(self, X_fit, y_fit, X_apply):
        out = np.empty_like(X_apply)
        for j in range(X_fit.shape[1]):
            edges = np.quantile(X_fit[:, j], np.linspace(0, 1, self.bins + 1)[1:-1])
            b_fit, b_apply = np.digitize(X_fit[:, j], edges), np.digitize(X_apply[:, j], edges)
            means = np.array([y_fit[b_fit == k].mean() if (b_fit == k).any() else 0.0 for k in range(self.bins)])
            out[:, j] = means[b_apply]
        return out

    def fit_transform(self, X, y):
        if self.folds <= 1:
            return self._encode(X, y, X)
        idx = np.arange(len(y)) % self.folds
        out = np.empty_like(X)
        for k in range(self.folds):
            out[idx == k] = self._encode(X[idx != k], y[idx != k], X[idx == k])
        return out


class FitThenTransform:
    """No fit_transform: fit then transform, leaky."""

    def fit(self, X, y):
        self.w = np.array([np.corrcoef(X[:, j], y)[0, 1] for j in range(X.shape[1])])
        self.y = y
        return self

    def transform(self, X):
        return np.column_stack([self.y * 1.0, X[:, 0]])


def test_an_in_sample_target_encoder_fails():
    with pytest.raises(AssertionError, match=r"target leak: output column \d correlates \+0\.[1-9]"):
        assert_no_train_target_leak(lambda: BinTargetMean(folds=1))


def test_the_same_encoder_out_of_fold_passes():
    assert_no_train_target_leak(lambda: BinTargetMean(folds=3))


def test_fit_then_transform_builders_are_supported():
    with pytest.raises(AssertionError, match="target leak"):
        assert_no_train_target_leak(FitThenTransform)


def test_a_custom_producer_overrides_the_default_call():
    assert_no_train_target_leak(lambda: BinTargetMean(folds=1), produce=lambda t, X, y: np.random.default_rng(0).standard_normal((len(y), 2)))


def test_the_bound_is_honoured():
    assert_no_train_target_leak(lambda: BinTargetMean(folds=1), max_abs_corr=1.0)
    with pytest.raises(AssertionError):
        assert_no_train_target_leak(lambda: BinTargetMean(folds=3), max_abs_corr=0.0)


def test_nan_cells_are_ignored_and_constant_columns_skipped():
    out = np.column_stack([np.where(np.arange(100) % 2 == 0, np.nan, np.arange(100.0)), np.ones(100)])
    y = np.arange(100.0)
    corr = spearman_columns(out, y)
    assert corr[0] == pytest.approx(1.0) and corr[1] != corr[1]


def test_spearman_averages_tied_ranks():
    assert spearman_columns(np.array([1.0, 1.0, 2.0, 2.0]), np.array([1.0, 1.0, 2.0, 2.0])) == [pytest.approx(1.0)]
    (constant,) = spearman_columns(np.array([1.0, 1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0, 4.0]))
    assert math.isnan(constant)


def test_oof_scores_of_pure_noise_pass_and_in_sample_scores_fail():
    def in_sample(X, y):
        return y + 0.5 * np.random.default_rng(1).standard_normal(len(y))

    def oof(X, y):
        return np.random.default_rng(2).standard_normal(len(y))

    assert_oof_scores_at_chance(oof)
    with pytest.raises(AssertionError, match=r"training AUC 0.9"):
        assert_oof_scores_at_chance(in_sample)
    assert_oof_scores_at_chance(lambda X, y: np.column_stack([np.zeros(len(y)), oof(X, y)]))
