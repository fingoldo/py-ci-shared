"""swallowed_exceptions: contextlib.suppress twins (33-F5) and finally / close scopes that did real work (33-F21)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.swallowed_exceptions import find_swallowed_exceptions


def _found(tmp_path: Path, src: str) -> list[tuple[int, str]]:
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    findings, _ = find_swallowed_exceptions(tmp_path, use_git=False)
    return [(f.line, f.message) for f in findings]


def test_suppress_exception_is_reported(tmp_path):
    src = "import contextlib\n\n\ndef f():\n    with contextlib.suppress(Exception):\n        g()\n"
    assert _found(tmp_path, src) == [(5, "`suppress(Exception)` in f swallows a failure of `g()`")]


def test_suppress_oserror_around_the_np_save_replace_incident_is_reported(tmp_path):
    src = "from contextlib import suppress as sp\nimport os\n\n\ndef f(a, b):\n    with sp(OSError):\n        np.save(a, 1)\n        os.replace(a, b)\n"
    assert [line for line, _ in _found(tmp_path, src)] == [6]


def test_suppress_of_a_narrow_error_a_release_call_and_a_marker_are_not_reported(tmp_path):
    src = (
        "import contextlib\n\n\ndef f(p, q):\n"
        "    with contextlib.suppress(KeyError):\n        g()\n"
        "    with contextlib.suppress(OSError):\n        p.unlink()\n"
        "    with contextlib.suppress(OSError):  # swallow-ok: probe\n        g()\n"
    )
    assert _found(tmp_path, src) == []


def test_a_commit_swallowed_inside_finally_is_reported_but_a_release_is_not(tmp_path):
    src = (
        "def f(c, h):\n    try:\n        g()\n    finally:\n        try:\n            c.commit()\n        except Exception:\n            pass\n"
        "        try:\n            h.close()\n        except Exception:\n            pass\n"
    )
    assert [line for line, _ in _found(tmp_path, src)] == [7]


def test_real_work_swallowed_in_close_is_reported_but_emit_stays_exempt(tmp_path):
    src = (
        "class A:\n    def close(self):\n        try:\n            self.flush_to_disk()\n            self.save()\n        except Exception:\n            pass\n"
        "    def emit(self, r):\n        try:\n            self.write(r)\n        except Exception:\n            pass\n"
    )
    assert [line for line, _ in _found(tmp_path, src)] == [6]
