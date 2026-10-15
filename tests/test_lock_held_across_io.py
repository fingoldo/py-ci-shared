"""Unit tests for lock_held_across_io: a blocking call inside a with-block on a lock."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.lock_held_across_io import assert_no_lock_held_across_io, find_lock_held_across_io


def _lines(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "mod.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return [f.message for f in find_lock_held_across_io([p], min_files=1)]


@pytest.mark.parametrize(
    "call",
    [
        "self._db.connect(host)",
        "connect_pool(host)",
        "self.getconn()",
        "urllib.request.urlopen(url)",
        "requests.get(url)",
        "httpx.post(url)",
        "socket.create_connection((host, 80))",
        "subprocess.run(cmd)",
        "time.sleep(1)",
        "open(path, 'w').write(text)",
        "open(path, mode='ab')",
    ],
)
def test_blocking_call_under_a_named_lock_is_found(tmp_path, call):
    body = f"""
        import time, socket, subprocess, requests, httpx, urllib.request

        class Pool:
            def acquire(self, host, url, path, text, cmd):
                with self._pool_lock:
                    {call}
    """
    assert len(_lines(tmp_path, body)) == 1


def test_module_bound_lock_and_inline_constructor_count_as_locks(tmp_path):
    body = """
        import threading

        _gate = threading.Lock()

        def fetch(conn, host):
            with _gate:
                conn.connect(host)

        def fetch_inline(conn, host):
            with threading.RLock():
                conn.connect(host)
    """
    assert len(_lines(tmp_path, body)) == 2


def test_connect_outside_the_lock_is_not_a_finding(tmp_path):
    body = """
        class Pool:
            def acquire(self, host):
                conn = self._db.connect(host)
                with self._pool_lock:
                    self._conns[host] = conn
                return conn
    """
    assert _lines(tmp_path, body) == []


def test_read_open_and_non_lock_with_are_not_findings(tmp_path):
    body = """
        def load(path, session):
            with open(path) as fh:
                data = fh.read()
            with session.connect():
                pass
            return data
    """
    assert _lines(tmp_path, body) == []


def test_nested_function_inside_the_lock_is_not_executed_there(tmp_path):
    body = """
        class Pool:
            def register(self, host):
                with self._pool_lock:
                    def later():
                        return self._db.connect(host)
                    self._deferred.append(later)
    """
    assert _lines(tmp_path, body) == []


def test_nested_locks_report_the_call_once(tmp_path):
    body = """
        class Pool:
            def acquire(self, host):
                with self._outer_lock:
                    with self._inner_lock:
                        self._db.connect(host)
    """
    assert len(_lines(tmp_path, body)) == 1


def test_assert_raises_on_a_finding_and_passes_when_clean(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("class P:\n    def a(self):\n        with self._lock:\n            self.db.connect()\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="lock-held-across-io"):
        assert_no_lock_held_across_io([bad])
    good = tmp_path / "good.py"
    good.write_text("class P:\n    def a(self):\n        self.db.connect()\n", encoding="utf-8")
    assert_no_lock_held_across_io([good])


_LOCKED_WRITE = """
import threading
_lock = threading.Lock()


def append(path, data):
    with _lock:
        with open(path, "ab") as handle:{marker}
            handle.write(data)
"""


def test_a_reviewed_marker_with_a_reason_suppresses_the_finding(tmp_path):
    assert _lines(tmp_path, _LOCKED_WRITE.format(marker="  # lock-io-ok: cross-process append; a torn line otherwise")) == []


def test_a_marker_without_a_reason_does_not_suppress_the_finding(tmp_path):
    assert len(_lines(tmp_path, _LOCKED_WRITE.format(marker="  # lock-io-ok:"))) == 1


def test_a_marker_on_another_line_does_not_suppress_the_finding(tmp_path):
    body = _LOCKED_WRITE.format(marker="") + "\n# lock-io-ok: the wrong line, so the call is still reported\n"
    assert len(_lines(tmp_path, body)) == 1
