"""``config_getattr_default_parity`` reports the fallbacks that contradict the schema and nothing else."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from py_ci_shared.config_getattr_default_parity import (
    assert_getattr_defaults_match_schema,
    find_getattr_default_mismatches,
    schema_field_defaults,
)


class Schema(BaseModel):
    """A small stand-in config."""

    enabled: bool = True
    threshold: float = 0.5
    strategy: str = "stratified"
    required_field: str
    built: list = Field(default_factory=list)


class Other(BaseModel):
    """A second config that disagrees with the first about one shared field."""

    enabled: bool = False
    only_here: int = 7


def _write(tmp_path, source, name="m.py"):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_contradicting_fallback_is_reported(tmp_path):
    """The shipped shape: the call site's literal says one thing, the config says another."""
    p = _write(tmp_path, "def f(cfg):\n    return getattr(cfg, 'threshold', 0.9)\n")
    found = find_getattr_default_mismatches([p], tmp_path, [Schema])
    assert [(g.field, g.literal, g.declared) for g in found] == [("threshold", 0.9, 0.5)]


def test_an_agreeing_fallback_and_a_computed_one_are_left_alone(tmp_path):
    """A literal equal to the default, and a fallback that is not a literal at all, are both fine."""
    p = _write(tmp_path, "def f(cfg, other):\n    a = getattr(cfg, 'threshold', 0.5)\n    b = getattr(cfg, 'strategy', other.strategy)\n    return a, b\n")
    assert find_getattr_default_mismatches([p], tmp_path, [Schema]) == []


def test_fields_the_schema_does_not_declare_are_ignored(tmp_path):
    """An unknown field, a required one and a default_factory field have no declared default to contradict."""
    p = _write(tmp_path, "def f(cfg):\n    return getattr(cfg, 'unknown', 1), getattr(cfg, 'required_field', 'x'), getattr(cfg, 'built', [])\n")
    assert find_getattr_default_mismatches([p], tmp_path, [Schema]) == []
    assert "required_field" not in schema_field_defaults([Schema]) and "built" not in schema_field_defaults([Schema])


def test_a_field_two_schemas_disagree_about_is_dropped(tmp_path):
    """``enabled`` means different things in the two configs, so no site can be judged against either."""
    p = _write(tmp_path, "def f(cfg):\n    return getattr(cfg, 'enabled', False)\n")
    assert find_getattr_default_mismatches([p], tmp_path, [Schema, Other]) == []
    assert find_getattr_default_mismatches([p], tmp_path, [Schema])[0].field == "enabled"


def test_only_config_shaped_receivers_count(tmp_path):
    """A receiver the caller did not name is somebody else's object, even when the field names collide."""
    p = _write(tmp_path, "def f(row, my_config):\n    return getattr(row, 'threshold', 0.9), getattr(my_config, 'threshold', 0.9)\n")
    found = find_getattr_default_mismatches([p], tmp_path, [Schema])
    assert len(found) == 1
    assert find_getattr_default_mismatches([p], tmp_path, [Schema], receiver_names=frozenset({"cfg"}), receiver_suffixes=()) == []


def test_the_assert_names_the_site_and_honours_the_allowlist(tmp_path):
    """The failure names field, literal and declared default; an allowed field passes with its reason."""
    p = _write(tmp_path, "def f(cfg):\n    return getattr(cfg, 'threshold', 0.9)\n")
    with pytest.raises(AssertionError, match="threshold=0.9 vs the config's 0.5"):
        assert_getattr_defaults_match_schema([p], tmp_path, [Schema])
    assert_getattr_defaults_match_schema([p], tmp_path, [Schema], allowed={"threshold": "the gate is deliberately stricter"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_getattr_defaults_match_schema([p], tmp_path, [Schema], allowed={"threshold": ""})


def test_a_stale_allowlist_entry_and_an_empty_scan_are_rejected(tmp_path):
    """An allowed field whose sites now agree, and a scan that lost its files, both fail."""
    p = _write(tmp_path, "def f(cfg):\n    return getattr(cfg, 'threshold', 0.5)\n")
    with pytest.raises(AssertionError, match="now agree"):
        assert_getattr_defaults_match_schema([p], tmp_path, [Schema], allowed={"threshold": "was off once"})
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_getattr_defaults_match_schema([], tmp_path, [Schema], min_files=1)
