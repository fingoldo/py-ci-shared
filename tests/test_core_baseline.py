"""`_core.baseline`: multiset, missing fails, NEEDS-JUSTIFICATION rejected, atomic byte-stable writes, old formats."""

from __future__ import annotations

import json
import os

import pytest

from py_ci_shared._core import UNJUSTIFIED_MARKER, Baseline, BaselineError, Finding, atomic_write_text
from py_ci_shared._core import baseline as baseline_mod


def _f(msg: str, line: int = 1, path: str = "a.py") -> Finding:
    return Finding(path, line, "rule", msg)


class TestFinding:
    def test_the_key_ignores_the_line_and_is_stable(self):
        assert _f("x", 1).key == _f("x", 99).key == "rule::a.py::x"
        assert _f("x").key != _f("y").key
        assert Finding("a.py", 1, "r", "m", key="custom").key == "custom"

    def test_render(self):
        assert _f("boom", 7).render() == "a.py:7: [rule] boom"


class TestMultiset:
    def test_a_duplicate_finding_is_not_absorbed_by_one_entry(self, tmp_path):
        b = Baseline(tmp_path / "b.json")
        b.enforce([_f("x", 1)], refresh=True)
        outcome = b.enforce([_f("x", 1), _f("x", 2)])
        assert not outcome.ok and outcome.new == ["rule::a.py::x"]
        b.enforce([_f("x", 1), _f("x", 2)], refresh=True)
        assert b.enforce([_f("x", 1), _f("x", 2)]).ok  # control: both accepted

    def test_fewer_than_accepted_is_stale_not_a_failure(self, tmp_path):
        b = Baseline(tmp_path / "b.json")
        b.enforce([_f("x"), _f("x")], refresh=True)
        outcome = b.enforce([_f("x")])
        assert outcome.ok and outcome.stale == ["rule::a.py::x"]
        assert b.enforce([_f("x"), _f("x")]).stale == []

    def test_list_format_duplicates_count(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps(["k", "k", "j"]), encoding="utf-8")
        counts, notes = Baseline(p).load()
        assert counts == {"k": 2, "j": 1} and notes == {}


class TestMissingAndRefresh:
    def test_a_missing_baseline_fails_naming_the_refresh_command(self, tmp_path):
        b = Baseline(tmp_path / "b.json", refresh_command="pytest --refresh-x")
        outcome = b.enforce([])
        assert outcome.missing and not outcome.ok and "pytest --refresh-x" in outcome.message
        assert not b.path.exists()
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            outcome.raise_for_pytest()

    def test_refresh_writes_and_skips(self, tmp_path):
        b = Baseline(tmp_path / "sub" / "b.json", gate="g")
        outcome = b.enforce([_f("x")], refresh=True)
        assert outcome.refreshed and b.path.exists()
        with pytest.raises(pytest.skip.Exception):
            outcome.raise_for_pytest()
        assert b.enforce([_f("x")]).ok

    def test_refresh_keeps_notes_and_marks_new_entries_when_asked(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"rule::a.py::x": "kept on purpose"}), encoding="utf-8")
        b = Baseline(p, new_note=UNJUSTIFIED_MARKER + ": explain")
        b.enforce([_f("x"), _f("y")], refresh=True)
        _, notes = b.load()
        assert notes == {"rule::a.py::x": "kept on purpose", "rule::a.py::y": UNJUSTIFIED_MARKER + ": explain"}

    def test_a_malformed_baseline_is_an_error_not_empty(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(BaselineError, match="unreadable"):
            Baseline(p).enforce([])
        p.write_text("42", encoding="utf-8")
        with pytest.raises(BaselineError, match="list or object"):
            Baseline(p).load()


class TestUnjustified:
    def test_needs_justification_entries_fail_a_normal_run(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"rule::a.py::x": f"{UNJUSTIFIED_MARKER}: survivor"}), encoding="utf-8")
        outcome = Baseline(p).enforce([_f("x")])
        assert not outcome.ok and outcome.unjustified == ["rule::a.py::x"] and outcome.new == []
        p.write_text(json.dumps({"rule::a.py::x": "unobservable constant"}), encoding="utf-8")
        assert Baseline(p).enforce([_f("x")]).ok

    def test_the_marker_in_the_middle_of_a_note_is_not_rejected(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"k": f"was {UNJUSTIFIED_MARKER}, now reasoned"}), encoding="utf-8")
        assert Baseline(p).enforce(["k"]).ok

    def test_baseline_ratchet_rejects_the_marker_too(self, tmp_path):
        """MT-1: mutation_teeth wrote the marker on refresh, and baseline_ratchet.enforce never looked at it."""
        from py_ci_shared.baseline_ratchet import Baseline as RatchetBaseline

        b = RatchetBaseline("m", directory=str(tmp_path))
        b.save({"k": f"{UNJUSTIFIED_MARKER}: survivor"})
        assert b.enforce({"k": "x"}, label="l", guidance="g") == 1
        b.save({"k": "cannot be observed: constant folded"})
        assert b.enforce({"k": "x"}, label="l", guidance="g") == 0


class TestFormatsAndWrites:
    @pytest.mark.parametrize(
        "payload, expected",
        [
            (["a", "b"], {"a": 1, "b": 1}),
            ({"a": "note", "_comment": "meta"}, {"a": 1}),
            ({"accepted": {"a": "n"}, "_comment": "c"}, {"a": 1}),
            ({"a": 3}, {"a": 3}),
            ({"schema": 1, "gate": "g", "entries": {"a": {"count": 2, "note": ""}}}, {"a": 2}),
            ([["p.py", "mod"]], {"p.py|mod": 1}),
        ],
    )
    def test_every_committed_format_reads(self, tmp_path, payload, expected):
        p = tmp_path / "b.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert dict(Baseline(p).load()[0]) == expected

    def test_orjson_written_bytes_read(self, tmp_path):
        orjson = pytest.importorskip("orjson")
        p = tmp_path / "b.json"
        p.write_bytes(orjson.dumps(sorted(["k2", "k1"]), option=orjson.OPT_INDENT_2))
        assert dict(Baseline(p).load()[0]) == {"k1": 1, "k2": 1}

    def test_writes_are_byte_stable_sorted_lf_utf8(self, tmp_path):
        b = Baseline(tmp_path / "b.json", gate="g")
        b.enforce([_f("z"), _f("a"), _f("a")], refresh=True)
        first = b.path.read_bytes()
        b.enforce([_f("a"), _f("z"), _f("a")], refresh=True)
        assert b.path.read_bytes() == first
        assert b"\r\n" not in first and first.endswith(b"\n")
        data = json.loads(first)
        assert list(data["entries"]) == ["rule::a.py::a", "rule::a.py::z"] and data["entries"]["rule::a.py::a"]["count"] == 2

    def test_atomic_write_leaves_no_tmp_files(self, tmp_path):
        target = tmp_path / "d" / "b.json"
        atomic_write_text(target, "one\n")
        atomic_write_text(target, "two\n")
        assert target.read_text(encoding="utf-8") == "two\n"
        assert sorted(p.name for p in target.parent.iterdir()) == ["b.json"]

    def test_a_failed_write_keeps_the_old_file_and_cleans_up(self, tmp_path, monkeypatch):
        target = tmp_path / "b.json"
        atomic_write_text(target, "old\n")

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(baseline_mod.os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            atomic_write_text(target, "new\n")
        monkeypatch.undo()
        assert target.read_text(encoding="utf-8") == "old\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json"]
        assert not any(name.endswith(".tmp") for name in os.listdir(tmp_path))
