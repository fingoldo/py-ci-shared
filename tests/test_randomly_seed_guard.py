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
