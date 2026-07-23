"""Unit tests for the CHANGELOG-promise / fix-cites-sensor cross-walk checks.

Real scratch CHANGELOG.md-shaped text, same no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.changelog_promise_parity import (
    DEFAULT_BULLET_PATTERN,
    DEFAULT_PROMISE_PATTERN,
    assert_changelog_bullets_satisfy_pattern,
    extract_section,
    find_unsatisfied_bullets,
)


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source.lstrip("\n"), encoding="utf-8")
    return p


# ---- extract_section ------------------------------------------------------

_SECTION_PATTERN = re.compile(r"^## \d{4}-\d{2}-\d{2}.*$", re.MULTILINE)


def test_extract_section_stops_at_second_heading():
    text = "intro\n## 2026-01-02 first\nbody one\n## 2026-01-01 second\nbody two\n"
    section = extract_section(text, _SECTION_PATTERN)
    assert "body one" in section
    assert "body two" not in section


def test_extract_section_runs_to_eof_when_only_one_heading():
    text = "intro\n## 2026-01-02 only\nbody\n"
    section = extract_section(text, _SECTION_PATTERN)
    assert "body" in section


def test_extract_section_empty_when_no_match():
    assert extract_section("no headings here\n", _SECTION_PATTERN) == ""


# ---- find_unsatisfied_bullets: self-contained satisfaction mode (mlframe-style) -----------

_FIX_PATTERN = re.compile(r"\bfix\(", re.IGNORECASE)
_SENSOR_PATTERN = re.compile(r"test_[a-zA-Z0-9_]+\.py")


def test_find_unsatisfied_bullets_self_contained_satisfied():
    text = "- **fix(scan): dedup rows** see test_dedup.py for the regression case\n"
    triggered, unsatisfied = find_unsatisfied_bullets(text, _FIX_PATTERN, satisfies_pattern=_SENSOR_PATTERN)
    assert len(triggered) == 1
    assert unsatisfied == []


def test_find_unsatisfied_bullets_self_contained_unsatisfied():
    text = "- **fix(scan): dedup rows** no test mentioned anywhere in this bullet\n"
    triggered, unsatisfied = find_unsatisfied_bullets(text, _FIX_PATTERN, satisfies_pattern=_SENSOR_PATTERN)
    assert len(triggered) == 1
    assert len(unsatisfied) == 1
    assert unsatisfied[0].title == "fix(scan): dedup rows"


def test_find_unsatisfied_bullets_non_triggering_bullet_ignored():
    text = "- **refactor(scan): rename var** no fix tag here\n"
    triggered, unsatisfied = find_unsatisfied_bullets(text, _FIX_PATTERN, satisfies_pattern=_SENSOR_PATTERN)
    assert triggered == []
    assert unsatisfied == []


# ---- find_unsatisfied_bullets: cross-document satisfaction mode (production_scrapers-style) --


def test_find_unsatisfied_bullets_cross_document_satisfied():
    text = "- **Some finding flagged for the final disposition report** needs an operator decision\n"
    other = ["## Deferred items\n- Some finding flagged for the final disposition report -- resolved via X\n"]
    triggered, unsatisfied = find_unsatisfied_bullets(text, DEFAULT_PROMISE_PATTERN, other_resolution_texts=other)
    assert len(triggered) == 1
    assert unsatisfied == []


def test_find_unsatisfied_bullets_cross_document_unsatisfied():
    text = "- **Some finding flagged for the final disposition report** needs an operator decision\n"
    other = ["## Deferred items\n- A totally different finding\n"]
    triggered, unsatisfied = find_unsatisfied_bullets(text, DEFAULT_PROMISE_PATTERN, other_resolution_texts=other)
    assert len(triggered) == 1
    assert len(unsatisfied) == 1


def test_find_unsatisfied_bullets_short_title_never_satisfied_via_cross_document():
    """A short/generic title must not spuriously match by coincidence in a large doc."""
    text = "- **X flagged for the final disposition report** short title\n"
    other = ["some text that happens to contain the letter X somewhere in it\n"]
    triggered, unsatisfied = find_unsatisfied_bullets(text, DEFAULT_PROMISE_PATTERN, other_resolution_texts=other, min_title_len=12)
    assert len(triggered) == 1
    assert len(unsatisfied) == 1  # "X" (len 1) never satisfies via cross-document mode


# ---- assert_changelog_bullets_satisfy_pattern ------------------------------


def test_assert_changelog_bullets_satisfy_pattern_passes(tmp_path: Path):
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

- **fix(scan): dedup rows** covered by test_dedup.py
""")
    assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN)


def test_assert_changelog_bullets_satisfy_pattern_fails(tmp_path: Path):
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

- **fix(scan): dedup rows** no sensor reference anywhere
""")
    with pytest.raises(pytest.fail.Exception, match="have no matching resolution"):
        assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN)


def test_assert_changelog_bullets_satisfy_pattern_skips_when_no_triggers(tmp_path: Path):
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

- **refactor: rename var** nothing to trigger on
""")
    with pytest.raises(pytest.skip.Exception):
        assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN)


def test_assert_changelog_bullets_satisfy_pattern_soft_threshold_tolerates_a_few(tmp_path: Path):
    """Mirrors mlframe's 0.15 threshold: 1 unsatisfied out of 5 (20%... wait, use 10 for exact 10%) stays under threshold."""
    bullets = "\n".join(f"- **fix(scan): item {i}** covered by test_item_{i}.py" for i in range(9))
    bullets += "\n- **fix(scan): item 9** no sensor mentioned\n"
    changelog = _write(tmp_path, "CHANGELOG.md", f"# Changelog\n\n{bullets}\n")
    # 1/10 = 10%, under the 15% threshold -- must pass.
    assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN, max_unsatisfied_fraction=0.15)


def test_assert_changelog_bullets_satisfy_pattern_soft_threshold_fails_when_exceeded(tmp_path: Path):
    bullets = "\n".join(f"- **fix(scan): item {i}** no sensor mentioned at all\n" for i in range(3))
    bullets += "- **fix(scan): item 3** covered by test_item.py\n"
    changelog = _write(tmp_path, "CHANGELOG.md", f"# Changelog\n\n{bullets}\n")
    # 3/4 = 75%, way over 15% -- must fail.
    with pytest.raises(pytest.fail.Exception):
        assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN, max_unsatisfied_fraction=0.15)


def test_assert_changelog_bullets_satisfy_pattern_cross_document_mode_via_disposition_file(tmp_path: Path):
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

- **Prefetch reconciliation extraction flagged for the final disposition report** deferred
""")
    _write(tmp_path, "DISPOSITION.md", """
# Disposition

- Prefetch reconciliation extraction flagged for the final disposition report -- deliberately not done
""")
    assert_changelog_bullets_satisfy_pattern(
        changelog,
        DEFAULT_PROMISE_PATTERN,
        other_resolution_paths=[tmp_path / "DISPOSITION.md"],
    )


def test_assert_changelog_bullets_satisfy_pattern_missing_other_path_is_skipped_not_crashed(tmp_path: Path):
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

- **Something flagged for the final disposition report** deferred
""")
    with pytest.raises(pytest.fail.Exception):
        assert_changelog_bullets_satisfy_pattern(
            changelog,
            DEFAULT_PROMISE_PATTERN,
            other_resolution_paths=[tmp_path / "DOES_NOT_EXIST.md"],
        )


def test_assert_changelog_bullets_satisfy_pattern_section_scoping(tmp_path: Path):
    section_pattern = re.compile(r"^## \d{4}-\d{2}-\d{2}.*$", re.MULTILINE)
    changelog = _write(tmp_path, "CHANGELOG.md", """
# Changelog

## 2026-02-01 newer entries

- **fix(scan): newer item** covered by test_newer.py

## 2026-01-01 older entries (pre-convention)

- **fix(scan): older item** no sensor here, predates the convention
""")
    # Scoped to only the FIRST matching section -- the older, unsatisfied bullet is out of scope.
    assert_changelog_bullets_satisfy_pattern(changelog, _FIX_PATTERN, _SENSOR_PATTERN, section_pattern=section_pattern)


def test_assert_changelog_bullets_satisfy_pattern_default_bullet_pattern_matches_real_shape():
    text = "- **Title with details** first line of body\n  continuation line indented\n- **Next bullet**\n"
    bullets = DEFAULT_BULLET_PATTERN.findall(text)
    assert len(bullets) == 2
    assert "continuation line indented" in bullets[0]
