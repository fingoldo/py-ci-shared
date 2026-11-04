"""Every input that changes a memoised result must change its cache key, and nothing else may.

A key that omits the target, the groups or one column returns a stale artefact for different data (a silently wrong model
reuse); a key that includes ``verbose`` or ``n_jobs`` recomputes forever. mlframe fixed this by hand eight times
(c4ee79f57, 8300ec4d0, f332fd6b8, afe080b6f, cac6527a7, 56530cd06, 2090a9972, b729b5c40). The existing gates check the key's
SHAPE (``constant_fallback_cache_key``, ``hash_fed_by_array_copy``, ``id_keyed_cache_validates_identity``,
``hash_key_determinism``); a static "parameter not mentioned in the key" scan flagged 215 of 331 functions and is
unusable. The viable check is behavioural:

    assert_key_sensitive(
        make_key,
        base={"X": X, "y": y, "groups": g, "verbose": 0},
        sensitive={"y": y2, "groups": g2},
        insensitive={"verbose": 1},
    )

It builds the key for *base*, then once per entry of *sensitive* with that one argument replaced (the key must differ), once
per entry of *insensitive* (the key must be equal), checks the key is deterministic, and reflects over the signature of
*make_key*: a parameter in neither table is a failure (the population check), so a new argument cannot be added to the key
function without declaring whether it belongs in the key.

Pure Python: no numpy needed (array values only need ``==``-comparable keys, which is the key function's job).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Collection, Mapping
from typing import Any, Optional

__all__ = ["assert_key_sensitive"]


def assert_key_sensitive(
    key_fn: Callable[..., Any],
    base: Mapping[str, Any],
    sensitive: Mapping[str, Any],
    insensitive: Optional[Mapping[str, Any]] = None,
    *,
    ignore_params: Collection[str] = ("self", "cls"),
) -> None:
    """Fail, listing every violation, when *key_fn*'s key does not move with a *sensitive* argument or moves with an *insensitive* one.

    *base* holds every argument of the call; *sensitive* maps an argument name to an alternative value that changes the result;
    *insensitive* maps a name to an alternative that must not. ``**kwargs``-style parameters are not reflected over.
    """
    insensitive = dict(insensitive or {})
    problems: list[str] = []
    overlap = sorted(set(sensitive) & set(insensitive))
    if overlap:
        raise ValueError(f"declared both sensitive and insensitive: {overlap}")
    missing_base = sorted((set(sensitive) | set(insensitive)) - set(base))
    if missing_base:
        raise ValueError(f"not in base: {missing_base}")
    params = [p.name for p in inspect.signature(key_fn).parameters.values() if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) and p.name not in ignore_params]
    undeclared = sorted(set(params) - set(sensitive) - set(insensitive))
    if undeclared:
        problems.append(f"parameters declared neither sensitive nor insensitive: {undeclared}")
    reference = key_fn(**base)
    if key_fn(**base) != reference:
        problems.append("the key is not deterministic: two calls with identical arguments differ")
    for name, alt in sensitive.items():
        if key_fn(**{**base, name: alt}) == reference:
            problems.append(f"'{name}' changes the result but not the key (stale reuse)")
    for name, alt in insensitive.items():
        if key_fn(**{**base, name: alt}) != reference:
            problems.append(f"'{name}' must not affect the result but changes the key (needless recompute)")
    if problems:
        raise AssertionError(f"{getattr(key_fn, '__name__', key_fn)} cache key is wrong:\n  " + "\n  ".join(problems))
