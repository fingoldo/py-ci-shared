"""thread_lifecycle: a non-daemon threading.Thread needs a join, a daemon flag, or a context-manager owner (pyutilz hardware monitor hung exit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.thread_lifecycle import (
    RULE,
    assert_thread_lifecycle,
    find_thread_lifecycle,
)

NL = chr(10)


def _write(root: Path, lines: list, rel: str = "mod.py") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NL.join(lines) + NL, encoding="utf-8")


def _find(tmp_path: Path, lines: list, rel: str = "mod.py", **kwargs):
    _write(tmp_path, lines, rel)
    return find_thread_lifecycle(tmp_path, use_git=False, min_files=1, **kwargs)


def test_an_unjoined_non_daemon_thread_is_reported_at_its_call(tmp_path):
    found = _find(
        tmp_path,
        [
            "import threading",  # 1
            "",  # 2
            "def start_monitor(fn):",  # 3
            "    t = threading.Thread(target=fn)",  # 4
            "    t.start()",  # 5
        ],
    )
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 4, RULE)]


def test_a_daemon_keyword_is_clean(tmp_path):
    assert _find(tmp_path, ["import threading", "def f(fn):", "    threading.Thread(target=fn, daemon=True).start()"]) == []


def test_a_join_in_the_same_function_is_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "def f(fn):",
                "    t = threading.Thread(target=fn)",
                "    t.start()",
                "    t.join()",
            ],
        )
        == []
    )


def test_a_join_in_another_function_does_not_count_for_source(tmp_path):
    found = _find(
        tmp_path,
        [
            "import threading",
            "def start(fn):",
            "    t = threading.Thread(target=fn)",
            "    t.start()",
            "def stop(t):",
            "    t.join()",
        ],
    )
    assert [f.line for f in found] == [3]


def test_setting_daemon_true_on_the_object_is_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "def f(fn):",
                "    t = threading.Thread(target=fn)",
                "    t.daemon = True",
                "    t.start()",
            ],
        )
        == []
    )


def test_a_class_with_enter_and_exit_owns_its_thread(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "class Monitor:",
                "    def __enter__(self):",
                "        self._t = threading.Thread(target=self.run)",
                "        self._t.start()",
                "        return self",
                "    def __exit__(self, *exc):",
                "        self._t.join()",
                "    def run(self):",
                "        pass",
            ],
        )
        == []
    )


def test_an_attribute_thread_joined_elsewhere_in_the_module_is_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "class Sampler:",
                "    def start(self, fn):",
                "        self._worker = threading.Thread(target=fn)",
                "        self._worker.start()",
                "    def stop(self):",
                "        self._worker.join()",
            ],
        )
        == []
    )


def test_a_test_module_joins_module_wide(tmp_path):
    lines = [
        "import threading",
        "def _start(fn):",
        "    t = threading.Thread(target=fn)",
        "    t.start()",
        "    return t",
        "def test_it(fn):",
        "    t = _start(fn)",
        "    t.join()",
    ]
    assert _find(tmp_path, lines, rel="test_mod.py", include_tests=True) == []
    # Default scope is source only: the test file is not scanned, so the floor refuses an empty scan.
    (tmp_path / "src_only").mkdir()
    _write(tmp_path / "src_only", lines, rel="test_mod.py")
    with pytest.raises(EmptyScanError):
        find_thread_lifecycle(tmp_path / "src_only", use_git=False, min_files=1)


def test_threads_built_in_a_list_and_joined_in_a_loop_are_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "def run(fn, n):",
                "    threads = [threading.Thread(target=fn) for _ in range(n)]",
                "    for th in threads:",
                "        th.start()",
                "    for th in threads:",
                "        th.join()",
            ],
        )
        == []
    )


def test_a_reasoned_marker_accepts_the_thread(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "import threading",
                "def f(fn):",
                "    # thread-ok: process-lifetime sampler, stopped by the interpreter on exit",
                "    threading.Thread(target=fn).start()",
            ],
        )
        == []
    )


def test_a_marker_without_a_reason_is_a_finding(tmp_path):
    found = _find(tmp_path, ["import threading", "def f(fn):", "    # thread-ok:", "    threading.Thread(target=fn).start()"])
    assert len(found) == 1 and "needs a reason" in found[0].message


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_thread_lifecycle(tmp_path, use_git=False)
