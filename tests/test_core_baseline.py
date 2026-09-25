"""`_core.baseline`: multiset, missing fails, NEEDS-JUSTIFICATION rejected, atomic byte-stable writes, old formats."""

from __future__ import annotations

import json
import os

import pytest

from py_ci_shared._core import UNJUSTIFIED_MARKER, Baseline, BaselineError, Finding, atomic_write_text
from py_ci_shared._core import baseline as baseline_mod


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


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


class TestShrinkOnlyRefresh:
    """A refresh drops what no longer fires; adding, raising or seeding needs the growth opt-in."""

    @pytest.fixture(autouse=True)
    def _no_grow(self, monkeypatch):
        monkeypatch.delenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", raising=False)

    def _base(self, tmp_path, entries):
        b = Baseline(tmp_path / "b.json", gate="g")
        if entries is not None:
            b.save(entries, {k: "why" for k in entries})
        return b

    def test_refresh_drops_stale_entries_and_lowers_counts_without_opt_in(self, tmp_path):
        b = self._base(tmp_path, {"k1": 3, "gone": 1})
        outcome = b.enforce(["k1"], refresh=True)
        assert outcome.refreshed and outcome.ok
        counts, notes = b.load()
        assert dict(counts) == {"k1": 1} and notes == {"k1": "why"}

    def test_refresh_with_a_new_violation_fails_names_it_and_still_prunes(self, tmp_path):
        b = self._base(tmp_path, {"k1": 1, "gone": 1})
        outcome = b.enforce(["k1", "k1", "fresh"], refresh=True)
        assert not outcome.ok and not outcome.refreshed
        assert sorted(outcome.new) == ["fresh", "k1"]
        assert "fresh  (new: 1)" in outcome.message and "k1  (1 -> 2)" in outcome.message
        assert "PY_CI_SHARED_REFRESH_ALLOW_GROW=1" in outcome.message and "--py-ci-refresh-grow" in outcome.message
        assert dict(b.load()[0]) == {"k1": 1}, "the removal lands, the growth does not"

    @pytest.mark.parametrize("how", ["kwarg", "env"])
    def test_the_opt_in_lets_it_grow(self, tmp_path, monkeypatch, how):
        b = self._base(tmp_path, {"k1": 1})
        if how == "env":
            monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
        outcome = b.enforce(["k1", "k1", "fresh"], refresh=True, grow=True if how == "kwarg" else None)
        assert outcome.refreshed
        assert dict(b.load()[0]) == {"k1": 2, "fresh": 1}

    def test_seeding_a_missing_baseline_needs_the_opt_in(self, tmp_path):
        b = self._base(tmp_path, None)
        outcome = b.enforce(["k1"], refresh=True)
        assert outcome.new == ["k1"] and "does not exist, and seeding it" in outcome.message
        assert not b.exists(), "a refused seeding writes nothing, so the next run still fails as missing"
        assert b.enforce(["k1"], refresh=True, grow=True).refreshed and b.exists()

    def test_an_empty_baseline_may_always_be_seeded(self, tmp_path):
        b = self._base(tmp_path, None)
        assert b.enforce([], refresh=True).refreshed and b.exists()

    def test_baseline_ratchet_regenerate_is_shrink_only_too(self, tmp_path):
        from py_ci_shared._core import BaselineGrowthError
        from py_ci_shared.baseline_ratchet import Baseline as RatchetBaseline

        b = RatchetBaseline("rule", directory=str(tmp_path))
        b.regenerate({"a": "x", "b": "y"}, grow=True)
        with pytest.raises(BaselineGrowthError, match=r"c  \(new: 1\)"):
            b.regenerate({"a": "x", "c": "z"})
        assert b.load() == {"a": "x"}

    def test_write_ratchet_slack_keeps_the_lower_ceiling_without_failing(self, tmp_path):
        from py_ci_shared._core import dump_json, write_ratchet

        kept = write_ratchet(tmp_path / "c.json", {"f": 105}, gate="g", previous={"f": 100}, render=dump_json, slack=10)
        assert kept == {"f": 100}
