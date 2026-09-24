"""Unit tests for pydantic_field_bounds: probes start from a valid instance and blame the probed field."""

from __future__ import annotations

from typing import Literal, Optional

import pytest

pydantic = pytest.importorskip("pydantic")

from py_ci_shared.pydantic_field_bounds import (
    _violating,
    assert_field_bounds_enforced,
    find_unenforced_literals,
    find_unenforced_numeric_bounds,
)


class _Unenforced(pydantic.BaseModel):
    """A required non-str field, and a bound declared where the validator never reads it."""

    n: int
    rate: float = pydantic.Field(0.5, json_schema_extra={"minimum": 0})

    @pydantic.field_validator("rate", mode="wrap")
    @classmethod
    def _swallow(cls, value, handler):  # type: ignore[no-untyped-def]
        try:
            return handler(value)
        except pydantic.ValidationError:
            return value


class _RequiredIntWithUnenforcedBound(pydantic.BaseModel):
    n: int
    k: int = pydantic.Field(1, ge=0)

    @pydantic.field_validator("k", mode="wrap")
    @classmethod
    def _swallow(cls, value, handler):  # type: ignore[no-untyped-def]
        try:
            return handler(value)
        except pydantic.ValidationError:
            return value


class _RequiredIntWithEnforcedBound(pydantic.BaseModel):
    n: int
    k: int = pydantic.Field(1, ge=0)


class _TwoBounds(pydantic.BaseModel):
    p: float = pydantic.Field(0.5, ge=0, le=1)


class _UpperOnlyUnenforced(pydantic.BaseModel):
    p: float = pydantic.Field(0.5, ge=0, le=1)

    @pydantic.field_validator("p", mode="wrap")
    @classmethod
    def _only_lower(cls, value, handler):  # type: ignore[no-untyped-def]
        if isinstance(value, (int, float)) and value > 1:
            return value
        return handler(value)


class _Conint(pydantic.BaseModel):
    c: pydantic.conint(ge=0) = 1  # type: ignore[valid-type]


class _RequiredBounded(pydantic.BaseModel):
    size: int = pydantic.Field(ge=10, le=20)
    mode: Literal["a", "b"]
    note: Optional[str] = None


class TestAuditRegressions:
    def test_a_required_int_field_does_not_make_every_probe_look_enforced(self) -> None:
        audited, bad = find_unenforced_numeric_bounds([_RequiredIntWithUnenforcedBound])
        assert audited == 1 and bad == ["_RequiredIntWithUnenforcedBound.k declares ge=0 but accepted -1"]
        assert find_unenforced_numeric_bounds([_RequiredIntWithEnforcedBound]) == (1, [])

    def test_a_rejection_blaming_another_field_is_inconclusive_not_enforced(self) -> None:
        class Cross(pydantic.BaseModel):
            k: int = pydantic.Field(1, ge=0)
            other: int = 0

            @pydantic.model_validator(mode="after")
            def _blame_other(self):  # type: ignore[no-untyped-def]
                if self.k < 0:
                    raise ValueError("model-level")
                return self

        inconclusive: list[str] = []
        # ge=0 IS enforced by the field itself, so the field-level error names k: conclusive.
        assert find_unenforced_numeric_bounds([Cross], inconclusive=inconclusive) == (1, [])
        assert inconclusive == []

    def test_every_bound_is_probed(self) -> None:
        assert find_unenforced_numeric_bounds([_TwoBounds]) == (2, [])
        audited, bad = find_unenforced_numeric_bounds([_UpperOnlyUnenforced])
        assert audited == 2 and bad == ["_UpperOnlyUnenforced.p declares le=1 but accepted 2.0"]

    def test_conint_interval_is_probed(self) -> None:
        assert find_unenforced_numeric_bounds([_Conint]) == (1, [])

    def test_a_fractional_bound_on_an_int_field_uses_an_integer_violation(self) -> None:
        """Int parsing must never be what rejects the probe. Recent pydantic refuses ``int`` + ``lt=0.5`` when the
        schema is built, so the rule is pinned on the helper, plus a model whose int field has an integral bound."""
        assert [_violating(k, 0.5, integer=True) for k in ("ge", "gt", "le", "lt")] == [0, 0, 1, 1]
        assert all(isinstance(_violating(k, 0.5, integer=True), int) for k in ("ge", "gt", "le", "lt"))
        assert [_violating(k, 0.5) for k in ("ge", "gt", "le", "lt")] == [-0.5, 0.5, 1.5, 0.5]

        class OptionalInt(pydantic.BaseModel):
            n: Optional[int] = pydantic.Field(None, lt=3)

            @pydantic.field_validator("n", mode="wrap")
            @classmethod
            def _swallow(cls, value, handler):  # type: ignore[no-untyped-def]
                try:
                    return handler(value)
                except pydantic.ValidationError:
                    return value

        assert find_unenforced_numeric_bounds([OptionalInt]) == (1, ["OptionalInt.n declares lt=3 but accepted 3"])

    def test_required_bounded_and_literal_fields_are_filled_validly(self) -> None:
        assert find_unenforced_numeric_bounds([_RequiredBounded]) == (2, [])
        assert find_unenforced_literals([_RequiredBounded]) == (1, [])

    def test_an_unfillable_model_is_inconclusive_and_fails_the_floor(self) -> None:
        class Opaque:
            pass

        class Unfillable(pydantic.BaseModel):
            model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)
            thing: Opaque
            k: int = pydantic.Field(1, ge=0)

        inconclusive: list[str] = []
        assert find_unenforced_numeric_bounds([Unfillable], inconclusive=inconclusive) == (0, [])
        assert inconclusive and "Unfillable.k" in inconclusive[0]
        with pytest.raises(pytest.fail.Exception, match="inconclusive"):
            assert_field_bounds_enforced([Unfillable])

    def test_an_unenforced_json_schema_bound_model_fails_the_floor(self) -> None:
        with pytest.raises(pytest.fail.Exception, match="nothing to look at"):
            assert_field_bounds_enforced([_Unenforced])
