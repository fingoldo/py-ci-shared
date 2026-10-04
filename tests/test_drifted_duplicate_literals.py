"""drifted_duplicate_literals: one set of numbers restated in several modules, reproduced on three dashboard cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared import drifted_duplicate_literals as gate
from py_ci_shared.drifted_duplicate_literals import (
    RULE_CONSTANT,
    RULE_SET,
    RULE_THRESHOLD,
    assert_no_drifted_duplicate_literals,
    find_drifted_duplicate_literals,
)

NL = chr(10)


def _write(root: Path, rel: str, lines: list) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((NL.join(lines) + NL).encode("utf-8"))


def _find(root: Path, **kwargs) -> list:
    return find_drifted_duplicate_literals(root, use_git=False, **kwargs)


# 09.4: the last-viewed band cut-offs in the colour code, the tooltip and a verify script's SQL.
_COLOUR_CODE = [
    "def last_viewed_colour(hours):",
    "    if hours <= 20:",
    "        return 'green'",
    "    elif hours <= 70:",
    "        return 'amber'",
    "    elif hours <= 221:",
    "        return 'orange'",
    "    return 'red'",
]
_TOOLTIP = ["VIEWED_TOOLTIP = 'green up to 20h, amber 20-70h, orange 70-221h, red beyond'"]
_VERIFY_SQL = [
    "BAND_SQL = '''",
    "SELECT CASE WHEN h <= 20 THEN 'green' WHEN h <= 70 THEN 'amber' WHEN h <= 221 THEN 'orange' ELSE 'red' END",
    "FROM viewed",
    "'''",
]


def test_the_band_cutoffs_in_three_surfaces_are_one_finding(tmp_path: Path):
    _write(tmp_path, "views/_card_timing.py", _COLOUR_CODE)
    _write(tmp_path, "views/_card_context.py", _TOOLTIP)
    _write(tmp_path, "scripts/verify_sql.py", _VERIFY_SQL)
    found = _find(tmp_path)
    assert [f.rule for f in found] == [RULE_SET]
    message = found[0].message
    assert "(20, 70, 221)" in message and "3 modules" in message
    for where in ("views/_card_timing.py:2 (ladder)", "views/_card_context.py:1 (text)", "scripts/verify_sql.py:1 (text)"):
        assert where in message


def test_the_band_cutoffs_derived_from_one_tuple_are_clean(tmp_path: Path):
    """The fix: one tuple, the colour code unpacks it and the tooltip and SQL format it in."""
    _write(tmp_path, "views/_card_context.py", ["VIEWED_BANDS_H = (20, 70, 221)"])
    _write(
        tmp_path,
        "views/_card_timing.py",
        [
            "from ._card_context import VIEWED_BANDS_H",
            "def colour(hours):",
            "    g, a, o = VIEWED_BANDS_H",
            "    if hours <= g:",
            "        return 'green'",
            "    return 'red'",
        ],
    )
    _write(
        tmp_path,
        "scripts/verify_sql.py",
        ["from views._card_context import VIEWED_BANDS_H", "BAND_SQL = 'CASE WHEN h <= {} THEN 1 END'.format(*VIEWED_BANDS_H)"],
    )
    assert _find(tmp_path) == []


def test_the_score_caveat_quoted_in_three_surfaces_is_one_finding(tmp_path: Path):
    """09.2: 22.7% vs 26.0% over 1,652 postings, typed into three surfaces as prose."""
    _write(tmp_path, "views/_card.py", ["HOVER = 'The top fifth hired 22.7% against 26.0% for the bottom fifth, over 1,652 postings.'"])
    _write(tmp_path, "views/recommendations.py", ["def label(base):", "    return base + ' - fit, not a hire prediction (22.7% vs 26.0%)'"])
    _write(tmp_path, "views/scores.py", ["CAPTION = 'Measured over 1,652 postings: 22.7% vs 26.0%.'"])
    found = _find(tmp_path)
    assert [f.rule for f in found] == [RULE_SET]
    assert "(22.7, 26.0)" in found[0].message and "3 modules" in found[0].message


def test_the_score_caveat_from_one_set_of_constants_is_clean(tmp_path: Path):
    _write(
        tmp_path,
        "views/_card.py",
        ["CAVEAT_N = 1652", "TOP_PCT = 22.7", "BOTTOM_PCT = 26.0", "HOVER = f'{TOP_PCT:.1f}% against {BOTTOM_PCT:.1f}% over {CAVEAT_N:,}'"],
    )
    _write(tmp_path, "views/recommendations.py", ["from ._card import TOP_PCT, BOTTOM_PCT", "LABEL = f'({TOP_PCT:.1f}% vs {BOTTOM_PCT:.1f}%)'"])
    assert _find(tmp_path) == []


def test_the_staleness_threshold_computed_on_three_tabs_is_one_finding(tmp_path: Path):
    """04.3/19.1: the 48 h rule written as its own comparison on every tab."""
    _write(tmp_path, "views/market.py", ["def stale(age_h):", "    return age_h > 48"])
    _write(tmp_path, "views/trends.py", ["def stale(row):", "    return row.age_hours >= 48"])
    _write(tmp_path, "views/health.py", ["def amber(rollup_age_h):", "    return rollup_age_h > 48"])
    found = _find(tmp_path)
    assert [f.rule for f in found] == [RULE_THRESHOLD]
    assert "48 compared against" in found[0].message and "3 modules" in found[0].message


def test_the_staleness_threshold_from_one_constant_is_clean(tmp_path: Path):
    _write(tmp_path, "freshness.py", ["STALE_AFTER_H = 48", "def stale(age_h):", "    return age_h > STALE_AFTER_H"])
    for tab in ("market", "trends", "health"):
        _write(tmp_path, f"views/{tab}.py", ["from freshness import stale", "def amber(age_h):", "    return stale(age_h)"])
    assert _find(tmp_path) == []


def test_two_modules_are_not_enough_for_a_threshold(tmp_path: Path):
    """A single number compared in two places is common; three modules is the configurable bar."""
    _write(tmp_path, "a.py", ["def f(age_h):", "    return age_h > 48"])
    _write(tmp_path, "b.py", ["def f(age_h):", "    return age_h > 48"])
    assert _find(tmp_path) == []
    assert [f.rule for f in _find(tmp_path, min_threshold_modules=2)] == [RULE_THRESHOLD]


def test_similarly_named_constants_with_one_value_are_reported(tmp_path: Path):
    _write(tmp_path, "views/market.py", ["_STALE_AFTER_H = 48"])
    _write(tmp_path, "views/trends.py", ["STALENESS_LIMIT_HOURS = 48"])
    found = _find(tmp_path)
    assert [f.rule for f in found] == [RULE_CONSTANT]
    assert "_STALE_AFTER_H" in found[0].message and "STALENESS_LIMIT_HOURS" in found[0].message


def test_unrelated_constants_sharing_a_value_are_not_reported(tmp_path: Path):
    """Two names with nothing but a unit in common are two settings that happen to agree today."""
    _write(tmp_path, "a.py", ["RETRY_DELAY_S = 45"])
    _write(tmp_path, "b.py", ["PAGE_RENDER_TIMEOUT_S = 45"])
    assert _find(tmp_path) == []


def test_common_numbers_shapes_and_versions_are_quiet(tmp_path: Path):
    """The noise the rule must not raise: round numbers, call-argument shapes, version tuples, dates, docstrings."""
    for name in ("a.py", "b.py"):
        _write(
            tmp_path,
            name,
            [
                '"""Bands were (20, 70, 221) in the audit of 2026-08-31."""',
                "import sys",
                "SIZES = (10, 100, 1000)",
                "VERSION = (3, 11, 7)",
                "fig = make(figsize=(13, 9))",
                "OK = sys.version_info >= (3, 11)",
                "STAMP = 'built 2026-08-31 at 14:30, release 1.20.3'",
            ],
        )
    assert _find(tmp_path) == []


def test_a_set_inside_one_module_is_not_a_duplicate(tmp_path: Path):
    _write(tmp_path, "a.py", [*_COLOUR_CODE, *_TOOLTIP])
    assert _find(tmp_path) == []


def test_ignore_is_configurable(tmp_path: Path):
    _write(tmp_path, "views/_card_timing.py", _COLOUR_CODE)
    _write(tmp_path, "views/_card_context.py", _TOOLTIP)
    assert _find(tmp_path)
    assert _find(tmp_path, ignore={20, 70, 221}) == []


def test_test_files_are_skipped_by_default(tmp_path: Path):
    """A test restating a figure pins it; that is not a second source of truth."""
    _write(tmp_path, "views/_card_timing.py", _COLOUR_CODE)
    _write(tmp_path, "tests/test_bands.py", _TOOLTIP)
    assert _find(tmp_path) == []
    assert len(_find(tmp_path, include_tests=True)) == 1


def test_the_assertion_names_every_site(tmp_path: Path):
    _write(tmp_path, "views/_card_timing.py", _COLOUR_CODE)
    _write(tmp_path, "views/_card_context.py", _TOOLTIP)
    with pytest.raises(pytest.fail.Exception) as exc:
        assert_no_drifted_duplicate_literals(tmp_path, use_git=False)
    assert "views/_card_timing.py:2" in str(exc.value) and "views/_card_context.py:1" in str(exc.value)


def test_a_refreshed_baseline_needs_a_reason_before_it_passes(tmp_path: Path, monkeypatch):
    """A baseline entry must say why the modules may each write the numbers; the refresh writes the marker, not a reason."""
    src = tmp_path / "src"
    _write(src, "views/_card_timing.py", _COLOUR_CODE)
    _write(src, "views/_card_context.py", _TOOLTIP)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    with pytest.raises(pytest.skip.Exception):
        assert_no_drifted_duplicate_literals(src, use_git=False, baseline_path=baseline, refresh=True)
    monkeypatch.delenv("PY_CI_SHARED_REFRESH_ALLOW_GROW")
    with pytest.raises(pytest.fail.Exception, match="NEEDS-JUSTIFICATION"):
        assert_no_drifted_duplicate_literals(src, use_git=False, baseline_path=baseline, refresh=False)
    data = json.loads(baseline.read_text(encoding="utf-8"))
    for entry in data["entries"].values():
        entry["note"] = "the tooltip is frozen copy for the 2026-08 release"
    baseline.write_text(json.dumps(data), encoding="utf-8")
    assert_no_drifted_duplicate_literals(src, use_git=False, baseline_path=baseline, refresh=False)
    _write(src, "scripts/verify_sql.py", _VERIFY_SQL)  # a third module changes the group's key: it is new
    with pytest.raises(pytest.fail.Exception, match="verify_sql"):
        assert_no_drifted_duplicate_literals(src, use_git=False, baseline_path=baseline, refresh=False)


def test_an_unparsable_file_fails_unless_allowed(tmp_path: Path):
    _write(tmp_path, "ok.py", ["X = 1"])
    _write(tmp_path, "broken.py", ["def f(:"])
    with pytest.raises(pytest.fail.Exception, match=r"broken\.py"):
        assert_no_drifted_duplicate_literals(tmp_path, use_git=False)
    assert_no_drifted_duplicate_literals(tmp_path, use_git=False, allow_unparsed=True)


def test_an_empty_corpus_fails_the_floor(tmp_path: Path):
    with pytest.raises(pytest.fail.Exception, match="parsed"):
        assert_no_drifted_duplicate_literals(tmp_path, use_git=False)


def test_teeth_the_ladder_is_what_finds_the_band_cutoffs(tmp_path: Path, monkeypatch):
    """Revert the core condition (an if/elif chain's thresholds are a set): the 09.4 case must then go unreported,
    since its only code site is the colour ladder and prose alone never starts a group of round numbers."""
    _write(tmp_path, "views/_card_timing.py", _COLOUR_CODE)
    _write(tmp_path, "views/_card_context.py", _TOOLTIP)
    assert _find(tmp_path)
    calls = []

    def no_ladder(node, ignore):
        calls.append(node)
        return [], set()

    monkeypatch.setattr(gate, "_ladder_values", no_ladder)
    assert _find(tmp_path) == []
    assert calls, "the substitution was not applied: _ladder_values was never called"


def test_teeth_name_similarity_is_what_finds_the_threshold(tmp_path: Path, monkeypatch):
    """Revert name similarity: the 48 h case must then go unreported."""
    for tab, name in (("market", "age_h"), ("trends", "age_hours"), ("health", "rollup_age_h")):
        _write(tmp_path, f"views/{tab}.py", [f"def stale({name}):", f"    return {name} > 48"])
    assert _find(tmp_path)
    calls = []

    def never(a, b, generic):
        calls.append((a, b))
        return False

    monkeypatch.setattr(gate, "_similar", never)
    assert _find(tmp_path) == []
    assert calls, "the substitution was not applied: _similar was never called"
