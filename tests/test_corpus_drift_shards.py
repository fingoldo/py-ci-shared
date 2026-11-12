"""The nightly corpus-drift job runs one shard per consumer repo and merges the shard snapshots before comparing.

The merge must give the same shape a single snapshot has, refuse a repo measured twice, and the shard selection must pick exactly the
corpus consumers, one per shard."""

from __future__ import annotations

import json

import pytest

from py_ci_shared import _consumers, corpus_drift


def _snapshot(path, repos, created="2026-11-11T03:00:00+00:00"):
    data = {"schema": corpus_drift.SCHEMA, "created": created, "repos": repos}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _repo(counts):
    return {"commit": "abc", "counts": counts, "errors": {}, "seconds": {}}


def test_merge_combines_shards_into_the_single_snapshot_shape(tmp_path):
    a = _snapshot(tmp_path / "a.json", {"mlframe": _repo({"f1": 3})}, created="2026-11-11T03:10:00+00:00")
    b = _snapshot(tmp_path / "b.json", {"pyutilz": _repo({"f1": 0})}, created="2026-11-11T03:20:00+00:00")
    merged = corpus_drift.merge_snapshots([a, b])
    assert merged["schema"] == corpus_drift.SCHEMA
    assert sorted(merged["repos"]) == ["mlframe", "pyutilz"]
    assert merged["repos"]["mlframe"]["counts"] == {"f1": 3}
    assert merged["created"] == "2026-11-11T03:20:00+00:00"


def test_merge_refuses_a_repo_measured_by_two_shards(tmp_path):
    a = _snapshot(tmp_path / "a.json", {"mlframe": _repo({"f1": 3})})
    b = _snapshot(tmp_path / "b.json", {"mlframe": _repo({"f1": 4})})
    with pytest.raises(ValueError, match="more than one snapshot"):
        corpus_drift.merge_snapshots([a, b])


def test_merge_refuses_a_foreign_schema(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "other", "repos": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        corpus_drift.merge_snapshots([bad])


def test_a_merged_snapshot_compares_like_a_single_one(tmp_path):
    previous = _snapshot(tmp_path / "prev.json", {"mlframe": _repo({"f1": 10})})
    shard = _snapshot(tmp_path / "shard.json", {"mlframe": _repo({"f1": 10})})
    merged = tmp_path / "merged.json"
    merged.write_text(json.dumps(corpus_drift.merge_snapshots([shard])), encoding="utf-8")
    assert corpus_drift.main(["compare", str(previous), str(merged)]) == 0


def test_corpus_names_lists_exactly_the_corpus_consumers(tmp_path, capsys):
    cfg = tmp_path / "consumers.toml"
    cfg.write_text(
        '[[consumer]]\nname = "a"\nrepo = "o/a"\nbranch = "master"\ncorpus = true\n\n'
        '[[consumer]]\nname = "b"\nrepo = "o/b"\nbranch = "master"\n\n'
        '[[consumer]]\nname = "c"\nrepo = "o/c"\nbranch = "master"\ncorpus = true\n',
        encoding="utf-8",
    )
    assert _consumers.main(["corpus-names", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out) == ["a", "c"]


def test_only_selects_one_consumer_for_a_shard(tmp_path, monkeypatch):
    cfg = tmp_path / "consumers.toml"
    cfg.write_text(
        '[[consumer]]\nname = "a"\nrepo = "o/a"\nbranch = "master"\nprivate = false\ncorpus = true\n\n'
        '[[consumer]]\nname = "c"\nrepo = "o/c"\nbranch = "master"\nprivate = false\ncorpus = true\n',
        encoding="utf-8",
    )
    seen = []

    def fake_checkout(consumers, dest, *, token=None):
        seen.extend(c.name for c in consumers)
        raise SystemExit(0)

    monkeypatch.setattr(_consumers, "checkout", lambda consumers, dest, token=None: _FakeResult(consumers, seen))
    rc = _consumers.main(["checkout", str(cfg), "--dest", str(tmp_path / "d"), "--repos-file", str(tmp_path / "r.toml"), "--only", "c"])
    assert rc in (0, 1)
    assert seen == ["c"]


class _FakeResult:
    def __init__(self, consumers, seen):
        seen.extend(c.name for c in consumers)
        self.skipped = []
        self.cloned = []
        self.failed = []

    def __getattr__(self, name):
        return []
