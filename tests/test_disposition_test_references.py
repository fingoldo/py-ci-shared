"""Unit tests for disposition_test_references: a test a disposition names must exist."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.disposition_test_references import assert_disposition_tests_exist, disposition_paragraphs, find_missing_test_references


@pytest.fixture
def project(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_guard.py").write_text("class TestGuard:\n    def test_refuses(self):\n        pass\n\n\ndef test_free():\n    pass\n", encoding="utf-8")
    return tmp_path


def _audit(project: Path, body: str) -> Path:
    p = project / "01.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_real_references_pass(project):
    audit = _audit(
        project, "### X-1 (P2) -- t\n\n**Disposition:** RESOLVED. Test `tests/test_guard.py::TestGuard::test_refuses`, and `test_free`, and `TestGuard`.\n"
    )
    assert find_missing_test_references([audit], project) == []


def test_a_missing_file_a_missing_member_and_a_missing_name_are_each_reported(project):
    audit = _audit(
        project,
        "**Disposition:** RESOLVED; tested in `tests/test_gone.py`,\n`tests/test_guard.py::TestGuard::test_other`, and by `TestNowhere`.\n",
    )
    assert find_missing_test_references([audit], project) == [
        "01.md: `TestNowhere`: no test of that name in tests/",
        "01.md: `tests/test_gone.py`: no such test file",
        "01.md: `tests/test_guard.py::test_other`: not defined in that file",
    ]


def test_a_sibling_projects_test_resolves_against_that_sibling(tmp_path):
    """A fix that landed in a sibling package is tested there, and the disposition says so by path or name."""
    proj, sibling = tmp_path / "proj", tmp_path / "other_pkg"
    (proj / "tests").mkdir(parents=True)
    (sibling / "tests").mkdir(parents=True)
    (sibling / "tests" / "test_there.py").write_text("def test_in_the_sibling():\n    pass\n", encoding="utf-8")
    audit = _audit(proj, "**Disposition:** RESOLVED in `other_pkg/tests/test_there.py`, by `test_in_the_sibling`.\n")
    assert len(find_missing_test_references([audit], proj)) == 2
    assert find_missing_test_references([audit], proj, other_roots=[sibling]) == []


def test_a_bare_tests_path_resolves_in_a_sibling_too(tmp_path):
    """A dashboard disposition cited `tests/test_check_indexes_reconciliation.py`, which lives in the

    sibling package; the round names the package in its prose, not inside the backticks.
    """
    proj = tmp_path / "dashboard"
    (proj / "tests").mkdir(parents=True)
    sibling = tmp_path / "realtime_applications"
    (sibling / "tests").mkdir(parents=True)
    (sibling / "tests" / "test_reconciliation.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    audit = proj / "02b.md"
    audit.write_text("**Disposition:** RESOLVED; `tests/test_reconciliation.py` globs every expectation.\n", encoding="utf-8")
    assert find_missing_test_references([audit], proj, other_roots=[sibling]) == []
    assert find_missing_test_references([audit], proj) == ["02b.md: `tests/test_reconciliation.py`: no such test file"]


def test_only_disposition_paragraphs_count(project):
    """A finding's own text may name a test that is missing -- that is often the finding."""
    audit = _audit(project, "The problem: `TestNowhere` was never written.\n\n**Disposition:** RESOLVED, see `TestGuard`.\n\nLater prose: `TestAlsoMissing`.\n")
    assert find_missing_test_references([audit], project) == []


def test_a_multi_line_disposition_is_one_paragraph_and_fences_are_skipped():
    text = "**Disposition:** RESOLVED\nsecond line `TestA`\n\n```\n**Disposition:** in a fence `TestB`\n```\n- Disposition: DEFERRED `TestC`\n"
    paras = disposition_paragraphs(text)
    assert len(paras) == 2 and "`TestA`" in paras[0] and "TestB" not in "".join(paras) and "`TestC`" in paras[1]


def test_assert_is_shrink_only(project):
    audit = _audit(project, "**Disposition:** RESOLVED by `TestNowhere`.\n")
    known = ["01.md: `TestNowhere`: no test of that name in tests/"]
    assert_disposition_tests_exist([audit], project, known=known)
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_disposition_tests_exist([audit], project)
    with pytest.raises(pytest.fail.Exception, match="now resolve"):
        assert_disposition_tests_exist([_audit(project, "**Disposition:** RESOLVED by `TestGuard`.\n")], project, known=known)


def test_a_table_row_disposition_is_checked(project):
    audit = _audit(project, "| id | text |\n|----|------|\n| Disposition: fixed, test `test_ghost` |\n| X-2 | Disposition: fixed, test `test_free` |\n")
    assert find_missing_test_references([audit], project) == ["01.md: `test_ghost`: no test of that name in tests/"]


def test_a_backslash_path_is_normalised(project):
    audit = _audit(project, "**Disposition:** RESOLVED in `tests\\test_guard.py::TestGuard::test_refuses` and `tests\\test_gone.py`.\n")
    assert find_missing_test_references([audit], project) == ["01.md: `tests/test_gone.py`: no such test file"]


def test_a_parametrised_id_names_its_test(project):
    audit = _audit(project, "**Disposition:** RESOLVED by `test_free[case-1]` and `tests/test_guard.py::test_free[x]` and `test_ghost[y]`.\n")
    assert find_missing_test_references([audit], project) == ["01.md: `test_ghost`: no test of that name in tests/"]


def test_a_pyi_path_is_not_read_as_a_py_file(project):
    (project / "tests" / "stubs.pyi").write_text("def f() -> int: ...\n", encoding="utf-8")
    audit = _audit(project, "**Disposition:** RESOLVED; typed in `tests/stubs.pyi`.\n")
    assert find_missing_test_references([audit], project) == []


def test_class_member_references_are_class_scoped(project):
    (project / "tests" / "test_b.py").write_text("class TestA:\n    pass\n\n\nclass TestB:\n    def test_b(self):\n        pass\n", encoding="utf-8")
    audit = _audit(project, "**Disposition:** RESOLVED by `tests/test_b.py::TestA::test_b` and `TestA::test_b` and `TestB::test_b`.\n")
    assert find_missing_test_references([audit], project) == [
        "01.md: `TestA::test_b`: no test of that name in tests/",
        "01.md: `tests/test_b.py::test_b`: not defined in that file",
    ]


def test_an_unparsable_test_file_is_reported(project):
    (project / "tests" / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    audit = _audit(project, "**Disposition:** RESOLVED by `TestGuard`.\n")
    problems = find_missing_test_references([audit], project)
    assert len(problems) == 1 and "test_broken.py: cannot be parsed" in problems[0]
