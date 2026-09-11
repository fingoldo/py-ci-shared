"""Tests for py_ci_shared.statement_compilation: the 08-H1 shape, each patch spelling, and each exclusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.statement_compilation import (
    assert_no_mocked_statement_constructors,
    compile_pg,
    find_mocked_statement_constructors,
)


def _write(tmp_path: Path, name: str, source: str) -> list[Path]:
    path = tmp_path / name
    path.write_text(source.lstrip("\n"), encoding="utf-8")
    return [path]


def test_the_08_h1_shape_is_reported(tmp_path: Path):
    """The test that could not have caught 08-H1: the constructor itself was a MagicMock."""
    files = _write(
        tmp_path,
        "test_cefr.py",
        """
from unittest.mock import MagicMock, patch


async def test_apply_updates_upserts():
    with patch("sqlalchemy.dialects.postgresql.insert", MagicMock()):
        await importer._apply_updates(rows, stats)
""",
    )

    problems = find_mocked_statement_constructors(files, tmp_path)

    assert len(problems) == 1
    assert "test_cefr.py:5" in problems[0]
    assert "`insert`" in problems[0]


@pytest.mark.parametrize(
    "call",
    [
        'patch("glossum.importers.cefr_importer.pg_insert")',
        'patch.object(cefr_importer, "pg_insert")',
        'mocker.patch("glossum.storage.writer.update")',
    ],
)
def test_every_patch_spelling_is_in_the_population(tmp_path: Path, call: str):
    files = _write(
        tmp_path,
        "test_writes.py",
        f"""
def test_it(mocker):
    with {call}:
        pass
""",
    )

    assert len(find_mocked_statement_constructors(files, tmp_path)) == 1


def test_what_is_not_in_the_population(tmp_path: Path):
    """Patching the session or engine, an autospecced constructor, and a routing-marked test."""
    files = _write(
        tmp_path,
        "test_clean.py",
        '''
import pytest
from unittest.mock import AsyncMock, create_autospec, patch


def test_session_is_mocked_not_the_statement():
    with patch("glossum.storage.db.session", AsyncMock()):
        pass


def test_autospec_keeps_the_real_signature():
    with patch("glossum.importers.cefr_importer.pg_insert", autospec=True):
        pass


def test_new_is_an_autospec_of_the_real_thing():
    with patch("glossum.importers.cefr_importer.insert", new=create_autospec(real_insert)):
        pass


@pytest.mark.routing
def test_the_upsert_path_is_chosen():
    """About which branch ran, not about the SQL."""
    with patch("glossum.importers.cefr_importer.pg_insert") as spy:
        assert spy.called
''',
    )

    assert find_mocked_statement_constructors(files, tmp_path) == []


def test_the_assert_names_every_offender(tmp_path: Path):
    files = _write(tmp_path, "test_bad.py", 'from unittest.mock import patch\n\n\ndef test_it():\n    with patch("m.insert"):\n        pass\n')

    with pytest.raises(pytest.fail.Exception, match=r"test_bad\.py:5"):
        assert_no_mocked_statement_constructors(files, tmp_path)


def test_compile_pg_emits_postgresql_grammar():
    """The helper's point: the generic dialect does not render ON CONFLICT, so a test using it proves less."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    postgresql = pytest.importorskip("sqlalchemy.dialects.postgresql")

    table = sqlalchemy.Table("t", sqlalchemy.MetaData(), sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True))
    statement = postgresql.insert(table).values(id=1).on_conflict_do_nothing(index_elements=["id"])

    sql = compile_pg(statement)

    assert "ON CONFLICT" in sql.upper()
    assert "INSERT INTO t" in sql
