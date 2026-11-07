"""43-F6: names consumers imported by their underscore spelling have public names, and the old spelling is the same object."""

from __future__ import annotations

import importlib

from py_ci_shared import code_audit, sql_verify
from py_ci_shared.code_audit import _base, registry


def test_code_audit_exports_the_two_base_helpers_publicly():
    assert code_audit.is_test_file is _base.is_test_file
    assert code_audit.is_locals_or_globals_call is _base.is_locals_or_globals_call
    assert {"is_test_file", "is_locals_or_globals_call"} <= set(code_audit.__all__)


def test_registry_cpu_helpers_have_public_names_and_old_aliases():
    assert registry._physical_cpu_count is registry.physical_cpu_count
    assert registry._MIN_SCANNERS_PER_WORKER == registry.MIN_SCANNERS_PER_WORKER == 5
    assert isinstance(registry.physical_cpu_count(), int) and registry.physical_cpu_count() >= 1


def test_sql_verify_literal_percent_is_public_and_aliased():
    assert sql_verify._LITERAL_PERCENT == sql_verify.LITERAL_PERCENT == "@@literal-percent@@"


def test_toml_compat_is_public_and_the_old_module_is_an_alias():
    old = importlib.import_module("py_ci_shared._toml_compat")
    new = importlib.import_module("py_ci_shared.toml_compat")  # by sys.modules: test_toml_compat re-imports it
    assert old.tomllib is new.tomllib
    assert new.tomllib.loads("a = 1\n") == {"a": 1}
