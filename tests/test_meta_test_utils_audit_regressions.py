"""Regression tests for ``py_ci_shared.meta_test_utils`` that began life in pyutilz's dev-fixes and 2026-09-03 domain audit suites.

Moved with the module: ``safe_import`` in ``__all__``, the Python 3.8 ``removesuffix`` ban, the bool sentinel, the repo-scan cache
reset, call-expression whitelists in the deferred counter and the ``_config`` suffix strip.
"""

from __future__ import annotations

from pathlib import Path


class TestMetaAll:
    """Finding 7: safe_import must be exported in __all__."""

    def test_safe_import_in_all(self):
        from py_ci_shared import meta_test_utils as m

        assert "safe_import" in m.__all__


def test_f06_citation_with_call_parens_resolves(tmp_path: Path):
    """A qualified-name citation written with call parens (`my_function()`) resolves to the symbol, and a missing one does not.

    The package floor is 3.9, where `str.removesuffix` exists, so the resolver may use it; what is pinned is the behaviour.
    """
    from py_ci_shared import meta_test_utils as mtu

    (tmp_path / "m.py").write_text("def my_function():\n    return 1\n", encoding="utf-8")
    mtu.clear_repo_scan_caches()
    assert mtu._names_a_repo_symbol("my_function()", tmp_path) is True
    assert mtu._names_a_repo_symbol("my_function", tmp_path) is True
    assert mtu._names_a_repo_symbol("no_such_function()", tmp_path) is False


def test_f58_bool_sentinel_is_the_non_default_value():
    """The bool sentinel was True, which is also the commonest default: with `actual != expected`,
    a parser that DROPPED the field still matched its True default and passed as intact."""
    import dataclasses

    from py_ci_shared.meta_test_utils import optional_scalar_fields

    @dataclasses.dataclass
    class Cfg:
        enabled: bool = True
        disabled: bool = False
        name: str = "x"

    sentinels = optional_scalar_fields(Cfg)
    assert sentinels["enabled"] is False
    assert sentinels["disabled"] is True


def test_f126_repo_scan_caches_can_be_cleared(tmp_path: Path):
    """The lru_cache is keyed on the ROOT PATH alone, so a test that writes a file and a later test
    that scans the same root saw a stale set -- making results depend on test order."""
    from py_ci_shared import meta_test_utils as mtu

    (tmp_path / "first.py").write_text("A = 1\n", encoding="utf-8")
    mtu.clear_repo_scan_caches()
    assert "second.py" not in mtu._repo_filenames(tmp_path)
    (tmp_path / "second.py").write_text("B = 2\n", encoding="utf-8")
    mtu.clear_repo_scan_caches()
    assert "second.py" in mtu._repo_filenames(tmp_path)


def test_f135_call_expression_whitelist_is_not_counted_as_zero(tmp_path: Path):
    """`set(_LEGACY_ENTRIES)` is an ast.Call too; recording it as 0 made tracked debt appear to
    shrink in exactly the case where it grew."""
    from py_ci_shared.meta_test_utils import count_user_deferred_entries

    (tmp_path / "test_w.py").write_text("_LEGACY = ['a', 'b']\n_USER_DEFERRED_EMPTY = set()\n_USER_DEFERRED_FULL = set(_LEGACY)\n", encoding="utf-8")
    out = count_user_deferred_entries(tmp_path)
    assert out.get("test_w::_USER_DEFERRED_EMPTY") == 0
    assert "test_w::_USER_DEFERRED_FULL" not in out


def test_f136_config_suffix_is_stripped_only_from_the_end():
    """An unanchored replace("_config", "") dropped the INNER occurrence too."""
    from py_ci_shared.meta_test_utils import snake_case_variants_of

    variants = snake_case_variants_of("MyConfigManagerConfig")
    assert "my_config_manager" in variants
    assert "my_manager" not in variants
