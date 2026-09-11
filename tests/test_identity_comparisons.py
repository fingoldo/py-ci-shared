"""Unit tests for identity_comparisons: a string constant is compared by value."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.identity_comparisons import assert_no_identity_comparisons, find_identity_comparisons, string_constant_names


@pytest.fixture
def files(tmp_path: Path) -> list[Path]:
    (tmp_path / "queries.py").write_text(
        'INSERT_SQL = "INSERT INTO t VALUES %s"\nUPSERT_SQL = INSERT_SQL + " -- upsert"\nTEMPLATE: str = f"SELECT {1}"\n_MISSING = object()\nLIMIT = 5\n',
        encoding="utf-8",
    )
    (tmp_path / "use.py").write_text(
        "from queries import INSERT_SQL, UPSERT_SQL, _MISSING\nimport queries\n\n"
        "def pick(sql, cached, flag):\n"
        "    a = sql is INSERT_SQL\n"
        "    b = sql is not queries.UPSERT_SQL\n"
        "    c = cached is _MISSING\n"
        "    d = sql is None\n"
        "    e = sql == INSERT_SQL\n"
        "    return a, b, c, d, e\n",
        encoding="utf-8",
    )
    return [tmp_path / "queries.py", tmp_path / "use.py"]


def test_the_string_constants_are_found(files):
    assert string_constant_names(files) == {"INSERT_SQL", "UPSERT_SQL", "TEMPLATE"}


def test_only_the_identity_comparisons_against_strings_are_reported(files):
    problems = find_identity_comparisons(files, root=files[0].parent)
    assert [p.split(":")[1] for p in problems] == ["5", "6"]
    assert all("use ==" in p for p in problems)


def test_assert_passes_on_value_comparisons(tmp_path, files):
    files[1].write_text("from queries import INSERT_SQL\n\ndef pick(sql):\n    return sql == INSERT_SQL\n", encoding="utf-8")
    assert_no_identity_comparisons(files)


def test_assert_refuses_an_empty_scan():
    with pytest.raises(pytest.fail.Exception, match="check nothing"):
        assert_no_identity_comparisons([])
