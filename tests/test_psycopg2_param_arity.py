"""Tests for py_ci_shared.psycopg2_param_arity."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.psycopg2_param_arity import assert_psycopg2_param_arity, find_psycopg2_param_arity, placeholders

BOM = b"\xef\xbb\xbf"
SHORT = b'_SQL = "UPDATE jobs SET state = %s, note = %s WHERE uid = %s"\n\n\ndef mark(cur, uid):\n    cur.execute(_SQL, ("done", uid))\n'


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _one(tmp_path: Path, body: str) -> list:
    root = _corpus(tmp_path, {"m.py": body.encode()})
    return find_psycopg2_param_arity(root, use_git=False)


def test_reports_the_seeded_violation(tmp_path):
    (finding,) = find_psycopg2_param_arity(_corpus(tmp_path, {"m.py": SHORT}), use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 5, "psycopg2-param-arity")
    assert "has 3 %s placeholder(s) but is given 2 parameter(s)" in finding.message and "`execute(_SQL, ...)`" in finding.message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _one(tmp_path, SHORT.decode().replace('("done", uid)', '("done", None, uid)')) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_psycopg2_param_arity(_corpus(tmp_path / "a", {"m.py": SHORT}), use_git=False)
    bom = find_psycopg2_param_arity(_corpus(tmp_path / "b", {"m.py": BOM + SHORT}), use_git=False)
    assert [(f.line, f.message) for f in plain] == [(f.line, f.message) for f in bom] and plain


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_psycopg2_param_arity(root, use_git=False)
    assert find_psycopg2_param_arity(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_psycopg2_param_arity(tmp_path, min_files=1, use_git=False)


def test_a_doubled_percent_is_a_literal_and_not_a_placeholder():
    assert placeholders("a ILIKE '%%suspended%%' AND b = %s") == ([], 1)


def test_a_statement_carrying_a_percent_literal_next_to_its_one_parameter_is_clean(tmp_path):
    assert _one(tmp_path, "def f(cur, x):\n    cur.execute(\"SELECT 1 WHERE a ILIKE '%%suspended%%' AND b = %s\", (x,))\n") == []


def test_a_plus_chain_and_a_constant_built_from_a_constant_are_resolved(tmp_path):
    body = '_A = "SELECT 1 WHERE a = %s"\n_B = _A + " AND b = %s"\n\n\ndef f(cur):\n    cur.execute(_B, (1,))\n'
    (finding,) = _one(tmp_path, body)
    assert "has 2 %s placeholder(s) but is given 1" in finding.message


def test_too_many_parameters_are_reported_too(tmp_path):
    (finding,) = _one(tmp_path, 'def f(cur):\n    cur.execute("SELECT %s", (1, 2))\n')
    assert "given 2" in finding.message


def test_a_zero_placeholder_statement_given_a_parameter_is_reported(tmp_path):
    (finding,) = _one(tmp_path, 'def f(cur):\n    cur.execute("SELECT 1", (1,))\n')
    assert "has 0 %s placeholder(s) but is given 1" in finding.message


def test_a_named_placeholder_missing_from_the_dict_is_reported_and_an_extra_key_is_not(tmp_path):
    body = 'Q = "SELECT %(a)s, %(b)s, %(a)s"\n\n\ndef f(cur):\n    cur.execute(Q, {"a": 1})\n    cur.execute(Q, {"a": 1, "b": 2, "c": 3})\n'
    (finding,) = _one(tmp_path, body)
    assert finding.line == 5 and "%(b)s" in finding.message


def test_a_dict_against_positional_and_a_tuple_against_named_are_reported(tmp_path):
    body = 'def f(cur):\n    cur.execute("SELECT %s", {"a": 1})\n    cur.execute("SELECT %(a)s", (1,))\n'
    assert [f.line for f in _one(tmp_path, body)] == [2, 3]


def test_mixed_styles_in_one_statement_are_reported(tmp_path):
    (finding,) = _one(tmp_path, 'def f(cur):\n    cur.execute("SELECT %s, %(a)s", (1,))\n')
    assert "mixes" in finding.message


@pytest.mark.parametrize(
    "call",
    [
        'cur.execute("SELECT %s, %s", (*xs,))',
        'cur.execute("SELECT %(a)s", {**d})',
        'cur.execute("SELECT %(a)s", {k: 1})',
        'cur.execute("SELECT %s, %s", params)',
        'cur.execute("SELECT %s, %s")',
        'cur.execute(f"SELECT %s, {x}", (1,))',
        "cur.execute(build(), (1,))",
        'cur.execute("SELECT {cols} %s".format(cols=c), (1, 2))',
        "cur.execute(UNKNOWN, (1, 2))",
    ],
)
def test_what_cannot_be_proved_is_not_reported(tmp_path, call):
    assert _one(tmp_path, f"def f(cur, xs, d, k, params, x, c):\n    {call}\n") == []


def test_another_drivers_placeholder_style_is_not_psycopg2s_business(tmp_path):
    body = "def f(conn):\n    conn.execute('INSERT INTO t VALUES (?, ?)', [1, 2])\n    conn.execute('SELECT $1', [1])\n"
    assert _one(tmp_path, body) == []


def test_a_name_bound_twice_is_not_resolved(tmp_path):
    body = '_S = "SELECT %s"\n_S = "SELECT %s, %s"\n\n\ndef f(cur):\n    cur.execute(_S, (1,))\n'
    assert _one(tmp_path, body) == []


def test_sql_calls_extends_the_set_of_callees(tmp_path):
    root = _corpus(tmp_path, {"m.py": b'def f(db):\n    db.fetch_rows("SELECT %s, %s", (1,))\n'})
    assert find_psycopg2_param_arity(root, use_git=False) == []
    assert len(find_psycopg2_param_arity(root, sql_calls={"fetch_rows": (0, 1)}, use_git=False)) == 1


def test_assert_fails_with_the_finding_and_passes_through_a_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path, {"m.py": SHORT})
    with pytest.raises(AssertionError, match=r"m.py:5"):
        assert_psycopg2_param_arity(root, use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):  # a refresh reports itself by skipping
        assert_psycopg2_param_arity(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_psycopg2_param_arity(root, baseline_path=baseline, use_git=False)
