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
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: 64 * GiB)  # the CPU arithmetic is under test, not this host's RAM
    assert wb.available_workers() == 4


def test_available_workers_never_returns_less_than_one(monkeypatch):
    monkeypatch.delenv(wb.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(wb, "_physical_cores", lambda: 1)
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: 100.0)
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: 64 * GiB)  # the CPU arithmetic is under test, not this host's RAM
    assert wb.available_workers() == 1


def test_a_four_core_laptop_at_a_fifth_busy_gets_two_workers():
    """4 physical cores at 17.5% busy leave 3.3 free; half of that is 1.65, which a truncated budget reported as one worker."""
    assert wb.worker_count_from(4, 17.5) == 2


def test_half_a_worker_rounds_up_not_down():
    assert wb.worker_count_from(2, 0.0) == 1  # 1.0 stays 1
    assert wb.worker_count_from(3, 0.0) == 2  # 1.5 rounds half up


GiB = 2**30


def test_each_worker_needs_four_gib_free():
    assert wb.worker_count_by_memory(8, 32 * GiB) == 8
    assert wb.worker_count_by_memory(8, 12 * GiB) == 3  # 12 GiB buys three workers, not eight


def test_the_memory_cap_never_goes_below_one_worker():
    assert wb.worker_count_by_memory(4, 1 * GiB) == 1
    assert wb.worker_count_by_memory(4, 0) == 1


def test_no_measurement_leaves_the_cpu_count_alone():
    assert wb.worker_count_by_memory(3, None) == 3


def test_available_workers_applies_the_memory_cap(monkeypatch):
    """A 16-core idle machine would get 8 workers from the CPU; with 9 GiB free the memory cap brings it to 2."""
    monkeypatch.delenv(wb.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(wb, "_physical_cores", lambda: 16)
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: 0.0)
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: 64 * GiB)  # the CPU arithmetic is under test, not this host's RAM
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: 9 * GiB)
    assert wb.available_workers() == 2


def test_a_repository_can_set_its_own_memory_per_worker(monkeypatch):
    """With 9 GiB free the package default caps an idle 16-core machine at two workers; a repository that needs 2 GiB per worker gets four."""
    monkeypatch.delenv(wb.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(wb, "_physical_cores", lambda: 16)
    monkeypatch.setattr(wb, "_busy_percent", lambda _s: 0.0)
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: 9 * GiB)
    assert wb.available_workers() == 2
    assert wb.available_workers(ram_per_worker_bytes=2 * GiB) == 4


def test_the_override_still_bypasses_the_memory_cap(monkeypatch):
    monkeypatch.setenv(wb.ENV_OVERRIDE, "6")
    monkeypatch.setattr(wb, "_free_memory_bytes", lambda: pytest.fail("memory must not be sampled under an override"))
    assert wb.available_workers() == 6
