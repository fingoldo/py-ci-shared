"""Input-shape invariants of an estimator's results: column order, worker count and frame type.

These bugs are behavioural, not syntactic (a static probe for position-indexed ``transform`` found one site): ``predict`` on
a frame with the same columns in another order silently returns shifted predictions (mlframe 676e5ed36, a37146d65); a
result that depends on ``n_jobs`` because seeds are derived per worker (efa6a4361, adaf9af1b); pandas and polars inputs
that produce different features because of null versus NaN (149c4f22f, 80bacc768, 25b3992b8). Each helper fits
fresh estimators from a factory and compares outputs.

- :func:`assert_column_order_invariant`: fit on ``X``, predict on ``X`` and on ``X`` with reversed columns; the outputs must
  match, or the estimator must refuse with ``ValueError``/``KeyError`` (never silently differ).
- :func:`assert_n_jobs_invariant`: ``factory(n_jobs=k)`` for each k must give equal outputs.
- :func:`assert_frame_type_parity`: the estimator fit and applied on ``X`` and on ``to_other(X)`` (pandas to polars, say)
  must give equal outputs; put explicit null/NaN cells in ``X``.

*method* names the output to compare (``"predict"``, ``"predict_proba"``, ``"transform"``). numpy is imported lazily; the
frame is whatever the consumer passes (pandas for column order).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["assert_column_order_invariant", "assert_frame_type_parity", "assert_n_jobs_invariant"]


def _same(a: Any, b: Any, atol: float, rtol: float) -> bool:
    import numpy as np

    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return x.shape == y.shape and bool(np.array_equal(np.isnan(x), np.isnan(y))) and bool(np.allclose(x, y, atol=atol, rtol=rtol, equal_nan=True))


def assert_column_order_invariant(
    est_factory: Callable[[], Any],
    X: Any,
    y: Any,
    *,
    method: str = "predict",
    atol: float = 1e-8,
    rtol: float = 1e-6,
    refusals: tuple[type[BaseException], ...] = (ValueError, KeyError),
) -> None:
    """Fail when applying a fitted estimator to *X* with reversed columns silently changes the output.

    A refusal (an exception in *refusals*) is an accepted outcome; a different result is not.
    """
    est = est_factory().fit(X, y)
    reference = getattr(est, method)(X)
    reordered = X[list(X.columns[::-1])]
    try:
        other = getattr(est, method)(reordered)
    except refusals:  # best-effort: a refusal in `refusals` is the documented accepted outcome of this invariant
        return
    if not _same(reference, other, atol, rtol):
        raise AssertionError(f"{type(est).__name__}.{method} changes when the columns are reordered (columns {list(X.columns)} reversed) and does not refuse")


def assert_n_jobs_invariant(
    est_factory: Callable[..., Any],
    X: Any,
    y: Any,
    *,
    n_jobs: Sequence[int] = (1, 2),
    method: str = "predict",
    atol: float = 1e-8,
    rtol: float = 1e-6,
) -> None:
    """Fail when ``est_factory(n_jobs=k)`` gives a different output for some k than for the first one."""
    results = [getattr(est_factory(n_jobs=k).fit(X, y), method)(X) for k in n_jobs]
    for k, res in zip(n_jobs[1:], results[1:]):
        if not _same(results[0], res, atol, rtol):
            raise AssertionError(f"{method} output depends on n_jobs (n_jobs={n_jobs[0]} vs {k}): seeds or reductions are derived per worker")


def assert_frame_type_parity(
    est_factory: Callable[[], Any],
    X: Any,
    y: Any,
    to_other: Callable[[Any], Any],
    *,
    method: str = "predict",
    atol: float = 1e-8,
    rtol: float = 1e-6,
) -> None:
    """Fail when the estimator gives different outputs for *X* and ``to_other(X)`` (fit and apply on the same type each time)."""
    first = getattr(est_factory().fit(X, y), method)(X)
    other_x = to_other(X)
    second = getattr(est_factory().fit(other_x, y), method)(other_x)
    if not _same(first, second, atol, rtol):
        raise AssertionError(f"{method} differs between {type(X).__name__} and {type(other_x).__name__} inputs (null versus NaN handling?)")
