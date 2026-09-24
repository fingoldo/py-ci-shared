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


def _lines(problems: list[str]) -> list[str]:
    return [p.split(":")[1] for p in problems]


def test_a_percent_formatted_constant_is_stringish(tmp_path):
    """SUITE-18: `"%s" % x` builds a str, so identity against it is the same defect."""
    (tmp_path / "q.py").write_text('SQL = "SELECT %s" % "x"\nN = 3 % 2\n\ndef f(s):\n    return s is SQL, s is N\n', encoding="utf-8")
    problems = find_identity_comparisons([tmp_path / "q.py"], root=tmp_path)
    assert _lines(problems) == ["5"] and "s is SQL" in problems[0]


def test_tuple_if_try_and_class_constants_are_found(tmp_path):
    (tmp_path / "q.py").write_text(
        "A, B = 'a', 'b'\n"
        "if FLAG:\n    C = 'c'\nelse:\n    C = 'd'\n"
        "try:\n    D = 'x'\nexcept Exception:\n    D = 'y'\n"
        "class K:\n    E = 'e'\n    N = 1\n",
        encoding="utf-8",
    )
    assert string_constant_names([tmp_path / "q.py"]) == {"A", "B", "C", "D", "E"}
    (tmp_path / "use.py").write_text("from q import A, K\n\ndef f(s, k):\n    return s is A, s is K.E, s is k.E, s is K.N\n", encoding="utf-8")
    problems = find_identity_comparisons([tmp_path / "q.py", tmp_path / "use.py"], root=tmp_path)
    assert [p.split("`")[1] for p in problems] == ["s is A", "s is K.E", "s is k.E"]


def test_a_same_named_sentinel_elsewhere_is_not_a_string_constant(tmp_path):
    (tmp_path / "a.py").write_text('MISSING = "missing"\n\ndef f(x):\n    return x is MISSING\n', encoding="utf-8")
    (tmp_path / "b.py").write_text("MISSING = object()\n\ndef g(x):\n    return x is MISSING\n", encoding="utf-8")
    problems = find_identity_comparisons([tmp_path / "a.py", tmp_path / "b.py"], root=tmp_path)
    assert [p.split(":")[0] for p in problems] == ["a.py"]


def test_bom_and_unparsable_files(tmp_path):
    (tmp_path / "bom.py").write_bytes(b'\xef\xbb\xbfSQL = "x"\n\ndef f(a):\n    return a is SQL\n')
    assert _lines(find_identity_comparisons([tmp_path / "bom.py"], root=tmp_path)) == ["4"]
    (tmp_path / "ok.py").write_text("X = 1\n", encoding="utf-8")
    assert_no_identity_comparisons([tmp_path / "ok.py"])
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"broken.py"):
        assert_no_identity_comparisons([tmp_path / "ok.py", tmp_path / "broken.py"])
