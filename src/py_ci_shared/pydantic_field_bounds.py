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
import math
from collections.abc import Iterable
from types import ModuleType
from typing import Any, Literal, Optional, Union, get_args, get_origin

__all__ = ["assert_field_bounds_enforced", "find_unenforced_literals", "find_unenforced_numeric_bounds", "iter_pydantic_models"]

_NUMERIC = ("gt", "ge", "lt", "le")
_GENERIC_CANDIDATES: tuple[Any, ...] = (0, 1, 0.5, "x", True, [], {}, None, "2020-01-01", "00000000-0000-0000-0000-000000000000")


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


def _numeric_bounds(info: Any) -> dict[str, float]:
    """Every numeric bound on a field: ``Ge``/``Gt``/... predicates and ``Interval`` (``conint``, ``confloat``) alike."""
    found: dict[str, float] = {}
    for predicate in info.metadata or ():
        for kind in _NUMERIC:
            value = getattr(predicate, kind, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                found.setdefault(kind, value)
    return found


def _is_int_field(annotation: Any) -> bool:
    """``int`` or ``Optional[int]`` (bool excluded): a violating value must then be an integer too."""
    if annotation is int:
        return True
    if get_origin(annotation) is Union:
        members = [a for a in get_args(annotation) if a is not type(None)]
        return len(members) == 1 and members[0] is int
    return False


def _violating(kind: str, bound: float, *, integer: bool = False) -> float:
    """A value just outside ``kind=bound``; an integer one for an int field, so int parsing cannot be what rejects it."""
    if integer:
        return {"ge": math.ceil(bound) - 1, "gt": math.floor(bound), "le": math.floor(bound) + 1, "lt": math.ceil(bound)}[kind]
    return {"ge": bound - 1.0, "gt": bound, "le": bound + 1.0, "lt": bound}[kind]


def _satisfying(bounds: dict[str, float], integer: bool) -> list[Any]:
    """Values inside every bound, tried first when a required bounded field has to be filled."""
    out: list[Any] = []
    lows = ([bounds["ge"]] if "ge" in bounds else []) + ([bounds["gt"] + (1 if integer else 0.5)] if "gt" in bounds else [])
    if lows:
        out.append(math.ceil(max(lows)) if integer else max(lows))
    if "le" in bounds or "lt" in bounds:
        hi = bounds["le"] if "le" in bounds else bounds["lt"] - (1 if integer else 0.5)
        out.append(math.floor(hi) if integer else hi)
    return out


def _literal_values(annotation: Any) -> list[str]:
    if get_origin(annotation) is Literal:
        return [v for v in get_args(annotation) if isinstance(v, str)]
    return [v for arg in get_args(annotation) if get_origin(arg) is Literal for v in get_args(arg) if isinstance(v, str)]


def _candidates(info: Any) -> list[Any]:
    literal = [v for v in get_args(info.annotation) if get_origin(info.annotation) is Literal] or [
        v for arg in get_args(info.annotation) if get_origin(arg) is Literal for v in get_args(arg)
    ]
    bounds = _numeric_bounds(info)
    return [*literal, *_satisfying(bounds, _is_int_field(info.annotation)), *_GENERIC_CANDIDATES]


def _required(cls: type) -> dict[str, Any]:
    from pydantic_core import PydanticUndefined

    return {name: info for name, info in cls.model_fields.items() if info.default is PydanticUndefined and info.default_factory is None}  # type: ignore[attr-defined]


def _error_fields(exc: Exception) -> Optional[set[str]]:
    """Top-level field names a ``ValidationError`` blames; ``None`` for any other exception."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return None
    try:
        return {str(e["loc"][0]) for e in errors() if e.get("loc")}
    except Exception:
        return None


def _valid_base(cls: type) -> "tuple[Optional[dict[str, Any]], str]":
    """Keyword arguments that construct *cls* successfully, found by trying typed candidates for each required field;
    ``(None, why)`` when none could be found (every probe of this model is then inconclusive)."""
    required = _required(cls)
    choices = {name: _candidates(info) for name, info in required.items()}
    index = dict.fromkeys(required, 0)
    for _ in range(sum(len(c) for c in choices.values()) + 1):
        kwargs = {name: choices[name][index[name]] for name in required}
        try:
            cls(**kwargs)
            return kwargs, ""
        except Exception as exc:
            blamed = _error_fields(exc)
            if not blamed or not (blamed & set(required)):
                return None, f"{type(exc).__name__}: {exc}".splitlines()[0]
            for name in blamed & set(required):
                index[name] += 1
                if index[name] >= len(choices[name]):
                    return None, f"no candidate value satisfies required field {name!r}"
    return None, "no combination of candidate values constructs the model"


def _probe(cls: type, base: dict[str, Any], name: str, value: Any) -> Optional[bool]:
    """True if construction ACCEPTED *value* for *name*, False if it rejected it FOR THAT FIELD, None otherwise."""
    try:
        cls(**{**base, name: value})
    except Exception as exc:
        blamed = _error_fields(exc)
        return False if blamed is not None and name in blamed else None
    return True


def find_unenforced_numeric_bounds(models: Iterable[type], *, inconclusive: Optional[list[str]] = None) -> tuple[int, list[str]]:
    """``(bounds probed, ["Model.field declares ge=0 but accepted -1", ...])``.

    Every bound of a field is probed separately (``ge=0, le=1`` is two probes), ``conint``/``Interval`` included.
    The probe starts from a VALID instance (required fields filled with typed candidates) and counts a rejection
    only when the error names the probed field. A probe that cannot be made conclusive is not counted; its reason
    is appended to *inconclusive* when a list is given.
    """
    audited = 0
    bad: list[str] = []
    for cls in models:
        bases: Optional[tuple[Optional[dict[str, Any]], str]] = None
        for name, info in cls.model_fields.items():  # type: ignore[attr-defined]
            bounds = _numeric_bounds(info)
            if not bounds:
                continue
            if bases is None:
                bases = _valid_base(cls)
            base, why = bases
            for kind, bound in bounds.items():
                if base is None:
                    if inconclusive is not None:
                        inconclusive.append(f"{cls.__name__}.{name} {kind}={bound}: no valid instance to start from ({why})")
                    continue
                value = _violating(kind, bound, integer=_is_int_field(info.annotation))
                verdict = _probe(cls, base, name, value)
                if verdict is None:
                    if inconclusive is not None:
                        inconclusive.append(f"{cls.__name__}.{name} {kind}={bound}: rejecting {value} blamed another field")
                    continue
                audited += 1
                if verdict:
                    bad.append(f"{cls.__name__}.{name} declares {kind}={bound} but accepted {value}")
    return audited, bad


def find_unenforced_literals(models: Iterable[type], *, inconclusive: Optional[list[str]] = None) -> tuple[int, list[str]]:
    """``(fields probed, ["Model.field: Literal[...] accepted ...", ...])``; same valid-start and blame rules as
    :func:`find_unenforced_numeric_bounds`."""
    audited = 0
    bad: list[str] = []
    probe = "_bound_probe_not_a_member"
    for cls in models:
        bases: Optional[tuple[Optional[dict[str, Any]], str]] = None
        for name, info in cls.model_fields.items():  # type: ignore[attr-defined]
            values = _literal_values(info.annotation)
            if not values:
                continue
            if bases is None:
                bases = _valid_base(cls)
            base, why = bases
            if base is None:
                if inconclusive is not None:
                    inconclusive.append(f"{cls.__name__}.{name} Literal: no valid instance to start from ({why})")
                continue
            verdict = _probe(cls, base, name, probe)
            if verdict is None:
                if inconclusive is not None:
                    inconclusive.append(f"{cls.__name__}.{name} Literal: rejecting {probe!r} blamed another field")
                continue
            audited += 1
            if verdict:
                bad.append(f"{cls.__name__}.{name}: Literal{values} accepted {probe!r}")
    return audited, bad


def assert_field_bounds_enforced(
    models: Iterable[type], *, require_numeric: bool = True, require_literal: bool = False, allow_inconclusive: bool = True
) -> None:
    """Fail on a declared bound or Literal that construction does not enforce; the ``require_*`` flags are floors
    over CONCLUSIVE probes. With ``allow_inconclusive=False`` a probe that could not be made conclusive fails too."""
    import pytest

    models = list(models)
    inconclusive: list[str] = []
    n_audited, n_bad = find_unenforced_numeric_bounds(models, inconclusive=inconclusive)
    l_audited, l_bad = find_unenforced_literals(models, inconclusive=inconclusive)
    note = ("\n  inconclusive probes:\n    " + "\n    ".join(inconclusive)) if inconclusive else ""
    if require_numeric and n_audited == 0:
        pytest.fail(f"no numeric-bounded field could be probed in {len(models)} model(s); Check the model list -- the probe has nothing to look at{note}")
    if require_literal and l_audited == 0:
        pytest.fail(f"no Literal field could be probed in {len(models)} model(s); Check the model list -- the probe has nothing to look at{note}")
    problems = n_bad + l_bad + ([] if allow_inconclusive else [f"inconclusive: {i}" for i in inconclusive])
    if problems:
        pytest.fail(f"{len(problems)} declared constraint(s) not enforced at construction; Fix the field so pydantic validates it:\n  " + "\n  ".join(problems))
