"""The worker budget: half of the free physical cores, never fewer than one, overridable by an environment variable."""

from __future__ import annotations

import pytest

from py_ci_shared import worker_budget as wb


def test_idle_sixteen_core_machine_gets_half_its_cores():
    assert wb.worker_count_from(16, 0.0) == 8


def test_busy_machine_gets_only_its_free_share():
    # 16 physical cores at 75% busy leave 4 free; half of 4 is 2.
    assert wb.worker_count_from(16, 75.0) == 2


def test_saturated_machine_still_gets_one_worker():
    assert wb.worker_count_from(16, 100.0) == 1
    assert wb.worker_count_from(16, 99.9) == 1


def test_busy_percent_is_clamped_to_the_physical_range():
    assert wb.worker_count_from(8, -5.0) == wb.worker_count_from(8, 0.0)
    assert wb.worker_count_from(8, 250.0) == 1


def test_no_cores_reported_still_gives_one_worker():
    assert wb.worker_count_from(0, 0.0) == 1


def test_share_scales_the_free_cores():
    assert wb.worker_count_from(16, 0.0, share=0.25) == 4
    assert wb.worker_count_from(16, 0.0, share=1.0) == 16


def test_environment_override_wins_over_the_measurement(monkeypatch):
    monkeypatch.setenv(wb.ENV_OVERRIDE, "3")
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: pytest.fail("the CPU must not be sampled under an override"))
    assert wb.available_workers() == 3


@pytest.mark.parametrize("bad", ["", "0", "-2", "many"])
def test_invalid_override_falls_back_to_the_measurement(monkeypatch, bad):
    monkeypatch.setenv(wb.ENV_OVERRIDE, bad)
    monkeypatch.setattr(wb, "_physical_cores", lambda: 8)
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: 0.0)
    assert wb.available_workers() == 4


def test_available_workers_never_returns_less_than_one(monkeypatch):
    monkeypatch.delenv(wb.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(wb, "_physical_cores", lambda: 1)
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: 100.0)
    assert wb.available_workers() == 1
