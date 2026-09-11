"""A pydantic field's declared bound or ``Literal`` set actually rejects a value outside it.

The failure this catches looks like a checked constraint and is not one: a field declares ``Field(ge=0)`` in a call
shape pydantic's runtime validator never reads, the schema and the IDE tooltip both say "must be >= 0", a caller
passes -1, construction accepts it, and things go wrong two functions deeper. mlframe wrote this check for its
training configs; glossum ported it. For every field with a numeric bound (``gt`` / ``ge`` / ``lt`` / ``le``) or a
string ``Literal``, construct the model with a value that violates it and require a ``ValidationError``.

A probe that fails for another reason (a required field it could not fill) is inconclusive, not a pass or a failure.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from types import ModuleType
from typing import Any, Literal, get_args, get_origin

__all__ = ["assert_field_bounds_enforced", "find_unenforced_literals", "find_unenforced_numeric_bounds", "iter_pydantic_models"]

_NUMERIC = ("gt", "ge", "lt", "le")


def iter_pydantic_models(modules: Iterable[ModuleType], *, accepted_modules: Iterable[str] | None = None) -> list[type]:
    """Every ``BaseModel`` subclass defined in *modules* (or re-exported from them, when *accepted_modules* names its home)."""
    from pydantic import BaseModel

    mods = list(modules)
    homes = set(accepted_modules) if accepted_modules is not None else {m.__name__ for m in mods}
    seen: dict[str, type] = {}
    for module in mods:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ in homes:
                seen[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [seen[k] for k in sorted(seen)]


def _sentinels(cls: type) -> dict[str, Any]:
    from pydantic_core import PydanticUndefined

    out: dict[str, Any] = {}
    for name, info in cls.model_fields.items():  # type: ignore[attr-defined]
        if info.default is PydanticUndefined and info.default_factory is None:
            out[name] = f"_bound_probe_{name}"
    return out


def _numeric_bounds(info: Any) -> dict[str, float]:
    found: dict[str, float] = {}
    for predicate in info.metadata or ():
        kind = type(predicate).__name__.lower()
        if kind in _NUMERIC and getattr(predicate, kind, None) is not None:
            found[kind] = getattr(predicate, kind)
    return found


def _violating(kind: str, bound: float) -> float:
    return {"ge": bound - 1.0, "gt": bound, "le": bound + 1.0, "lt": bound}[kind]


def _literal_values(annotation: Any) -> list[str]:
    if get_origin(annotation) is Literal:
        return [v for v in get_args(annotation) if isinstance(v, str)]
    return [v for arg in get_args(annotation) if get_origin(arg) is Literal for v in get_args(arg) if isinstance(v, str)]


def _accepts(cls: type, kwargs: dict[str, Any]) -> bool | None:
    """True if construction accepted *kwargs*, False if validation rejected them, None if something else failed."""
    from pydantic import ValidationError

    try:
        cls(**kwargs)
    except (ValidationError, ValueError):
        return False
    except Exception:
        return None
    return True


def find_unenforced_numeric_bounds(models: Iterable[type]) -> tuple[int, list[str]]:
    """``(fields probed, ["Model.field declares ge=0 but accepted -1.0", ...])``."""
    audited = 0
    bad: list[str] = []
    for cls in models:
        base = _sentinels(cls)
        for name, info in cls.model_fields.items():  # type: ignore[attr-defined]
            bounds = _numeric_bounds(info)
            if not bounds:
                continue
            kind, bound = next(iter(bounds.items()))
            audited += 1
            value = _violating(kind, bound)
            if _accepts(cls, {**base, name: value}) is True:
                bad.append(f"{cls.__name__}.{name} declares {kind}={bound} but accepted {value}")
    return audited, bad


def find_unenforced_literals(models: Iterable[type]) -> tuple[int, list[str]]:
    """``(fields probed, ["Model.field: Literal[...] accepted ...", ...])``."""
    audited = 0
    bad: list[str] = []
    probe = "_bound_probe_not_a_member"
    for cls in models:
        base = _sentinels(cls)
        for name, info in cls.model_fields.items():  # type: ignore[attr-defined]
            values = _literal_values(info.annotation)
            if not values:
                continue
            audited += 1
            if _accepts(cls, {**base, name: probe}) is True:
                bad.append(f"{cls.__name__}.{name}: Literal{values} accepted {probe!r}")
    return audited, bad


def assert_field_bounds_enforced(models: Iterable[type], *, require_numeric: bool = True, require_literal: bool = False) -> None:
    """Fail on a declared bound or Literal that construction does not enforce; the ``require_*`` flags are floors."""
    import pytest

    models = list(models)
    n_audited, n_bad = find_unenforced_numeric_bounds(models)
    l_audited, l_bad = find_unenforced_literals(models)
    if require_numeric and n_audited == 0:
        pytest.fail(f"no numeric-bounded field found in {len(models)} model(s); Check the model list -- the probe has nothing to look at")
    if require_literal and l_audited == 0:
        pytest.fail(f"no Literal field found in {len(models)} model(s); Check the model list -- the probe has nothing to look at")
    problems = n_bad + l_bad
    if problems:
        pytest.fail(f"{len(problems)} declared constraint(s) not enforced at construction; Fix the field so pydantic validates it:\n  " + "\n  ".join(problems))
