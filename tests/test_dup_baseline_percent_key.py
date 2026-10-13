"""dup_baseline_percent_key: a pair listed twice under two percentages is found; one entry per pair, or a bare key, is not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.dup_baseline_percent_key import (
    assert_no_percent_only_duplicate_entries,
    find_percent_only_duplicates,
    strip_similarity_percent,
)


@pytest.mark.parametrize(
    ("raw", "bare"),
    [
        ("a/x.py::f ~ b/y.py::g (87.5%)", "a/x.py::f ~ b/y.py::g"),
        ("a/x.py::f ~ b/y.py::g (92%)", "a/x.py::f ~ b/y.py::g"),
        ("a/x.py::f ~ b/y.py::g", "a/x.py::f ~ b/y.py::g"),
        ("a/x.py::f (5 lines)", "a/x.py::f (5 lines)"),
    ],
)
def test_normalizer_strips_only_a_similarity_percentage(raw: str, bare: str) -> None:
    assert strip_similarity_percent(raw) == bare


def test_the_same_pair_at_two_percentages_is_one_finding_keyed_without_the_percent(tmp_path: Path) -> None:
    baseline = tmp_path / "duplicate_bodies.json"
    entries = {"a/x.py::f ~ b/y.py::g (87.5%)": "n", "a/x.py::f ~ b/y.py::g (87.6%)": "n"}
    baseline.write_text(json.dumps({"accepted": entries}), encoding="utf-8")
    found = find_percent_only_duplicates(baseline)
    assert list(found) == ["a/x.py::f ~ b/y.py::g"]
    assert "listed 2 times" in found["a/x.py::f ~ b/y.py::g"]


def test_distinct_pairs_and_bare_dict_shape_are_not_findings(tmp_path: Path) -> None:
    baseline = tmp_path / "duplicate_bodies.json"
    entries = {"_comment": "x", "a/x.py::f ~ b/y.py::g (87.5%)": "n", "a/x.py::f ~ c/z.py::h (80.0%)": "n"}
    baseline.write_text(json.dumps(entries), encoding="utf-8")
    assert find_percent_only_duplicates(baseline) == {}


def test_a_missing_duplicate_baseline_is_an_error_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_percent_only_duplicates(tmp_path / "absent.json")


def test_the_assert_fails_on_a_doubled_pair_and_passes_once_it_is_accepted(tmp_path: Path) -> None:
    dup = tmp_path / "tool" / "meta" / "baselines" / "duplicate_bodies.json"
    dup.parent.mkdir(parents=True)
    entries = {"a/x.py::f ~ b/y.py::g (87.5%)": "n", "a/x.py::f ~ b/y.py::g (87.6%)": "n"}
    dup.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_no_percent_only_duplicate_entries(tmp_path, duplicate_baseline_path="tool/meta/baselines/duplicate_bodies.json")
    accepted = {"a/x.py::f ~ b/y.py::g": "listed twice by two re-measures; the next refresh keeps one"}
    (dup.parent / "dup_baseline_percent_key.json").write_text(json.dumps({"accepted": accepted}), encoding="utf-8")
    assert_no_percent_only_duplicate_entries(tmp_path, duplicate_baseline_path="tool/meta/baselines/duplicate_bodies.json")
