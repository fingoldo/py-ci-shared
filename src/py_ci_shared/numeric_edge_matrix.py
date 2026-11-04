"""An adversarial-input matrix for metrics and numeric kernels, driven from a consumer's own test.

The single biggest hand-fixed class in mlframe was a metric returning 0.0, raising ``ZeroDivisionError``/``IndexError`` or
returning a finite number on input it cannot score: an empty fold, one class, all-NaN scores, ties, ``inf``, float32
overflow. Each fix shipped a one-off regression test and nothing enforced the matrix for the NEXT metric. This module is
that matrix: :func:`edge_cases` builds the named inputs, :func:`assert_edge_contract` runs one function over them and
checks each outcome against a contract, and :func:`assert_population_covered` fails when a public function of a module
is neither covered nor skipped with a reason.

Contract tokens (the *expect* table, merged over :data:`DEFAULT_EXPECT`):

- ``"nan"``: the result is NaN (a scalar, or every element of an array).
- ``"finite"``: the result is finite everywhere.
- ``"nan_or_valueerror"``: NaN, or a ``ValueError`` (a refusal with a message). The default for ``empty`` and ``all_nan``.
- ``"raise:ValueError"``: that exception (or a subclass) is required.
- ``"no_crash"``: any value, but no exception outside ``ValueError``/``TypeError`` (the default for the other cases).
- ``"any"``: nothing is checked except that a ``ZeroDivisionError``/``IndexError``/``RuntimeWarning`` never escapes.
- a callable ``result -> bool``.

``ZeroDivisionError``, ``IndexError`` and a ``RuntimeWarning`` (numpy division or invalid-value warnings, escalated to
errors for the call) are failures whatever the contract says. With *twin*, the CPU/GPU or numba/numpy counterpart is
called on the same arguments and the two must agree on raising, on the NaN pattern and, within ``rtol``/``atol``, on the
values.

Usage in a consumer's test (numpy is imported lazily; py-ci-shared does not depend on it)::

    from py_ci_shared.numeric_edge_matrix import assert_edge_contract

    def test_my_metric_edge_matrix():
        assert_edge_contract(my_metric, expect={"single_class": "nan"}, twin=my_metric_cpu)
"""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, NamedTuple, Optional, Union

__all__ = [
    "DEFAULT_EXPECT",
    "EDGE_CASE_NAMES",
    "EdgeCase",
    "assert_edge_contract",
    "assert_population_covered",
    "edge_cases",
    "public_functions",
]

EDGE_CASE_NAMES = (
    "empty",
    "one_row",
    "constant",
    "single_class",
    "all_nan",
    "some_nan",
    "inf",
    "ties_heavy",
    "float32_extreme",
    "subnormal",
    "zero_weight",
)
DEFAULT_EXPECT: Mapping[str, str] = {"empty": "nan_or_valueerror", "all_nan": "nan_or_valueerror"}
_NEVER = (ZeroDivisionError, IndexError)


class EdgeCase(NamedTuple):
    """One named adversarial input: labels, scores and weights of equal length."""

    name: str
    y_true: Any
    y_pred: Any
    weight: Any


def edge_cases(names: Optional[Iterable[str]] = None, *, seed: int = 0) -> list[EdgeCase]:
    """The named inputs, deterministic for *seed*. Binary labels, probability-like scores in (0, 1) unless the case says otherwise."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = 40
    y = (rng.random(n) < 0.4).astype(np.int64)
    p = rng.random(n)
    w = np.ones(n)
    built = {
        "empty": (np.empty(0, dtype=np.int64), np.empty(0), np.empty(0)),
        "one_row": (y[:1], p[:1], w[:1]),
        "constant": (y, np.full(n, 0.5), w),
        "single_class": (np.ones(n, dtype=np.int64), p, w),
        "all_nan": (y, np.full(n, np.nan), w),
        "some_nan": (y, np.where(np.arange(n) % 5 == 0, np.nan, p), w),
        "inf": (y, np.where(np.arange(n) % 7 == 0, np.inf, np.where(np.arange(n) % 11 == 0, -np.inf, p)), w),
        "ties_heavy": (y, np.round(p * 3) / 3, w),
        "float32_extreme": (y, np.where(np.arange(n) % 2 == 0, 3.0e38, -3.0e38).astype(np.float32), w.astype(np.float32)),
        "subnormal": (y, np.full(n, 1e-45, dtype=np.float32) * (1 + np.arange(n) % 3), w.astype(np.float32)),
        "zero_weight": (y, p, np.zeros(n)),
    }
    wanted = EDGE_CASE_NAMES if names is None else tuple(names)
    unknown = [x for x in wanted if x not in built]
    if unknown:
        raise ValueError(f"unknown edge case(s) {unknown}; known: {list(EDGE_CASE_NAMES)}")
    return [EdgeCase(x, *built[x]) for x in wanted]


def _call(fn: Callable[..., Any], args: tuple[Any, ...]) -> tuple[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        try:
            return "ok", fn(*args)
        except Exception as exc:  # best-effort: the exception is returned as data and judged by _raised_verdict, never dropped
            return "raised", exc


def _all_nan(value: Any) -> bool:
    import numpy as np

    try:
        arr = np.asarray(value, dtype=float)
    except (TypeError, ValueError):  # best-effort: non-numeric output is 'not NaN', which the caller reports as a failure
        return False
    return bool(np.isnan(arr).all())


def _all_finite(value: Any) -> bool:
    import numpy as np

    try:
        return bool(np.isfinite(np.asarray(value, dtype=float)).all())
    except (TypeError, ValueError):  # best-effort: non-numeric output is 'not finite', which the caller reports as a failure
        return False


def _raised_verdict(token: Union[str, Callable[[Any], bool]], exc: BaseException) -> Optional[str]:
    if isinstance(exc, _NEVER) or isinstance(exc, RuntimeWarning):
        return f"raised {type(exc).__name__}: {exc}"
    if callable(token):
        return f"raised {type(exc).__name__} where a result was required"
    if token == "any":
        return None
    if token.startswith("raise:"):
        wanted = token.split(":", 1)[1]
        return None if any(c.__name__ == wanted for c in type(exc).__mro__) else f"raised {type(exc).__name__}, wanted {wanted}"
    if token in ("nan_or_valueerror", "no_crash"):
        return None if isinstance(exc, (ValueError, TypeError)) else f"raised {type(exc).__name__}: {exc}"
    return f"raised {type(exc).__name__}: {exc}, but the contract {token!r} needs a result"


def _verdict(token: Union[str, Callable[[Any], bool]], outcome: str, value: Any) -> Optional[str]:
    """None when the outcome satisfies the contract, else the reason it does not."""
    if outcome == "raised":
        return _raised_verdict(token, value)
    if callable(token):
        return None if token(value) else f"result {value!r} fails the contract callable"
    if token.startswith("raise:"):
        return f"returned {value!r}, wanted {token}"
    if token in ("any", "no_crash"):
        return None
    if token in ("nan", "nan_or_valueerror"):
        return None if _all_nan(value) else f"returned {value!r}, wanted NaN"
    if token == "finite":
        return None if _all_finite(value) else f"returned {value!r}, wanted a finite value"
    raise ValueError(f"unknown contract token {token!r}")


def _twin_disagreement(a: tuple[str, Any], b: tuple[str, Any], rtol: float, atol: float) -> Optional[str]:
    import numpy as np

    if a[0] != b[0]:
        return f"one raised and one returned ({a[0]}: {a[1]!r} vs {b[0]}: {b[1]!r})"
    if a[0] == "raised":
        return None if type(a[1]) is type(b[1]) else f"raised {type(a[1]).__name__} vs {type(b[1]).__name__}"
    x, y = np.asarray(a[1], dtype=float), np.asarray(b[1], dtype=float)
    if x.shape != y.shape:
        return f"shapes differ {x.shape} vs {y.shape}"
    if not np.array_equal(np.isnan(x), np.isnan(y)):
        return "NaN patterns differ"
    keep = ~np.isnan(x)
    if not np.allclose(x[keep], y[keep], rtol=rtol, atol=atol):
        return f"values differ beyond rtol={rtol}, atol={atol}: {a[1]!r} vs {b[1]!r}"
    return None


def assert_edge_contract(
    fn: Callable[..., Any],
    *,
    make_args: Optional[Callable[[EdgeCase], tuple[Any, ...]]] = None,
    expect: Optional[Mapping[str, Union[str, Callable[[Any], bool]]]] = None,
    twin: Optional[Callable[..., Any]] = None,
    cases: Optional[Iterable[str]] = None,
    rtol: float = 1e-6,
    atol: float = 1e-9,
    seed: int = 0,
) -> None:
    """Run *fn* over the edge matrix and fail, listing every case, when an outcome breaks its contract or *twin* disagrees.

    *make_args* turns an :class:`EdgeCase` into the call's positional arguments (default ``(y_true, y_pred)``).
    """
    table: dict[str, Union[str, Callable[[Any], bool]]] = {**DEFAULT_EXPECT, **(expect or {})}
    unknown = [k for k in table if k not in EDGE_CASE_NAMES]
    if unknown:
        raise ValueError(f"expect names unknown case(s) {unknown}")
    build = make_args or (lambda c: (c.y_true, c.y_pred))
    failures: list[str] = []
    for case in edge_cases(cases, seed=seed):
        args = build(case)
        first = _call(fn, args)
        reason = _verdict(table.get(case.name, "no_crash"), *first)
        if reason is not None:
            failures.append(f"{case.name}: {reason}")
        if twin is not None:
            diff = _twin_disagreement(first, _call(twin, args), rtol, atol)
            if diff is not None:
                failures.append(f"{case.name}: twin disagrees: {diff}")
    if failures:
        name = getattr(fn, "__name__", repr(fn))
        raise AssertionError(f"{name} breaks its edge-case contract in {len(failures)} place(s):\n  " + "\n  ".join(failures))


def assert_population_covered(
    public: Collection[str],
    covered: Collection[str],
    skipped: Optional[Mapping[str, str]] = None,
) -> None:
    """Every public name must be covered by an edge-matrix test or skipped with a reason of at least 10 characters.

    Feed it ``[n for n, f in inspect.getmembers(module, inspect.isfunction) if not n.startswith("_")]`` so a new metric
    cannot join the module without joining the matrix.
    """
    skip = dict(skipped or {})
    short = sorted(k for k, v in skip.items() if len(v.strip()) < 10)
    missing = sorted(set(public) - set(covered) - set(skip))
    stale = sorted((set(covered) | set(skip)) - set(public))
    problems = []
    if missing:
        problems.append(f"neither covered nor skipped: {missing}")
    if short:
        problems.append(f"skip reason too short: {short}")
    if stale:
        problems.append(f"covered or skipped but not public (renamed or removed?): {stale}")
    if problems:
        raise AssertionError("edge-matrix population is out of date; " + "; ".join(problems))


def public_functions(module: Any) -> list[str]:
    """Names of the public functions defined in *module* itself (helper for :func:`assert_population_covered`)."""
    return sorted(n for n, f in inspect.getmembers(module, inspect.isfunction) if not n.startswith("_") and f.__module__ == module.__name__)
