"""Unit tests for the cfg().get() call-site vs Pydantic-schema parity checks.

Real scratch source trees + a tiny Pydantic schema class, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from pydantic import BaseModel, Field

from py_ci_shared.config_call_site_parity import (
    ConstantResolver,
    assert_call_site_defaults_match_schema_defaults,
    assert_every_cfg_get_call_resolves_to_a_schema_field,
    assert_every_schema_field_has_a_reader,
    assert_no_divergent_cfg_get_call_site_defaults,
    find_cfg_get_calls,
    schema_section_field_defaults,
    schema_section_field_map,
)


class _Filters(BaseModel):
    max_results: int = 50
    enabled: bool = True


class _Db(BaseModel):
    pool_max: int = 10


class _AppConfig(BaseModel):
    filters: _Filters = _Filters()
    db: _Db = _Db()


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source.lstrip("\n"), encoding="utf-8")
    return p


def test_schema_section_field_map():
    assert schema_section_field_map(_AppConfig) == {"filters": {"max_results", "enabled"}, "db": {"pool_max"}}


class _DbWithFactory(BaseModel):
    pool_max: int = 10
    pricing: dict = Field(default_factory=dict)


class _AppConfigWithFactory(BaseModel):
    db: _DbWithFactory = _DbWithFactory()


def test_schema_section_field_defaults_handles_default_factory():
    """A field declared with default_factory=... (required for a mutable
    default like a dict) has FieldInfo.default set to PydanticUndefined --
    the factory must be called to get the real produced value."""
    defaults = schema_section_field_defaults(_AppConfigWithFactory)
    assert defaults[("db", "pricing")] == {}
    assert defaults[("db", "pool_max")] == 10


def test_find_cfg_get_calls_direct_chain(tmp_path):
    _write(tmp_path, "a.py", """
def f():
    return cfg().get("filters", "max_results", 50, int)
""")
    calls = find_cfg_get_calls(tmp_path, [tmp_path / "a.py"])
    assert len(calls) == 1
    assert (calls[0].section, calls[0].key) == ("filters", "max_results")


def test_find_cfg_get_calls_bound_name(tmp_path):
    _write(tmp_path, "a.py", """
def f():
    _c = cfg()
    return _c.get("db", "pool_max", 10)
""")
    calls = find_cfg_get_calls(tmp_path, [tmp_path / "a.py"])
    assert len(calls) == 1
    assert (calls[0].section, calls[0].key) == ("db", "pool_max")


def test_assert_every_cfg_get_call_resolves_to_a_schema_field_passes(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    assert_every_cfg_get_call_resolves_to_a_schema_field(tmp_path, [tmp_path / "a.py"], _AppConfig)


def test_assert_every_cfg_get_call_resolves_to_a_schema_field_fails_on_unknown_key(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "typo_field", 1)\n')
    with pytest.raises(pytest.fail.Exception, match="typo_field"):
        assert_every_cfg_get_call_resolves_to_a_schema_field(tmp_path, [tmp_path / "a.py"], _AppConfig)


def test_assert_every_cfg_get_call_resolves_to_a_schema_field_fails_on_unknown_section(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("nosuchsection", "x", 1)\n')
    with pytest.raises(pytest.fail.Exception, match="unknown section"):
        assert_every_cfg_get_call_resolves_to_a_schema_field(tmp_path, [tmp_path / "a.py"], _AppConfig)


def test_assert_every_schema_field_has_a_reader_passes(tmp_path):
    _write(tmp_path, "a.py", """
cfg().get("filters", "max_results", 50)
cfg().get("filters", "enabled", True)
cfg().get("db", "pool_max", 10)
""")
    assert_every_schema_field_has_a_reader(tmp_path, [tmp_path / "a.py"], _AppConfig)


def test_assert_every_schema_field_has_a_reader_fails_on_unread_field(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    with pytest.raises(pytest.fail.Exception, match=r"\[db\] pool_max"):
        assert_every_schema_field_has_a_reader(tmp_path, [tmp_path / "a.py"], _AppConfig)


def test_assert_every_schema_field_has_a_reader_honors_known_indirect_readers(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    assert_every_schema_field_has_a_reader(
        tmp_path, [tmp_path / "a.py"], _AppConfig,
        known_indirect_readers={("filters", "enabled"): "read via snapshot()", ("db", "pool_max"): "read via snapshot()"},
    )


def test_assert_no_divergent_cfg_get_call_site_defaults_passes_when_agreeing(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    _write(tmp_path, "b.py", 'cfg().get("filters", "max_results", 50)\n')
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files)


def test_assert_no_divergent_cfg_get_call_site_defaults_fails_on_disagreement(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    _write(tmp_path, "b.py", 'cfg().get("filters", "max_results", 100)\n')
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    with pytest.raises(pytest.fail.Exception, match="divergent"):
        assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files)


def test_constant_resolver_follows_the_actual_import_not_a_same_named_collision(tmp_path):
    """Regression: two UNRELATED files each define their own BATCH_SIZE with a
    DIFFERENT value (a real pattern seen in practice -- six modules in one
    project each had their own same-named BATCH_SIZE). A call site importing
    BATCH_SIZE from ONE specific module must resolve to THAT module's value,
    not whichever same-named constant a flat, non-import-aware scan happens
    to find first."""
    _write(tmp_path, "unrelated.py", "BATCH_SIZE = 999\n")
    _write(tmp_path, "a.py", "BATCH_SIZE = 500\n")
    b_path = _write(tmp_path, "b.py", 'from a import BATCH_SIZE\ncfg().get("filters", "max_results", BATCH_SIZE)\n')
    files = [tmp_path / "unrelated.py", tmp_path / "a.py", b_path]
    calls = find_cfg_get_calls(tmp_path, files)
    assert len(calls) == 1
    resolver = ConstantResolver(tmp_path)
    resolved = resolver.resolve(calls[0].default_node, calls[0].abs_path)
    assert resolved == 500, f"must resolve via b.py's own import of a.BATCH_SIZE (500), not unrelated.py's BATCH_SIZE (999); got {resolved!r}"


def test_assert_no_divergent_cfg_get_call_site_defaults_named_constant_agrees_with_literal(tmp_path):
    """A bare literal and a named module constant resolving to the same value
    must NOT be flagged as divergent."""
    _write(tmp_path, "consts.py", "MAX_RESULTS = 50\n")
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    _write(tmp_path, "b.py", "from consts import MAX_RESULTS\ncfg().get(\"filters\", \"max_results\", MAX_RESULTS)\n")
    files = [tmp_path / "consts.py", tmp_path / "a.py", tmp_path / "b.py"]
    assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files)


def test_assert_no_divergent_cfg_get_call_site_defaults_omitted_type_matches_default_type_repr(tmp_path):
    """One call site passes type_=int explicitly, a sibling omits it entirely
    (relying on the accessor's own `type_: type = int` signature default) --
    normalized via default_type_repr, these must NOT be flagged as divergent."""
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50, int)\n')
    _write(tmp_path, "b.py", 'cfg().get("filters", "max_results", 50)\n')
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    with pytest.raises(pytest.fail.Exception, match="divergent"):
        assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files)
    assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files, default_type_repr="int")


def test_assert_no_divergent_cfg_get_call_site_defaults_unresolvable_site_is_skipped_not_flagged(tmp_path):
    """A genuinely dynamic default (e.g. derived from a CLI arg) at ONE call
    site must not be compared against a resolvable sibling site at all --
    treating the unresolvable value as if it were a distinct, divergent
    value would spuriously flag it even when the resolvable sites agree."""
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    _write(tmp_path, "b.py", 'cfg().get("filters", "max_results", 50)\n')
    _write(tmp_path, "c.py", "def f(some_dynamic_arg):\n    cfg().get(\"filters\", \"max_results\", some_dynamic_arg)\n")
    files = [tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.py"]
    assert_no_divergent_cfg_get_call_site_defaults(tmp_path, files)  # must not raise


def test_assert_call_site_defaults_match_schema_defaults_passes(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 50)\n')
    assert_call_site_defaults_match_schema_defaults(tmp_path, [tmp_path / "a.py"], _AppConfig, min_checked=1)


def test_assert_call_site_defaults_match_schema_defaults_fails_on_mismatch(tmp_path):
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 999)\n')
    with pytest.raises(pytest.fail.Exception, match="disagree"):
        assert_call_site_defaults_match_schema_defaults(tmp_path, [tmp_path / "a.py"], _AppConfig, min_checked=1)


def test_assert_call_site_defaults_match_schema_defaults_min_checked_guard(tmp_path):
    """A dynamic (unresolvable) default must be skipped, not counted -- and if
    that leaves too few checked call sites, the resolver-broken guard fires."""
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", some_dynamic_value())\n')
    with pytest.raises(AssertionError, match="only resolved"):
        assert_call_site_defaults_match_schema_defaults(tmp_path, [tmp_path / "a.py"], _AppConfig, min_checked=1)


def test_assert_call_site_defaults_match_schema_defaults_honors_known_intentional_mismatches(tmp_path):
    """A call site whose default is DELIBERATELY different from the schema's
    normal-operation value (a safety-net fallback, e.g. variant_count=1 vs
    the schema's normal 3) must not fail once whitelisted."""
    _write(tmp_path, "a.py", 'cfg().get("filters", "max_results", 1)\n')
    with pytest.raises(pytest.fail.Exception, match="disagree"):
        assert_call_site_defaults_match_schema_defaults(tmp_path, [tmp_path / "a.py"], _AppConfig, min_checked=1)
    assert_call_site_defaults_match_schema_defaults(
        tmp_path, [tmp_path / "a.py"], _AppConfig, min_checked=0,
        known_intentional_mismatches={("filters", "max_results"): "deliberate safety-net fallback"},
    )
