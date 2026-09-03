"""The trend report reads every baseline shape and tells a moved rule from an unmoved one."""

from __future__ import annotations

import json
import subprocess

import pytest

from py_ci_shared.baseline_trend import count_entries, history, report


def test_counts_the_python_ratchet_shape():
    text = json.dumps({"_comment": "x", "accepted": {"a": "why", "b": "why"}})
    assert count_entries(text) == 2


def test_counts_both_dart_sweep_shapes():
    assert count_entries(json.dumps({"entries": ["a", "b", "c"]})) == 3
    assert count_entries(json.dumps({"entries": {"a": "why", "b": ""}})) == 2


def test_counts_the_module_size_shape_by_its_own_key():
    text = json.dumps({"limit": 600, "ceilings": {"lib/a.dart": 700}})
    assert count_entries(text) == 1


def test_a_file_that_is_not_a_baseline_counts_as_nothing():
    """Returning 0 would put a stray file in the report as a rule with no debt."""
    assert count_entries("not json at all") is None
    assert count_entries(json.dumps(["a", "list"])) is None
    assert count_entries(json.dumps({"something": "else"})) is None


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tool" / "meta" / "baselines").mkdir(parents=True)
    return tmp_path, git


def write(path, entries):
    path.write_text(json.dumps({"accepted": {k: "" for k in entries}}), encoding="utf-8")


def test_history_reads_the_count_at_each_commit(repo):
    root, git = repo
    path = root / "tool" / "meta" / "baselines" / "rule.json"

    write(path, ["a", "b", "c"])
    git("add", "-A")
    git("commit", "-qm", "three")
    write(path, ["a"])
    git("add", "-A")
    git("commit", "-qm", "one")

    counts = [c for _, _, c in history(str(root), "tool/meta/baselines/rule.json", None)]
    assert counts == [3, 1], "oldest first, so a reader sees the direction"


def test_report_separates_a_rule_that_moved_from_one_that_did_not(repo):
    root, git = repo
    base = root / "tool" / "meta" / "baselines"

    write(base / "paid.json", ["a", "b"])
    write(base / "stuck.json", ["x"])
    git("add", "-A")
    git("commit", "-qm", "adopt both")
    write(base / "paid.json", [])
    write(base / "stuck.json", ["x"])
    git("add", "-A")
    git("commit", "-qm", "pay one down")

    everything = report(str(root), ["tool/meta/baselines"], None, False)
    assert any("paid" in line and "->" in line for line in everything)
    assert any("stuck" in line and "==" in line for line in everything)

    unmoved = report(str(root), ["tool/meta/baselines"], None, True)
    assert [line for line in unmoved if "stuck" in line]
    assert not [line for line in unmoved if "paid" in line], (
        "a rule that fell is not what this filter is for - it is the ones that never move that need a "
        "decision"
    )
