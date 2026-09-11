"""The LOC ratchet turns one way: every rule of `loc_budget.ratchet_problems`, on inputs chosen to hit it.

Driving the assertion over a real tree could never show the drained-entry rule has teeth, because
nothing is drained in any tree today; that is how production_scrapers' version of this rule sat as a
stderr line in green runs until audit 2026-09-05 TEST-7. These drive the pure function instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.loc_budget import DEFAULT_GROWTH_SLACK as SLACK
from py_ci_shared.loc_budget import assert_no_new_oversized_file, ratchet_problems


class TestTheRules:
    def test_a_new_oversized_file_is_reported(self):
        assert ratchet_problems({}, {"new.py": 1500})

    def test_growth_past_slack_is_reported(self):
        assert ratchet_problems({"a.py": 1100}, {"a.py": 1100 + SLACK + 1})

    def test_growth_within_slack_is_tolerated(self):
        assert ratchet_problems({"a.py": 1100}, {"a.py": 1100 + SLACK}) == []

    def test_a_shrunk_file_is_reported(self):
        """The case that existed: 1136 -> 1021 left 115 lines of unjustified headroom."""
        assert ratchet_problems({"a.py": 1136}, {"a.py": 1021})

    def test_shrinkage_within_slack_is_tolerated(self):
        assert ratchet_problems({"a.py": 1100}, {"a.py": 1100 - SLACK}) == []

    def test_a_drained_file_is_reported(self):
        """This used to be a stderr line in a passing run."""
        assert ratchet_problems({"gone.py": 1200}, {})

    def test_an_unchanged_tree_is_silent(self):
        """The guard on all of the above: a rule set that reports everything proves nothing."""
        assert ratchet_problems({"a.py": 1100}, {"a.py": 1100}) == []

    def test_one_way_false_keeps_only_the_original_two_rules(self):
        assert ratchet_problems({"a.py": 1136, "gone.py": 1200}, {"a.py": 1021}, one_way=False) == []
        assert ratchet_problems({}, {"new.py": 1500}, one_way=False)


def test_the_assertion_applies_the_one_way_rules_by_default(tmp_path):
    root = tmp_path
    big = root / "big.py"
    big.write_text("x = 1\n" * 1021, encoding="utf-8")
    baseline = root / "_loc_over_1k_baseline.json"
    baseline.write_text('{"big.py": 1136}', encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match="SHRANK"):
        assert_no_new_oversized_file([big], root, baseline)
    assert_no_new_oversized_file([big], root, baseline, one_way=False)
