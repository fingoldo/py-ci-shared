"""Tests for py_ci_shared.env_example_round_trip: each trap the module names, and the defect it exists for."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import pytest

pytest.importorskip("pydantic_settings")  # a dev extra; the module itself imports neither pydantic package

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode

from py_ci_shared.env_example_round_trip import assert_env_example_loads, documented_values, unloadable_values


def _split(v: object) -> object:
    return [p.strip() for p in v.split(",") if p.strip()] if isinstance(v, str) else v


class Fixed(BaseSettings):
    """The shape after the fix: NoDecode lets the comma-splitting validator see the raw string."""

    database_url: str
    log_level: str = "INFO"
    origins: Annotated[list[str], NoDecode] = []

    @field_validator("origins", mode="before")
    @classmethod
    def _origins(cls, v: object) -> object:
        return _split(v)

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v not in {"DEBUG", "INFO"}:
            raise ValueError(f"bad level {v}")
        return v


class Broken(Fixed):
    """The defect: a plain list, which the env source JSON-decodes before any validator runs."""

    origins: list[str] = []


BASE = {"DATABASE_URL": "postgresql://x"}


def _env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env.example"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_comma_list_loads_after_the_fix_and_not_before(tmp_path: Path) -> None:
    env = _env(tmp_path, "# ORIGINS=http://a:1,http://b:2\n")
    assert unloadable_values(Fixed, env, base_env=BASE) == ([], 1)
    problems, tried = unloadable_values(Broken, env, base_env=BASE)
    assert tried == 1 and len(problems) == 1 and "ORIGINS='http://a:1,http://b:2'" in problems[0]


def test_an_inline_comment_is_not_part_of_the_value(tmp_path: Path) -> None:
    env = _env(tmp_path, "LOG_LEVEL=DEBUG          # DEBUG or INFO\nHASH=a#b\n")
    assert [(n, v) for _, n, v in documented_values(env)] == [("LOG_LEVEL", "DEBUG"), ("HASH", "a#b")]
    assert unloadable_values(Fixed, env, base_env=BASE) == ([], 1)


def test_the_shell_environment_is_cleared_for_each_trial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A good value on the machine must not hide the documented bad one, and the machine is restored after."""
    monkeypatch.setenv("KEEP_ME", "1")
    env = _env(tmp_path, "LOG_LEVEL=LOUD\n")
    problems, _ = unloadable_values(Fixed, env, base_env=BASE)
    assert len(problems) == 1 and os.environ["KEEP_ME"] == "1"


def test_a_base_env_that_does_not_build_is_reported_once(tmp_path: Path) -> None:
    """Without it every line would fail for the same missing required field, reading as N separate defects."""
    problems, tried = unloadable_values(Fixed, _env(tmp_path, "LOG_LEVEL=DEBUG\nORIGINS=a\n"), base_env={})
    assert tried == 0 and len(problems) == 1 and problems[0].startswith("base_env alone does not build Fixed")


def test_placeholders_and_unknown_names_are_skipped(tmp_path: Path) -> None:
    env = _env(tmp_path, "DATABASE_URL=...\nLOG_LEVEL=<level>\nNOT_A_FIELD=x\nORIGINS=\n")
    assert unloadable_values(Fixed, env, base_env=BASE) == ([], 0)


def test_a_check_that_tried_nothing_fails(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 documented value"):
        assert_env_example_loads(Fixed, _env(tmp_path, "NOT_A_FIELD=x\n"), base_env=BASE)


def test_the_assert_names_the_line(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match=r"\.env\.example:2: LOG_LEVEL='LOUD'"):
        assert_env_example_loads(Fixed, _env(tmp_path, "# comment\nLOG_LEVEL=LOUD\n"), base_env=BASE)
