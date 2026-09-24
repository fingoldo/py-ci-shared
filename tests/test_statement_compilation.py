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


@pytest.mark.parametrize(
    "header, call",
    [
        ("from unittest import mock", 'mock.patch("pkg.mod.insert")'),
        ("import unittest.mock", 'unittest.mock.patch("pkg.mod.insert")'),
        ("from unittest import mock as m", 'm.patch("pkg.mod.insert")'),
        ("from unittest.mock import patch as p", 'p("pkg.mod.insert")'),
        ("from unittest import mock", 'mock.patch.object(mod, "pg_insert")'),
        ("import os", 'monkeypatch.setattr("pkg.mod.update", fake)'),
    ],
)
def test_aliased_and_module_spellings_are_in_the_population(tmp_path: Path, header: str, call: str):
    files = _write(tmp_path, "test_alias.py", f"{header}\n\n\ndef test_it(monkeypatch):\n    with {call}:\n        pass\n")
    problems = find_mocked_statement_constructors(files, tmp_path)
    assert len(problems) == 1 and "test_alias.py:5" in problems[0]


def test_an_unrelated_patch_method_is_not_a_patch_call(tmp_path: Path):
    """An HTTP client's .patch() takes a URL; the callee must resolve to a mock patcher."""
    files = _write(tmp_path, "test_http.py", 'def test_it(client):\n    client.patch("/api/items.update")\n')
    assert find_mocked_statement_constructors(files, tmp_path) == []


def test_a_stacked_patch_above_the_routing_mark_is_exempt(tmp_path: Path):
    source = """
import pytest
from unittest.mock import patch


@patch("pkg.mod.insert")
@pytest.mark.routing
def test_routed(spy):
    pass


@patch("pkg.mod.insert")
def test_not_routed(spy):
    pass
"""
    problems = find_mocked_statement_constructors(_write(tmp_path, "test_stack.py", source), tmp_path)
    assert len(problems) == 1 and "test_stack.py:11" in problems[0]


def test_a_module_or_class_pytestmark_routing_exempts(tmp_path: Path):
    module = (
        'import pytest\nfrom unittest.mock import patch\n\npytestmark = [pytest.mark.routing]\n\n\ndef test_it():\n    with patch("m.insert"):\n        pass\n'
    )
    assert find_mocked_statement_constructors(_write(tmp_path, "test_mod.py", module), tmp_path) == []
    klass = (
        "import pytest\nfrom unittest.mock import patch\n\n\nclass TestX:\n    pytestmark = pytest.mark.routing\n\n"
        '    def test_it(self):\n        with patch("m.insert"):\n            pass\n\n\ndef test_other():\n    with patch("m.insert"):\n        pass\n'
    )
    problems = find_mocked_statement_constructors(_write(tmp_path, "test_cls.py", klass), tmp_path)
    assert len(problems) == 1 and "test_cls.py:14" in problems[0]


@pytest.mark.parametrize(
    "kwargs, flagged",
    [
        ("new_callable=MagicMock", True),
        ("autospec=False", True),
        ("spec=None", True),
        ("autospec=True", False),
        ("spec=real_insert", False),
        ("new_callable=create_autospec", False),
    ],
)
def test_only_a_truthy_autospec_keeps_the_signature(tmp_path: Path, kwargs: str, flagged: bool):
    files = _write(tmp_path, "test_spec.py", f'from unittest.mock import patch\n\n\ndef test_it():\n    with patch("m.insert", {kwargs}):\n        pass\n')
    assert len(find_mocked_statement_constructors(files, tmp_path)) == (1 if flagged else 0)


def test_http_and_method_targets_are_not_constructors(tmp_path: Path):
    source = (
        "from unittest.mock import patch\n\n\ndef test_it(mocker):\n"
        '    patch("httpx.AsyncClient.delete")\n    patch("requests.delete")\n    patch.object(Session, "delete")\n'
        '    patch("myapp.repo.delete")\n'
    )
    problems = find_mocked_statement_constructors(_write(tmp_path, "test_http2.py", source), tmp_path)
    assert len(problems) == 1 and "test_http2.py:8" in problems[0]
    scoped = find_mocked_statement_constructors([tmp_path / "test_http2.py"], tmp_path, module_prefixes=("sqlalchemy.",))
    assert scoped == []


def test_an_empty_corpus_and_an_unparsable_file_are_reported(tmp_path: Path):
    problems = find_mocked_statement_constructors([], tmp_path)
    assert len(problems) == 1 and "only 0 file(s) parsed" in problems[0]
    bad = _write(tmp_path, "test_bad.py", "def test_it(:\n    pass\n")
    good = _write(tmp_path, "test_ok.py", "def test_ok():\n    pass\n")
    problems = find_mocked_statement_constructors(bad + good, tmp_path)
    assert len(problems) == 1 and "test_bad.py" in problems[0] and "not checked" in problems[0]
    with pytest.raises(pytest.fail.Exception):
        assert_no_mocked_statement_constructors([], tmp_path)


def test_a_file_outside_root_and_a_bom_file_are_checked(tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    path = outside / "test_bom.py"
    path.write_bytes(b"\xef\xbb\xbf" + b'from unittest.mock import patch\n\n\ndef test_it():\n    with patch("m.insert"):\n        pass\n')
    problems = find_mocked_statement_constructors([path], tmp_path / "root")
    assert len(problems) == 1 and "test_bom.py:5" in problems[0]
