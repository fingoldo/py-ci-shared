"""Tests for py_ci_shared.systemexit_in_library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.systemexit_in_library import assert_no_systemexit_in_library, find_systemexit_in_library

BOM = b"\xef\xbb\xbf"
LIB = "import sys\n\ndef get_dsn():\n    if not CONFIG:\n        sys.exit(1)\n    return CONFIG\n"


def _write(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _find(tmp_path: Path, files, **kw):
    _write(tmp_path, files if isinstance(files, dict) else {"m.py": files})
    return find_systemexit_in_library(tmp_path, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    found = _find(tmp_path, LIB)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 5, "systemexit-in-library")]
    assert found[0].message.startswith("get_dsn: sys.exit() in a library function (the module has no __main__ guard)")


def test_an_os_exit_finding_names_the_process_mechanism_not_systemexit(tmp_path):
    """41-F2: os._exit() is not a SystemExit; the message must say what it does, and the SystemExit explanation stays for the others."""
    exit_found = _find(tmp_path, "import os\ndef e():\n    os._exit(3)\n")
    assert len(exit_found) == 1 and "os._exit() ends the whole process" in exit_found[0].message
    assert "SystemExit passes" not in exit_found[0].message
    raise_found = _find(tmp_path, "def a():\n    raise SystemExit(2)\n")
    assert "SystemExit passes `except Exception`" in raise_found[0].message


def test_raise_systemexit_exit_quit_and_os_exit_forms(tmp_path):
    src = (
        "import os\ndef a():\n    raise SystemExit(2)\ndef b():\n    raise SystemExit\ndef c():\n    exit(1)\ndef d():\n    quit()\ndef e():\n    os._exit(3)\n"
    )
    assert [f.line for f in _find(tmp_path, src)] == [3, 5, 7, 9, 11]


def test_a_normal_exception_and_unrelated_exit_methods_are_clean(tmp_path):
    assert _find(tmp_path, "def get_dsn():\n    raise RuntimeError('no dsn')\n") == []
    assert _find(tmp_path, "def f(parser, pool):\n    pool.exit()\n    parser.exit(1)\n") == []


def test_entry_names_opt_in_flags_markers_and_module_level_guard_are_clean(tmp_path):
    assert _find(tmp_path, "import sys\ndef main():\n    sys.exit(2)\ndef parse_args():\n    sys.exit(2)\n") == []
    assert _find(tmp_path, "import sys\ndef probe(host, exit_on_fail=False):\n    if exit_on_fail:\n        sys.exit(1)\n") == []
    assert _find(tmp_path, "import sys\ndef f():\n    sys.exit(1)  # exit-ok: only the CLI calls this\n") == []
    assert _find(tmp_path, "import sys\ndef f():\n    # exit-ok: CLI only\n    sys.exit(1)\n") == []
    assert _find(tmp_path, "import sys\nif __name__ == '__main__':\n    sys.exit(0)\n") == []


def test_guarded_module_is_clean_unless_the_function_is_imported_elsewhere(tmp_path):
    guarded = LIB + "\nif __name__ == '__main__':\n    get_dsn()\n"
    assert _find(tmp_path, {"cli.py": guarded}) == []
    found = _find(tmp_path / "x", {"cli.py": guarded, "dash.py": "from cli import get_dsn\n"}) if (tmp_path / "x").mkdir() is None else []
    assert [(f.path, f.line) for f in found] == [("cli.py", 5)]
    assert "imported by another module" in found[0].message


def test_nested_function_exits_belong_to_the_inner_function(tmp_path):
    src = "import sys\ndef outer():\n    def inner():\n        sys.exit(1)\n    return inner\n"
    found = _find(tmp_path, src)
    assert [(f.line, f.message.split(":")[0]) for f in found] == [(4, "inner")]


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    assert [f.line for f in _find(tmp_path / "b", BOM + LIB.encode())] == [5]
    assert [f.line for f in _find(tmp_path / "c", LIB.replace("\n", "\r\n").encode())] == [5]


def test_test_files_are_skipped_unless_asked(tmp_path):
    (tmp_path / "tests").mkdir()
    _write(tmp_path, {"tests/test_x.py": LIB, "conftest.py": LIB, "ok.py": "x = 1"})
    assert find_systemexit_in_library(tmp_path, use_git=False) == []
    found = find_systemexit_in_library(tmp_path, use_git=False, include_tests=True)
    assert sorted(f.path for f in found) == ["conftest.py", "tests/test_x.py"]


def test_custom_entry_names(tmp_path):
    assert _find(tmp_path, LIB, entry_names="get_dsn") == []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, {"ok.py": LIB, "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_systemexit_in_library(tmp_path, use_git=False)
    assert len(find_systemexit_in_library(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_systemexit_in_library(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    _write(tmp_path, {"m.py": LIB})
    with pytest.raises(AssertionError, match="get_dsn"):
        assert_no_systemexit_in_library(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_systemexit_in_library(tmp_path, use_git=False)
    base.write_text(json.dumps({"schema": 1, "gate": "systemexit_in_library", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8")
    assert_no_systemexit_in_library(tmp_path, use_git=False, baseline_path=base)
    _write(tmp_path, {"n.py": LIB})
    with pytest.raises(pytest.fail.Exception, match=r"n\.py"):
        assert_no_systemexit_in_library(tmp_path, use_git=False, baseline_path=base)


@pytest.mark.parametrize("name", ["get_domain_dsn", "client_dsn", "remaining_budget", "decline_request", "load_entry_settings"])
def test_helper_names_merely_containing_an_entry_word_are_not_exempt(tmp_path, name):
    found = _find(tmp_path, f"import sys\n\ndef {name}():\n    sys.exit(1)\n")
    assert [f.line for f in found] == [4]


@pytest.mark.parametrize("name", ["main", "_main", "cli", "run_cli", "parse_args", "main_loop", "cli_entry"])
def test_real_entry_names_stay_exempt(tmp_path, name):
    assert _find(tmp_path, f"import sys\n\ndef {name}():\n    sys.exit(1)\n") == []


def test_aliased_sys_is_seen(tmp_path):
    found = _find(tmp_path, "import sys as _sys\n\ndef helper():\n    _sys.exit(1)\n")
    assert [f.line for f in found] == [4]
    assert [f.line for f in _find(tmp_path, "from sys import exit as bye\n\ndef helper():\n    bye(1)\n")] == [4]


def test_module_attribute_use_of_a_guarded_cli_helper_is_shared(tmp_path):
    cli = "import sys\n\ndef get_dsn():\n    sys.exit(1)\n\nif __name__ == '__main__':\n    get_dsn()\n"
    found = _find(tmp_path, {"cli_tool.py": cli, "worker.py": "import cli_tool\n\ncli_tool.get_dsn()\n"})
    assert [(f.path, f.line) for f in found] == [("cli_tool.py", 4)]
    alone = tmp_path / "alone"
    alone.mkdir()
    assert _find(alone, {"cli_tool.py": cli}) == []
