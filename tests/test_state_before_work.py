"""Tests for py_ci_shared.state_before_work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.state_before_work import assert_state_before_work, find_state_before_work

BOM = b"\xef\xbb\xbf"
STAMP_FIRST = (
    "import time\n\nclass S:\n    def sweep(self):\n        self.last_full_sweep = time.monotonic()\n        rows = self.query_stale()\n        return rows\n"
)
QUERY_FIRST = (
    "import time\n\nclass S:\n    def sweep(self):\n        rows = self.query_stale()\n        self.last_full_sweep = time.monotonic()\n        return rows\n"
)
COUNTER_FIRST = "def flush(rows, db):\n    for row in rows:\n        processed += 1\n        db.insert(row)\n"


def _find(tmp_path: Path, src, **kw):
    (tmp_path / "m.py").write_bytes(src if isinstance(src, bytes) else src.encode("utf-8"))
    return find_state_before_work(tmp_path, use_git=False, **kw)


def test_reports_the_seeded_stamp(tmp_path):
    found = _find(tmp_path, STAMP_FIRST)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 5, "state-before-work")]
    assert found[0].message.startswith("sweep: stamp last_full_sweep is written before query_stale(...) (line 6)")


def test_reports_the_seeded_counter(tmp_path):
    found = _find(tmp_path, COUNTER_FIRST)
    assert [f.line for f in found] == [3]
    assert found[0].message.startswith("flush: counter processed is written before insert(...) (line 4)")


def test_work_then_state_is_clean(tmp_path):
    assert _find(tmp_path, QUERY_FIRST) == []
    assert _find(tmp_path, "def flush(rows, db):\n    for row in rows:\n        db.insert(row)\n        processed += 1\n") == []


def test_restored_in_except_or_finally_is_clean(tmp_path):
    src = (
        "import time\n\nclass S:\n    def sweep(self):\n        old = self.last_full_sweep\n        self.last_full_sweep = time.monotonic()\n"
        "        try:\n            self.query_stale()\n        except Exception:\n            self.last_full_sweep = old\n            raise\n"
    )
    assert _find(tmp_path, src) == []


def test_stamp_followed_by_logging_or_clock_only_is_clean(tmp_path):
    assert _find(tmp_path, "import time\ndef f(self, log):\n    self.last_run = time.time()\n    log.info('started')\n") == []
    assert _find(tmp_path, "import time\ndef f(self):\n    self.last_run = time.time()\n    return None\n") == []


def test_non_matching_names_and_non_clock_values_are_clean(tmp_path):
    assert _find(tmp_path, "import time\ndef f(self):\n    self.started = time.time()\n    self.query()\n") == []
    assert _find(tmp_path, "def f(self):\n    self.last_run = 0\n    self.query()\n") == []
    assert _find(tmp_path, "def f(self, db):\n    skipped += 1\n    db.insert(1)\n") == []


def test_marker_aliases_and_custom_names(tmp_path):
    marked = STAMP_FIRST.replace("        self.last_full_sweep", "        # stamp-first-ok: the query is idempotent\n        self.last_full_sweep")
    assert _find(tmp_path, marked) == []
    assert [f.line for f in _find(tmp_path, "from time import monotonic as clock\ndef f(self):\n    self.next_scan = clock()\n    self.work()\n")] == []
    assert [f.line for f in _find(tmp_path, "import time\ndef f(self):\n    self.heartbeat = time.time()\n    self.work()\n", stamp_regex="heartbeat")] == [3]


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    assert [f.line for f in _find(tmp_path / "b", BOM + STAMP_FIRST.encode())] == [5]
    assert [f.line for f in _find(tmp_path / "c", STAMP_FIRST.replace("\n", "\r\n").encode())] == [5]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text(STAMP_FIRST, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_state_before_work(tmp_path, use_git=False)
    assert len(find_state_before_work(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_state_before_work(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    (tmp_path / "m.py").write_text(STAMP_FIRST, encoding="utf-8")
    with pytest.raises(AssertionError, match="last_full_sweep"):
        assert_state_before_work(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_state_before_work(tmp_path, use_git=False)
    base.write_text(json.dumps({"schema": 1, "gate": "state_before_work", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8")
    assert_state_before_work(tmp_path, use_git=False, baseline_path=base)
    (tmp_path / "n.py").write_text(STAMP_FIRST, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"n\.py"):
        assert_state_before_work(tmp_path, use_git=False, baseline_path=base)
