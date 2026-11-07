"""``include_stubs`` is an opt-in of the corpus: ``.pyi``/``.pyw`` and any-case ``.py`` (audit 42-F9). The default is unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import iter_files, scan_python
from py_ci_shared.naive_utcnow import collect_naive_utcnow
from py_ci_shared.predictable_tmp_path import find_predictable_tmp_path
from py_ci_shared.swallowed_exceptions import find_swallowed_exceptions
from py_ci_shared.systemexit_in_library import find_systemexit_in_library
from py_ci_shared.unsafe_deserialization import find_unsafe_deserialization

SUFFIXES = ("U.PY", "s.pyi", "w.pyw", "ok.py")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for name in SUFFIXES:
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    return tmp_path


def test_the_default_corpus_is_unchanged(tree):
    assert [p.name for p in iter_files(tree, use_git=False)] == ["ok.py"]
    assert scan_python(tree, use_git=False).parsed_count == 1


def test_include_stubs_adds_stubs_and_any_case_py_but_not_other_files(tree):
    names = sorted(p.name for p in iter_files(tree, use_git=False, include_stubs=True))
    assert names == ["U.PY", "ok.py", "s.pyi", "w.pyw"]
    assert scan_python(tree, use_git=False, include_stubs=True).parsed_count == 4


_VIOLATIONS = {
    "naive_utcnow": ("import datetime\nx = datetime.datetime.utcnow()\n", lambda r: collect_naive_utcnow(r, use_git=False)[0]),
    "swallowed": ("def f():\n    try:\n        g()\n    except Exception:\n        pass\n", lambda r: find_swallowed_exceptions(r, use_git=False)[0]),
    "unsafe_deserialization": ("import pickle\n\ndef f(b):\n    return pickle.loads(b)\n", lambda r: find_unsafe_deserialization(r, use_git=False)),
    "systemexit": ("import sys\n\ndef f():\n    sys.exit(1)\n", lambda r: find_systemexit_in_library(r, use_git=False)),
    "tmp_path": ('def f():\n    return open("/tmp/fixed.txt", "w")\n', lambda r: find_predictable_tmp_path(r, use_git=False)),
}


@pytest.mark.parametrize("name", sorted(_VIOLATIONS))
@pytest.mark.parametrize("filename", ["U.PY", "w.pyw"])
def test_an_opted_in_gate_sees_a_violation_in_an_any_case_or_pyw_file(tmp_path, name, filename):
    source, run = _VIOLATIONS[name]
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    assert run(tmp_path) == [], "control: a clean tree has no finding"
    (tmp_path / filename).write_text(source, encoding="utf-8")
    assert [f.path for f in run(tmp_path)] == [filename]
