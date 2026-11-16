"""49-T4(a): under the CLI, the refresh command names the gate the developer enabled, not the baseline's internal label."""

from __future__ import annotations

import pytest

from py_ci_shared._core.outcome import CLI_ENV_VAR, refresh_hint, running_gate


def test_cli_refresh_hint_names_the_enabled_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLI_ENV_VAR, "1")
    with running_gate("value_bearing_asserts"):
        hint = refresh_hint("--refresh-value-asserts", "value-asserts")
    assert "`py-ci-shared refresh value_bearing_asserts`" in hint
    assert "value-asserts" not in hint


def test_cli_refresh_hint_falls_back_to_the_label_outside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLI_ENV_VAR, "1")
    assert "`py-ci-shared refresh function_length`" in refresh_hint("--x", "function_length")


def test_pytest_refresh_hint_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLI_ENV_VAR, raising=False)
    with running_gate("value_bearing_asserts"):
        assert refresh_hint("--refresh-x", "value-asserts") == "pytest --refresh-x (or PY_CI_SHARED_REFRESH=value-asserts)"
