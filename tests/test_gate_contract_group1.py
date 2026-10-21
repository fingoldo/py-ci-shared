"""Regression tests for the group-1 retrofit: an unparsable file is a failure unless ``allow_unparsed``, and a floor
on the parsed files still applies with it."""

import pytest

from py_ci_shared.atomic_write_staging import assert_atomic_write_staging
from py_ci_shared.phantom_code_references import assert_no_stale_absolute_line_citations
from py_ci_shared.unresolved_imports import assert_all_from_imports_resolve

BROKEN = "def broken(:\n"


def test_atomic_write_staging_unparsable_file_fails_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_atomic_write_staging(tmp_path, use_git=False)
    assert_atomic_write_staging(tmp_path, use_git=False, allow_unparsed=True)


def test_atomic_write_staging_floor_applies_even_when_unparsed_allowed(tmp_path):
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_atomic_write_staging(tmp_path, use_git=False, allow_unparsed=True)


def test_unresolved_imports_unparsable_file_fails_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
        assert_all_from_imports_resolve([tmp_path], [tmp_path], resolvable_prefixes=())
    assert_all_from_imports_resolve([tmp_path], [tmp_path], resolvable_prefixes=(), allow_unparsed=True)


def test_stale_citations_empty_input_fails_by_default(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="would check nothing"):
        assert_no_stale_absolute_line_citations([])


def test_stale_citations_unparsable_file_is_a_finding_unless_allowed(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
        assert_no_stale_absolute_line_citations([bad])
    assert_no_stale_absolute_line_citations([bad], min_files=0, allow_unparsed=True)
