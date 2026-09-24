"""Unit tests for ignore_ratchet: a blocking gate's ignore list may only shrink."""

from __future__ import annotations

import json

import pytest

from py_ci_shared.ignore_ratchet import (
    assert_ignore_list_only_shrinks,
    precommit_arg_codes,
    ratchet_problems,
    ruff_counts,
    workflow_input_codes,
)


class TestRatchetProblems:
    def test_a_steady_list_is_clean(self):
        assert ratchet_problems(["C901", "B905"], {"C901": 3, "B905": 1}, {"C901": 3, "B905": 1}) == []

    def test_growth_under_the_ignore_fails(self):
        (p,) = ratchet_problems(["F401"], {"F401": 18}, {"F401": 17})
        assert "18 findings, up from 17" in p

    def test_zero_findings_means_the_code_must_leave_the_list(self):
        (p,) = ratchet_problems(["F401"], {"F401": 0}, {"F401": 17})
        assert "remove it from the ignore list" in p

    def test_one_finding_left_is_still_a_shrink_not_a_removal(self):
        """The boundary: 1 is a shrink to lock in, 0 is the code leaving the list."""
        (p,) = ratchet_problems(["F401"], {"F401": 1}, {"F401": 2})
        assert "down from 2" in p and "remove it" not in p

    def test_a_shrink_must_be_locked_in(self):
        (p,) = ratchet_problems(["C901"], {"C901": 2}, {"C901": 3})
        assert "refresh the baseline" in p

    def test_an_undeclared_ignore_fails(self):
        (p,) = ratchet_problems(["N818", "C901"], {"N818": 1, "C901": 3}, {"C901": 3})
        assert p.startswith("N818: ignored by the gate with no baseline entry")

    def test_a_baseline_entry_for_a_code_no_longer_ignored_fails(self):
        (p,) = ratchet_problems(["C901"], {"C901": 3}, {"C901": 3, "F401": 17})
        assert p.startswith("F401: in the baseline but no longer ignored")


class TestParsing:
    def test_workflow_input_codes(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text('jobs:\n  ruff-blocking:\n    uses: x/y@v1\n    with:\n      ignore: "C901, DTZ001,RUF012"\n', encoding="utf-8")
        assert workflow_input_codes(wf, job="ruff-blocking") == ["C901", "DTZ001", "RUF012"]

    def test_precommit_arg_codes_in_both_spellings(self, tmp_path):
        pc = tmp_path / ".pre-commit-config.yaml"
        pc.write_text(
            "repos:\n  - repo: x\n    hooks:\n      - id: ruff\n        alias: ps-ruff\n        args: ['--select', 'F,E9', '--ignore', 'F403,F405']\n"
            "      - id: other\n        args: ['--ignore=E501']\n",
            encoding="utf-8",
        )
        assert precommit_arg_codes(pc, hook="ps-ruff") == ["F403", "F405"]
        assert precommit_arg_codes(pc, hook="other") == ["E501"]
        with pytest.raises(LookupError):
            precommit_arg_codes(pc, hook="missing")


def test_ruff_counts_reads_the_real_tool(tmp_path):
    (tmp_path / "a.py").write_text("import os\nimport sys\n\nprint(sys.argv)\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    assert ruff_counts(tmp_path, ["F401", "E711"]) == {"E711": 0, "F401": 1}


def test_assert_refuses_an_empty_list(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="parsed as empty"):
        assert_ignore_list_only_shrinks([], {}, tmp_path / "b.json")


def test_assert_reports_against_the_committed_baseline(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"F401": 1}), encoding="utf-8")
    assert_ignore_list_only_shrinks(["F401"], {"F401": 1}, baseline)
    with pytest.raises(pytest.fail.Exception, match="up from 1"):
        assert_ignore_list_only_shrinks(["F401"], {"F401": 2}, baseline)


def test_a_prefix_ignore_counts_every_code_under_it(tmp_path):
    (tmp_path / "a.py").write_text("import os\nimport sys\n\n\ndef f(x):\n    return x == None\n", encoding="utf-8")
    counts = ruff_counts(tmp_path, ["F", "E711", "F401"])
    assert counts == {"E711": 1, "F": 2, "F401": 2}
    problems = ratchet_problems(["F"], counts, {"F": 2})
    assert problems == []
    assert "0 findings left" in ratchet_problems(["F"], {"F": 0}, {"F": 2})[0]


def test_a_file_ruff_cannot_parse_raises(tmp_path):
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    assert ruff_counts(tmp_path, ["F401"]) == {"F401": 1}
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"broken.py"):
        ruff_counts(tmp_path, ["F401"])
