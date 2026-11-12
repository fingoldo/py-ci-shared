"""Enabling a command-line module as a gate says what to run instead (audit 49-F11)."""

from __future__ import annotations

import pytest

from py_ci_shared._core.config import ConfigError, GateRun
from py_ci_shared._core.runner import resolve_gate


def test_a_cli_module_enabled_as_a_gate_names_the_command_to_run() -> None:
    """Negative control (49-F11): the old message only said the module cannot be enabled, with no next command."""
    with pytest.raises(ConfigError) as caught:
        resolve_gate(GateRun(name="bandit_warn", module="bandit_warn", entry=None))
    message = str(caught.value)
    assert "command-line tool" in message
    assert "python -m py_ci_shared.bandit_warn" in message
    assert "assert_*" in message
