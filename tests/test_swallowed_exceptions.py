"""Tests for swallowed_exceptions: one seeded site per shape, one control per exemption, all on real files."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.swallowed_exceptions import assert_no_swallowed_exceptions, find_swallowed_exceptions


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _lines(tmp_path: Path, body: str, name: str = "m.py", **kw: object) -> list[int]:
    (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_swallowed_exceptions(tmp_path, use_git=False, **kw)  # type: ignore[arg-type]
    return [f.line for f in findings if f.path == name]


class TestDetection:
    @pytest.mark.parametrize(
        "caught",
        ["", "Exception", "BaseException", "OSError", "IOError", "EnvironmentError", "PermissionError", "(ValueError, OSError)", "shutil.Error"],
    )
    def test_each_in_scope_type_is_reported(self, tmp_path, caught):
        src = f"import shutil\n\ndef f(p, data):\n    try:\n        p.write_text(data)\n    except {caught}:\n        pass\n"
        assert _lines(tmp_path, src) == [6]

    @pytest.mark.parametrize("caught", ["FileNotFoundError", "KeyError", "ValueError", "ImportError"])
    def test_out_of_scope_types_are_not(self, tmp_path, caught):
        src = f"def f(p, data):\n    try:\n        p.write_text(data)\n    except {caught}:\n        pass\n"
        assert _lines(tmp_path, src) == []

    def test_continue_break_ellipsis_and_a_docstring_are_silent(self, tmp_path):
        src = """
            def f(paths):
                for p in paths:
                    try:
                        p.write_text("x")
                    except OSError:
                        continue
                    try:
                        p.write_text("y")
                    except OSError:
                        "ignored"
                        ...
                    try:
                        p.write_text("z")
                    except OSError:
                        break
        """
        assert _lines(tmp_path, src) == [6, 10, 15]

    def test_an_aliased_exception_name_is_resolved(self, tmp_path):
        src = "from builtins import OSError as E\n\ndef f(p):\n    try:\n        p.write_text('x')\n    except E:\n        pass\n"
        assert _lines(tmp_path, src) == [6]

    def test_the_message_names_the_scope_and_the_failing_statement(self, tmp_path):
        (tmp_path / "m.py").write_text(
            "class C:\n    def save(self, p):\n        try:\n            p.write_text('x')\n        except OSError:\n            pass\n", encoding="utf-8"
        )
        findings, _ = find_swallowed_exceptions(tmp_path, use_git=False)
        assert findings[0].message == "`except OSError` in C.save swallows a failure of `p.write_text('x')`"


class TestNotASwallow:
    @pytest.mark.parametrize(
        "handler",
        ["raise", "raise RuntimeError('x') from None", "return None", "logger.warning('write failed')", "self.failed = True", "errors.append(p)"],
    )
    def test_a_handler_that_acts_is_not_reported(self, tmp_path, handler):
        src = f"def f(self, p, logger, errors):\n    try:\n        p.write_text('x')\n    except OSError:\n        {handler}\n"
        assert _lines(tmp_path, src) == []

    def test_a_debug_log_is_silent_only_when_asked(self, tmp_path):
        src = "def f(p, logger):\n    try:\n        p.write_text('x')\n    except OSError:\n        logger.debug('no')\n"
        assert _lines(tmp_path, src) == []
        assert _lines(tmp_path, src, quiet_log_is_silent=True) == [4]
        warn = "def f(p, logger):\n    try:\n        p.write_text('x')\n    except OSError:\n        logger.warning('no')\n"
        assert _lines(tmp_path, warn, name="w.py", quiet_log_is_silent=True) == []


class TestExemptions:
    def test_best_effort_cleanup_calls_are_exempt(self, tmp_path):
        src = """
            import os, subprocess
            def f(p, proc, conn, lru, key):
                try:
                    os.remove(p)
                    lru.pop(key, None)
                except OSError:
                    pass
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)])
                except OSError:
                    pass
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    fd = os.open(p, os.O_RDONLY)
                    os.fsync(fd)
                except OSError:
                    pass
        """
        assert _lines(tmp_path, src) == []

    def test_cleanup_mixed_with_real_work_is_not_exempt(self, tmp_path):
        src = "import os\ndef f(p, q):\n    try:\n        os.remove(p)\n        q.write_text('x')\n    except OSError:\n        pass\n"
        assert _lines(tmp_path, src) == [6]

    def test_metadata_probes_import_probes_and_fallbacks_are_exempt(self, tmp_path):
        src = """
            import os, json
            def size(entries):
                total = 0
                for e in entries:
                    try:
                        total += e.stat().st_size
                    except OSError:
                        pass
                return total
            try:
                import numpy
            except Exception:
                pass
            def cached(p):
                try:
                    return json.loads(p.read_text())
                except (OSError, ValueError):
                    pass
                return None
        """
        assert _lines(tmp_path, src) == []

    def test_finally_blocks_and_release_scopes_are_exempt(self, tmp_path):
        src = """
            class H:
                def __del__(self):
                    try:
                        self.p.write_text("x")
                    except Exception:
                        pass
                def emit(self, record):
                    try:
                        self.buf.write(record)
                    except Exception:
                        pass
                def run(self):
                    try:
                        work()
                    finally:
                        try:
                            self.p.write_text("x")
                        except OSError:
                            pass
                    try:
                        self.p.write_text("y")
                    except OSError:
                        pass
        """
        assert _lines(tmp_path, src) == [23]

    @pytest.mark.parametrize(
        "marker_line",
        ["    except OSError:  # swallow-ok: read-only mirror", "    except OSError:  # nosec B110 - probe", "    except OSError:  # nosec B112"],
    )
    def test_markers_on_the_except_line(self, tmp_path, marker_line):
        src = f"def f(p):\n    try:\n        p.write_text('x')\n{marker_line}\n        pass\n"
        assert _lines(tmp_path, src) == []

    def test_markers_above_or_in_the_body_count_and_an_empty_reason_does_not(self, tmp_path):
        above = "def f(p):\n    try:\n        p.write_text('x')\n    # swallow-ok: cache is optional\n    except OSError:\n        pass\n"
        body = "def f(p):\n    try:\n        p.write_text('x')\n    except OSError:\n        pass  # swallow-ok: cache is optional\n"
        empty = "def f(p):\n    try:\n        p.write_text('x')\n    except OSError:  # swallow-ok:\n        pass\n"
        other = "def f(p):\n    try:\n        p.write_text('x')\n    except OSError:  # nosec B603\n        pass\n"
        assert _lines(tmp_path, above, name="a.py") == []
        assert _lines(tmp_path, body, name="b.py") == []
        assert _lines(tmp_path, empty, name="c.py") == [4]
        assert _lines(tmp_path, other, name="d.py") == [4]

    def test_test_and_script_directories_are_out_of_scope_by_default(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "t.py").write_text("try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8")
        (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
        assert find_swallowed_exceptions(tmp_path, use_git=False)[0] == []
        assert len(find_swallowed_exceptions(tmp_path, exclude_parts=(), use_git=False)[0]) == 1


class TestCorpusAndBaseline:
    def test_a_bom_file_is_scanned(self, tmp_path):
        (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbftry:\n    x()\nexcept Exception:\n    pass\n")
        assert [f.line for f in find_swallowed_exceptions(tmp_path, use_git=False)[0]] == [3]

    def test_unparsable_and_empty(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "bad.py").write_text("def (:\n", encoding="utf-8")
        (tmp_path / "a" / "ok.py").write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
            assert_no_swallowed_exceptions(tmp_path / "a", use_git=False)
        (tmp_path / "b").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_swallowed_exceptions(tmp_path / "b", use_git=False)

    def test_zero_tolerance_without_a_baseline_and_a_ratchet_with_one(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "m.py").write_text("def f(p):\n    try:\n        p.write_text('x')\n    except OSError:\n        pass\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="swallowed-exception"):
            assert_no_swallowed_exceptions(src, use_git=False)
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_swallowed_exceptions(src, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_no_swallowed_exceptions(src, baseline_path=bl, refresh=True, use_git=False)
        assert sum(e["count"] for e in json.loads(bl.read_text(encoding="utf-8"))["entries"].values()) == 1
        assert_no_swallowed_exceptions(src, baseline_path=bl, use_git=False)
        (src / "m.py").write_text("# moved\n" + (src / "m.py").read_text(encoding="utf-8"), encoding="utf-8")
        assert_no_swallowed_exceptions(src, baseline_path=bl, use_git=False)  # a line shift is not a new finding
        (src / "n.py").write_text("def g(p):\n    try:\n        p.write_text('x')\n    except OSError:\n        pass\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"n.py"):
            assert_no_swallowed_exceptions(src, baseline_path=bl, use_git=False)


class TestAListOfDirectories:
    _BODY = "def f(p, data):\n    try:\n        p.write_text(data)\n    except OSError:\n        pass\n"

    def _two_dirs(self, tmp_path: Path) -> tuple[Path, Path]:
        a, b = tmp_path / "a", tmp_path / "b"
        for d, name in ((a, "one.py"), (b, "two.py")):
            d.mkdir()
            (d / name).write_text(self._BODY, encoding="utf-8")
        return a, b

    def test_two_directories_report_what_two_calls_report(self, tmp_path):
        a, b = self._two_dirs(tmp_path)
        together, scan = find_swallowed_exceptions([a, b], use_git=False)
        apart = find_swallowed_exceptions(a, use_git=False)[0] + find_swallowed_exceptions(b, use_git=False)[0]
        assert [(f.path, f.line) for f in together] == [("one.py", 4), ("two.py", 4)]
        assert sorted((f.path, f.line) for f in apart) == [(f.path, f.line) for f in together]
        assert len(scan.files) == 2

    def test_a_directory_and_a_file_mix(self, tmp_path):
        a, b = self._two_dirs(tmp_path)
        findings, _ = find_swallowed_exceptions([a, b / "two.py"], use_git=False)
        assert sorted(f.path for f in findings) == sorted(["one.py", (b / "two.py").as_posix()])
        assert [f.line for f in findings] == [4, 4]

    def test_a_missing_entry_raises(self, tmp_path):
        from py_ci_shared._core import CorpusError

        a, _ = self._two_dirs(tmp_path)
        with pytest.raises(CorpusError, match="does not exist"):
            find_swallowed_exceptions([a, tmp_path / "nope"], use_git=False)
