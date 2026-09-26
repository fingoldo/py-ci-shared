"""Tests for clock_day_boundary: the incident shape, each offset spelling, and each exemption."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.clock_day_boundary import assert_no_clock_day_boundary, find_clock_day_boundary


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


_INCIDENT = """
    import time
    def test_it_still_works_later_the_same_day(client):
        cursor = client.mint(now=time.time())
        assert client.accepts(cursor, now=time.time() + 3600)
"""


def _lines(tmp_path: Path, body: str) -> list[int]:
    (tmp_path / "test_m.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_clock_day_boundary(tmp_path, use_git=False)
    return [f.line for f in findings]


def test_the_incident_shape_is_reported(tmp_path):
    assert _lines(tmp_path, _INCIDENT) == [5]


@pytest.mark.parametrize(
    "shift",
    [
        "time.time() - 2 * 3600",
        "int(time.time()) + 7200",
        "datetime.now() + timedelta(hours=5)",
        "datetime.utcnow() - timedelta(minutes=90)",
        "date.today() + timedelta(days=1, hours=2)",
        "now + 3600",
    ],
)
def test_each_reading_and_offset_spelling(tmp_path, shift):
    body = f"import time\nfrom datetime import datetime, date, timedelta\ndef test_expires_same_day():\n    now = time.time()\n    x = {shift}\n"
    assert _lines(tmp_path, body) == [5]


@pytest.mark.parametrize(
    "shift",
    [
        "time.time() + 86400",
        "datetime.now() - timedelta(days=2)",
        "time.time() + 1800",
        "datetime.now() + timedelta(minutes=30)",
        "time.time() + delta",
        "time.time() // 86400 + 3600",
        "datetime.now().replace(hour=0) + timedelta(hours=3)",
    ],
)
def test_whole_days_short_shifts_unknown_offsets_and_normalised_readings_are_not(tmp_path, shift):
    body = f"import time\nfrom datetime import datetime, timedelta\ndef test_expires_same_day(delta):\n    x = {shift}\n"
    assert _lines(tmp_path, body) == []


def test_a_test_that_does_not_speak_of_days_is_an_mtime_or_a_duration(tmp_path):
    body = "import os, time\ndef test_does_not_overwrite(p):\n    past = time.time() - 3600\n    os.utime(p, (past, past))\n"
    assert _lines(tmp_path, body) == []


def test_frozen_clocks_lifetime_claims_markers_and_helpers_are_not(tmp_path):
    body = """
        import time
        from freezegun import freeze_time
        @freeze_time("2026-01-01 23:30:00")
        def test_a_day():
            return time.time() + 3600
        def test_b_day(freezer):
            return time.time() + 3600
        def test_c_day(monkeypatch):
            monkeypatch.setattr(time, "time", lambda: 0.0)
            return time.time() + 3600
        def test_d_day():
            return {"exp": int(time.time()) + 3600}
        def test_e_day():
            return time.time() + 3600  # clock-ok: the service compares durations only
        def helper_day():
            return time.time() + 3600
    """
    assert _lines(tmp_path, body) == []


def test_an_aliased_clock_is_resolved(tmp_path):
    body = "from time import time as now_s\ndef test_cursor_expiry():\n    return now_s() + 7200\n"
    assert _lines(tmp_path, body) == [3]


def test_bom_unparsable_empty_and_the_ratchet(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_bytes(b"\xef\xbb\xbf" + textwrap.dedent(_INCIDENT).encode())
    assert [f.line for f in find_clock_day_boundary(tests, use_git=False)[0]] == [5]
    bl = tmp_path / "bl.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_clock_day_boundary(tests, baseline_path=bl, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_clock_day_boundary(tests, baseline_path=bl, refresh=True, use_git=False)
    assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
    assert_no_clock_day_boundary(tests, baseline_path=bl, use_git=False)
    (tests / "test_b.py").write_text("import time\ndef test_b_day():\n    return time.time() + 7200\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="clock-crosses-day"):
        assert_no_clock_day_boundary(tests, baseline_path=bl, use_git=False)
    (tests / "test_bad.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"test_bad\.py"):
        assert_no_clock_day_boundary(tests, use_git=False)
    (tmp_path / "e").mkdir()
    with pytest.raises(pytest.fail.Exception, match="parsed"):
        assert_no_clock_day_boundary(tmp_path / "e", use_git=False)
