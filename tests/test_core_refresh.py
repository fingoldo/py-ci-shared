"""`_core.refresh`: a refresh request is seen from a pytest option, the env var (xdist) or argv."""

from __future__ import annotations

import sys

import pytest

from py_ci_shared._core import REFRESH_ENV_VAR, REFRESH_OPTION, refresh_requested, register_refresh_options

FLAG = "--refresh-value-asserts-baseline"


class _Config:
    def __init__(self, **options):
        self.options = options

    def getoption(self, name):
        if name not in self.options:
            raise ValueError(f"no option named {name!r}")
        return self.options[name]


class _Request:
    def __init__(self, config):
        self.config = config


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(REFRESH_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "argv", ["-c"])  # what an xdist worker sees


class TestEnvVar:
    @pytest.mark.parametrize("value", [FLAG, "refresh-value-asserts-baseline", "value-asserts", "all", "other, value-asserts"])
    def test_every_spelling_is_honoured(self, monkeypatch, value):
        monkeypatch.setenv(REFRESH_ENV_VAR, value)
        assert refresh_requested(FLAG)

    @pytest.mark.parametrize("value", ["", "other", "value", "--refresh-value-asserts"])
    def test_other_gates_do_not_trigger(self, monkeypatch, value):
        monkeypatch.setenv(REFRESH_ENV_VAR, value)
        assert not refresh_requested(FLAG)


class TestPytestConfig:
    def test_the_named_option(self):
        assert refresh_requested(FLAG, _Config(**{FLAG: True}))
        assert refresh_requested(FLAG, _Request(_Config(**{FLAG: True})))
        assert not refresh_requested(FLAG, _Config(**{FLAG: False}))

    def test_the_generic_option(self):
        assert refresh_requested(FLAG, _Config(**{REFRESH_OPTION: ["other,value-asserts"]}))
        assert not refresh_requested(FLAG, _Config(**{REFRESH_OPTION: ["other"]}))

    def test_an_unregistered_option_is_not_an_error(self):
        assert not refresh_requested(FLAG, _Config())

    def test_a_real_pytest_config_without_the_option(self, request):
        assert not refresh_requested(FLAG, request)


class TestArgvFallback:
    def test_the_flag_in_argv(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["pytest", FLAG])
        assert refresh_requested(FLAG)
        monkeypatch.setattr(sys, "argv", ["pytest", "--refresh-other-baseline"])
        assert not refresh_requested(FLAG)

    def test_the_generic_option_in_argv(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["pytest", f"{REFRESH_OPTION}=value-asserts"])
        assert refresh_requested(FLAG)
        monkeypatch.setattr(sys, "argv", ["pytest", REFRESH_OPTION, "all"])
        assert refresh_requested(FLAG)
        monkeypatch.setattr(sys, "argv", ["pytest", REFRESH_OPTION, "other"])
        assert not refresh_requested(FLAG)


def test_register_refresh_options_is_idempotent():
    class Parser:
        def __init__(self):
            self.names = []

        def addoption(self, name, **kwargs):
            if name in self.names:
                raise ValueError("already added")
            self.names.append(name)

    parser = Parser()
    register_refresh_options(parser, [FLAG])
    register_refresh_options(parser, [FLAG, "--refresh-other-baseline"])
    assert parser.names == [REFRESH_OPTION, "--py-ci-refresh-grow", FLAG, "--refresh-other-baseline"]


def test_registering_after_the_plugin_group_on_a_real_pytest_parser_is_not_an_error():
    # The pytest11 plugin registers --py-ci-refresh in its own group; a consumer conftest then calls a gate's
    # register_refresh_option(parser) on the root parser. argparse reports that cross-group clash as ArgumentError,
    # not ValueError, and it used to stop every test run of the consumer.
    from _pytest.config.argparsing import Parser

    parser = Parser()
    register_refresh_options(parser.getgroup("py-ci-shared"))
    register_refresh_options(parser, [FLAG])
    ns = parser.parse_known_args([FLAG])
    assert getattr(ns, FLAG.lstrip("-").replace("-", "_")) is True
