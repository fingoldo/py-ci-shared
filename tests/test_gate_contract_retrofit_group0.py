"""Regression tests for the group-0 retrofit of the corpus-gate keyword contract (``min_*`` floor and ``allow_unparsed``).

Each retrofitted entry gets the same two behaviours: an empty or too-small corpus fails, and an unparsable file fails
unless ``allow_unparsed=True``. One representative per module family is enough to pin the shared shape.
"""

from __future__ import annotations

import pytest

from py_ci_shared.alembic_concurrently import assert_concurrently_is_in_autocommit_blocks
from py_ci_shared.naive_utcnow import assert_no_naive_utcnow
from py_ci_shared.stdlib_json_ban import assert_no_stdlib_json
from py_ci_shared.unread_init_params import assert_no_unread_init_params

BROKEN = "def broken(:\n"


def test_naive_utcnow_unparsable_file_fails_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
        assert_no_naive_utcnow(tmp_path, use_git=False)
    assert_no_naive_utcnow(tmp_path, use_git=False, allow_unparsed=True)


def test_naive_utcnow_empty_root_fails_the_floor(tmp_path):
    with pytest.raises(pytest.fail.Exception):
        assert_no_naive_utcnow(tmp_path, use_git=False)


def test_stdlib_json_ban_unparsable_file_fails_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text("import orjson\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_no_stdlib_json(tmp_path, use_git=False, include_tests=True)
    assert_no_stdlib_json(tmp_path, use_git=False, include_tests=True, allow_unparsed=True)


def test_alembic_concurrently_empty_versions_dir_fails_the_floor(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="expected at least 1"):
        assert_concurrently_is_in_autocommit_blocks(tmp_path)


def test_alembic_concurrently_unparsable_migration_fails_unless_allowed(tmp_path):
    (tmp_path / "001_ok.py").write_text("revision = '1'\n", encoding="utf-8")
    (tmp_path / "002_bad.py").write_text(BROKEN, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_concurrently_is_in_autocommit_blocks(tmp_path)
    assert_concurrently_is_in_autocommit_blocks(tmp_path, allow_unparsed=True)


def test_unread_init_params_no_files_fails_the_floor(tmp_path):
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_no_unread_init_params([], tmp_path)
