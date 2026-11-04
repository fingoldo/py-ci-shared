"""A feature builder that fits on the training rows and returns features for those same rows leaks the target.

An encoder, stacker or "nearest neighbours of the target" feature computed in-sample gives the downstream model a column
that carries the training target and does not exist at predict time: validation looks excellent, production collapses.
mlframe fixed this by hand in at least eight places (d8f250e39, 50ba408eb, 6e918312f, daad55e79, 20535fd30, a463fcf3f,
f6522a8e2, 589478c49). A static scan cannot see it (31 hits on mlframe, nearly all legitimate ``fit_transform``), but the
leak is a statistical property, so test it dynamically.

:func:`assert_no_train_target_leak` draws ``X`` and a target ``y`` that are independent by construction, runs the feature
builder, and fails when any output column correlates with ``y`` on the training rows beyond ``max_abs_corr`` (Spearman,
NaN ignored). An out-of-fold-safe feature has expectation 0; an in-sample one lands at +-0.3..0.9.
:func:`assert_oof_scores_at_chance` is the stacker variant: out-of-fold scores for noise labels must give an AUC of
0.5 within ``tol`` on the training rows.

Usage in a consumer's meta test (numpy is imported lazily; py-ci-shared does not depend on it)::

    from py_ci_shared.target_leak_canary import assert_no_train_target_leak

    def test_target_encoder_is_out_of_fold():
        assert_no_train_target_leak(lambda: MyTargetEncoder(), n=2000, d=6)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

__all__ = ["assert_no_train_target_leak", "assert_oof_scores_at_chance", "spearman_columns"]


def _rank(values: Any) -> Any:
    """Average ranks (ties share the mean rank), so a binned or constant-block output is not mistaken for a leak or a pass."""
    import numpy as np

    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    mid = ends - (counts - 1) / 2.0
    return mid[inverse.reshape(-1)]


def spearman_columns(out: Any, y: Any) -> list[float]:
    """Spearman correlation of every column of *out* with *y*, NaN pairs dropped; ``nan`` for a column with no variation."""
    import numpy as np

    arr = np.asarray(out, dtype=float)
    if arr.ndim == 1:
        arr = arr[:, None]
    target = np.asarray(y, dtype=float)
    result: list[float] = []
    for j in range(arr.shape[1]):
        keep = ~(np.isnan(arr[:, j]) | np.isnan(target))
        if keep.sum() < 3:
            result.append(float("nan"))
            continue
        a, b = _rank(arr[keep, j]), _rank(target[keep])
        a, b = a - a.mean(), b - b.mean()
        denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
        result.append(float((a * b).sum() / denom) if denom > 0 else float("nan"))
    return result


def _default_fit_transform(transformer: Any, X: Any, y: Any) -> Any:
    if hasattr(transformer, "fit_transform"):
        return transformer.fit_transform(X, y)
    transformer.fit(X, y)
    return transformer.transform(X)


def assert_no_train_target_leak(
    make_transformer: Callable[[], Any],
    *,
    n: int = 2000,
    d: int = 6,
    seeds: int = 3,
    max_abs_corr: float = 0.08,
    produce: Optional[Callable[[Any, Any, Any], Any]] = None,
) -> None:
    """Fail when a feature builder's output on its own training rows correlates with an independent noise target.

    *produce* ``(transformer, X, y) -> features`` overrides the default ``fit_transform(X, y)`` (or ``fit`` then
    ``transform``), for builders whose training-row features come from another method. With ``n=2000`` the noise level of
    a Spearman correlation is about 0.022, so the default bound sits at roughly 3.6 sigma; widen it for tiny *n*.
    """
    import numpy as np

    run = produce or _default_fit_transform
    worst = (0.0, -1, -1)
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((n, d))
        y = rng.standard_normal(n)
        for j, corr in enumerate(spearman_columns(run(make_transformer(), X, y), y)):
            if corr == corr and abs(corr) > abs(worst[0]):
                worst = (corr, seed, j)
    if abs(worst[0]) > max_abs_corr:
        raise AssertionError(
            f"target leak: output column {worst[2]} correlates {worst[0]:+.3f} with an independent noise target on the training rows "
            f"(seed {worst[1]}, bound {max_abs_corr}); build training-row features out of fold"
        )


def _auc(scores: Any, labels: Any) -> float:
    import numpy as np

    pos = np.asarray(labels) == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _rank(np.asarray(scores, dtype=float))
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def assert_oof_scores_at_chance(
    make_oof_scores: Callable[[Any, Any], Any],
    *,
    n: int = 2000,
    d: int = 6,
    seeds: int = 3,
    tol: float = 0.05,
) -> None:
    """Out-of-fold scores for pure-noise binary labels must have a training-row AUC of 0.5 within *tol*.

    *make_oof_scores* ``(X, y) -> scores`` returns one score per training row (a 2-D result uses its last column).
    """
    import numpy as np

    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((n, d))
        y = (rng.random(n) < 0.5).astype(np.int64)
        scores = np.asarray(make_oof_scores(X, y), dtype=float)
        if scores.ndim == 2:
            scores = scores[:, -1]
        auc = _auc(scores, y)
        if auc != auc or abs(auc - 0.5) > tol:
            raise AssertionError(f"OOF scores on noise labels have training AUC {auc:.3f} (seed {seed}), expected 0.5 +- {tol}: the scores are in-sample")
