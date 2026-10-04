"""Run-time check that an object carries every callable attribute of a protocol a caller reads off it with a fallback.

``getattr(fn, "refresh", fn)`` cannot tell a loader that has no ``refresh`` from a wrapper that forgot to copy it: the
dashboard's ``load_top_jobs`` re-exported ``cache_clear`` from its ``@ttl_cached`` inner loader but not ``refresh``, and
the warm scheduler silently fell back to an ordinary cached call (audit 2026-10-03, P1). Call
:func:`assert_satisfies_protocol` where such an object is registered (a warm-target registry, a cache-clearer list) so
the gap fails at import instead. :mod:`py_ci_shared.wrapper_protocol_parity` is the static half.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = ["assert_satisfies_protocol", "missing_protocol_attributes"]


def missing_protocol_attributes(obj: Any, names: Iterable[str], *, inner: Any = None) -> list[str]:
    """The *names* that *obj* lacks as callables; with *inner*, only those *inner* has (the wrapper's duty)."""
    wanted = [n for n in names if inner is None or callable(getattr(inner, n, None))]
    return sorted(n for n in wanted if not callable(getattr(obj, n, None)))


def assert_satisfies_protocol(obj: Any, names: Iterable[str], *, what: str = "", inner: Any = None) -> None:
    """Raise ``AssertionError`` naming *what* (default: the object's name) when *obj* lacks a callable attribute of
    *names*. Call it where the attribute is relied on, e.g. in a warm-target registrar::

        assert_satisfies_protocol(fn, {"refresh", "cache_clear"}, what=f"warm target {fn.__name__}")

    With *inner* (the callable *obj* wraps), only the attributes *inner* has are required."""
    missing = missing_protocol_attributes(obj, names, inner=inner)
    if missing:
        label = what or getattr(obj, "__qualname__", None) or repr(obj)
        raise AssertionError(f"{label} lacks {', '.join(missing)}; a caller using getattr(obj, name, fallback) would silently take the fallback")
