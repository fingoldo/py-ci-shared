"""pytest-randomly's per-test seed reaches past 2**32 and is passed raw to third-party reseeders; the guard bounds it."""

from __future__ import annotations

import pytest

pytest_randomly = pytest.importorskip("pytest_randomly")

from py_ci_shared import randomly_seed_guard


def test_a_seed_past_2_32_reaches_a_reseeder_in_range(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(pytest_randomly, "entrypoint_reseeds", [seen.append])

    assert randomly_seed_guard.bound_randomly_reseeders()
    for reseed in pytest_randomly.entrypoint_reseeds:
        reseed(2**32 + 5)

    assert seen == [5]


def test_it_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Called from both the plugin and a conftest, it must not wrap twice."""
    seen: list[int] = []
    monkeypatch.setattr(pytest_randomly, "entrypoint_reseeds", [seen.append])

    randomly_seed_guard.bound_randomly_reseeders()
    randomly_seed_guard.bound_randomly_reseeders()

    assert len(pytest_randomly.entrypoint_reseeds) == 1
    pytest_randomly.entrypoint_reseeds[0](7)
    assert seen == [7]


def test_a_seed_in_range_is_passed_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(pytest_randomly, "entrypoint_reseeds", [seen.append])

    randomly_seed_guard.bound_randomly_reseeders()
    pytest_randomly.entrypoint_reseeds[0](123)

    assert seen == [123]


def test_entry_points_are_read_the_same_way_on_every_python(monkeypatch):
    from py_ci_shared import randomly_seed_guard as guard

    class _Ep:
        name = "x"

    class _Selectable:
        def select(self, group):
            return [_Ep()] if group == "g" else []

    monkeypatch.setattr(guard, "entry_points", lambda: _Selectable())
    assert [e.name for e in guard._entry_points_in("g")] == ["x"]
    assert guard._entry_points_in("other") == []
    monkeypatch.setattr(guard, "entry_points", lambda: {"g": [_Ep()]})  # the 3.9 dict shape
    assert [e.name for e in guard._entry_points_in("g")] == ["x"]
    assert guard._entry_points_in("other") == []


def test_the_real_entry_point_api_is_called_without_error_on_this_interpreter():
    """No monkeypatch: the 3.9 self-CI leg runs this against the interpreter's own importlib.metadata."""
    from py_ci_shared import randomly_seed_guard as guard

    found = guard._entry_points_in("pytest11")
    assert any(ep.value.startswith("py_ci_shared") for ep in found), [ep.value for ep in found]
