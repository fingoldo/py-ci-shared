"""47-T12 (unknown [tool.py_ci_shared] key lists the valid keys), 47-T13 (installed metadata that disagrees with the module)."""

from __future__ import annotations

import pytest

from py_ci_shared import ci_pin_version_skew as skew
from py_ci_shared._core.config import ConfigError, load_config


def test_an_unknown_key_error_lists_the_valid_keys_and_the_gate_form(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.py_ci_shared]\nbogus_gate = true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert "valid keys" in str(exc.value) and "enable = [" in str(exc.value)


def test_metadata_that_disagrees_with_the_module_is_reported():
    message = skew.metadata_mismatch("1.23.12", "1.23.10")
    assert message and "1.23.10" in message and "1.23.12" in message


def test_agreeing_or_missing_metadata_is_silent():
    assert skew.metadata_mismatch("1.23.12", "1.23.12") is None
    assert skew.metadata_mismatch("1.23.12", None) is None
