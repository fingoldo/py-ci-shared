"""The worker count a pytest run gets from free RAM (``_pytest_workers.plan_workers``)."""

from __future__ import annotations

from py_ci_shared._pytest_workers import DEFAULT_WORKER_GIB, GIB, plan_workers


def test_free_ram_sets_the_workers_after_a_reserve() -> None:
    # 12 GiB free of 16 GiB: the reserve is 20% of total (3.2 GiB, above the 2 GiB floor); (12 - 3.2) / 1.5 gives 5 workers.
    assert plan_workers(12 * GIB, 16 * GIB, physical_cores=8, env={}) == 5


def test_too_little_free_ram_is_serial() -> None:
    # 3 GiB free leaves nothing above the 3.2 GiB reserve, so no worker fits.
    assert plan_workers(3 * GIB, 16 * GIB, physical_cores=8, env={}) == 0


def test_the_physical_cores_cap_the_workers() -> None:
    # Plenty of RAM on 4 physical cores: one core stays free for the parent, so 3 workers.
    assert plan_workers(64 * GIB, 64 * GIB, physical_cores=4, env={}) == 3


def test_one_worker_is_serial() -> None:
    # A single worker is a subprocess with no parallel gain.
    assert plan_workers(64 * GIB, 64 * GIB, physical_cores=2, env={}) == 0


def test_no_hard_ceiling_on_a_large_machine() -> None:
    # The count follows the cores, not a fixed number: 64 physical cores with enough RAM give 63 workers.
    assert plan_workers(512 * GIB, 512 * GIB, physical_cores=64, env={}) == 63


def test_ci_is_serial_whatever_the_memory() -> None:
    assert plan_workers(64 * GIB, 64 * GIB, physical_cores=16, env={"CI": "true"}) == 0


def test_an_explicit_count_overrides_the_plan() -> None:
    assert plan_workers(3 * GIB, 16 * GIB, physical_cores=8, env={"PY_CI_SHARED_WORKERS": "3"}) == 3


def test_an_explicit_zero_is_serial_even_with_ram() -> None:
    assert plan_workers(64 * GIB, 64 * GIB, physical_cores=16, env={"PY_CI_SHARED_WORKERS": "0"}) == 0


def test_a_malformed_explicit_count_is_serial() -> None:
    assert plan_workers(64 * GIB, 64 * GIB, physical_cores=16, env={"PY_CI_SHARED_WORKERS": "many"}) == 0


def test_the_per_worker_estimate_can_be_overridden() -> None:
    # 12 GiB free, reserve 3.2 GiB: at 3 GiB per worker (8.8 / 3) that is 2 workers, not 5.
    assert plan_workers(12 * GIB, 16 * GIB, physical_cores=8, env={"PY_CI_SHARED_WORKER_GIB": "3"}) == 2


def test_a_malformed_per_worker_estimate_falls_back_to_the_default() -> None:
    expected = plan_workers(12 * GIB, 16 * GIB, physical_cores=8, env={})
    assert plan_workers(12 * GIB, 16 * GIB, physical_cores=8, env={"PY_CI_SHARED_WORKER_GIB": "lots"}) == expected
    assert DEFAULT_WORKER_GIB > 0


def test_a_malformed_explicit_count_is_logged(caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="py_ci_shared._pytest_workers"):
        assert plan_workers(64 * GIB, 64 * GIB, physical_cores=16, env={"PY_CI_SHARED_WORKERS": "many"}) == 0
    assert "PY_CI_SHARED_WORKERS='many' is not an integer" in caplog.text


def test_a_malformed_per_worker_estimate_is_logged(caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="py_ci_shared._pytest_workers"):
        plan_workers(12 * GIB, 16 * GIB, physical_cores=8, env={"PY_CI_SHARED_WORKER_GIB": "lots"})
    assert "PY_CI_SHARED_WORKER_GIB='lots' is not a number" in caplog.text
