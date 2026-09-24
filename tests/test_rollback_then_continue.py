from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.rollback_then_continue import RULE, assert_no_rollback_then_continue, find_rollback_then_continue

BAD = """
def importer(session, rows):
    imported = 0
    for row in rows:
        try:
            session.add(row)
            imported += 1
            if imported % 100 == 0:
                session.commit()
        except Exception:
            session.rollback()
    session.commit()
"""


def _write(root: Path, rel: str, body: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + textwrap.dedent(body).encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _lines(root: Path, **kw: object) -> list[int]:
    return [f.line for f in find_rollback_then_continue(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


def test_batched_writes_rolled_back_then_continued_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", BAD)
    findings = find_rollback_then_continue(tmp_path, use_git=False)
    assert [(f.line, f.message) for f in findings] == [
        (10, "importer: except handler rolls the whole transaction back inside a loop and carries on without resetting")
    ]


@pytest.mark.parametrize(
    "body",
    [
        # async session, literal SQL write, while loop
        "async def f(session, q):\n    while q:\n        try:\n            await session.execute(text('INSERT INTO t VALUES (1)'))\n        except Exception:\n            await session.rollback()\n",
        # DDL in a loop with no commit
        "def f(s, tables):\n    for t in tables:\n        try:\n            s.execute(f'TRUNCATE TABLE {t} CASCADE')\n        except Exception:\n            s.rollback()\n",
        # psycopg2 bulk helper, commit only every N rows
        "def f(db, rows, sql):\n    for i, r in enumerate(rows):\n        try:\n            execute_values(db.cur, sql, [r])\n            if len(rows) > 10:\n                db.commit()\n        except Exception:\n            db.rollback()\n",
        # writes in the outer loop, rollback in the inner one
        "def f(session, groups):\n    for g in groups:\n        session.add(g)\n        for item in g:\n            try:\n                check(item)\n            except ValueError:\n                session.rollback()\n",
    ],
)
def test_write_shapes_are_flagged(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert len(_lines(tmp_path)) == 1


@pytest.mark.parametrize(
    "fix",
    [
        (
            "        except Exception:\n            session.rollback()\n",
            "        except Exception:\n            session.rollback()\n            imported = 0\n",
        ),
        ("        except Exception:\n            session.rollback()\n", "        except Exception:\n            session.rollback()\n            raise\n"),
        ("        except Exception:\n            session.rollback()\n", "        except Exception:\n            session.rollback()\n            break\n"),
        (
            "        except Exception:\n            session.rollback()\n",
            "        except Exception:\n            session.rollback()\n            return imported\n",
        ),
        (
            "        except Exception:\n            session.rollback()\n",
            "        except Exception:\n            session.rollback()\n            pending.clear()\n",
        ),
        (
            "        except Exception:\n            session.rollback()\n",
            "        except Exception:\n            session.rollback()\n        finally:\n            batch = []\n",
        ),
        ("            session.add(row)\n", "            session.begin_nested()\n            session.add(row)\n"),
        ("            if imported % 100 == 0:\n                session.commit()\n", "            session.commit()\n"),
        ("            if imported % 100 == 0:\n                session.commit()\n", "            if not dry_run:\n                session.commit()\n"),
    ],
)
def test_each_mitigation_clears_the_finding(tmp_path: Path, fix: tuple[str, str]) -> None:
    old, new = fix
    assert old in BAD
    _write(tmp_path, "m.py", BAD.replace(old, new))
    assert _lines(tmp_path) == []


def test_marker_on_the_handler_line_opts_out(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", BAD.replace("        except Exception:\n", "        except Exception:  # rollback-continue-ok: the run restarts from scratch\n"))
    assert _lines(tmp_path) == []


@pytest.mark.parametrize(
    "body",
    [
        # read-only probe: rolling back an aborted SELECT is correct, and `.add` on a set is not a session write
        "def f(conn, cur, shapes, seen):\n    for sql in shapes:\n        try:\n            cur.execute(sql)\n            seen.add(1)\n        except Exception:\n            conn.rollback()\n",
        # rollback outside any loop
        "def f(session, row):\n    try:\n        session.add(row)\n    except Exception:\n        session.rollback()\n",
        # loop inside a nested function does not make the outer try loop-bound
        "def f(session, rows):\n    try:\n        session.add(rows)\n    except Exception:\n        session.rollback()\n    def g():\n        for r in rows:\n            pass\n",
        # handler without rollback
        "def f(session, rows):\n    for r in rows:\n        try:\n            session.add(r)\n        except Exception:\n            log(r)\n",
    ],
)
def test_negative_controls(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert _lines(tmp_path) == []


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/helpers.py", BAD)
    assert _lines(tmp_path) == []
    assert _lines(tmp_path, include_tests=True) == [10]


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", BAD, bom=True)
    assert _lines(tmp_path) == [10]


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_rollback_then_continue(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    assert [f.rule for f in find_rollback_then_continue(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_rollback_then_continue(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", BAD.replace("            session.rollback()\n", "            session.rollback()\n            raise\n"))
    assert_no_rollback_then_continue(src, use_git=False)
    _write(src, "m.py", BAD)
    with pytest.raises(pytest.fail.Exception, match=r"\[rollback-then-continue\]"):
        assert_no_rollback_then_continue(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_rollback_then_continue(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_rollback_then_continue(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_rollback_then_continue(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", BAD + BAD.replace("def importer", "def importer2").replace("importer:", "importer2:"))
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_rollback_then_continue(src, baseline_path=baseline, refresh=False, use_git=False)
