"""`checkpoint_isolation` must catch a state path inside the checkout, and name it usefully.

The failure message is half the value here. A test that says "a path was wrong" sends a reader
looking; one that names the constant and says what to do about it is actionable at 3am.
"""

from __future__ import annotations

import pytest

from py_ci_shared.checkpoint_isolation import assert_outside, offending_state_paths


class TestAssertOutside:
    def test_a_redirected_path_passes(self, tmp_path):
        elsewhere = tmp_path / "isolated" / "state.json"

        assert_outside(elsewhere, tmp_path / "repo", what="_STATE_FILE")

    def test_a_path_inside_the_checkout_fails(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "checkpoints").mkdir(parents=True)

        with pytest.raises(AssertionError) as excinfo:
            assert_outside(repo / "checkpoints" / "state.json", repo, what="_DISAPPEARANCE_FILE")

        message = str(excinfo.value)
        assert "_DISAPPEARANCE_FILE" in message, "the message must name the constant, not just the path"
        assert "NEXT test reads" in message, "the message must say why it matters, not only that it is wrong"

    def test_the_repo_root_itself_counts_as_inside(self, tmp_path):
        """A file written straight into the checkout root is the same defect as one under a
        subdirectory, and `is_relative_to` says so -- pinned because an earlier version of this
        check compared string prefixes and let the root through."""
        with pytest.raises(AssertionError):
            assert_outside(tmp_path / "state.json", tmp_path, what="_STATE")


class TestOffendingStatePaths:
    def test_it_reports_every_offender_not_just_the_first(self, tmp_path):
        """One failure per run is how a sweep like this gets abandoned half-done: the reader fixes
        one, re-runs, finds another, and stops before the end."""
        repo = tmp_path / "repo"
        repo.mkdir()

        problems = offending_state_paths(
            {
                "_A": repo / "a.json",
                "_B": repo / "logs" / "b.jsonl",
                "_C": tmp_path / "outside" / "c.json",
            },
            repo,
        )

        assert len(problems) == 2
        assert any("_A" in p for p in problems) and any("_B" in p for p in problems)
        assert not any("_C" in p for p in problems)

    def test_a_fully_redirected_set_is_empty(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        assert offending_state_paths({"_A": tmp_path / "tmp" / "a.json"}, repo) == []
