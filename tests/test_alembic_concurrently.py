"""Unit tests for the shared Alembic CONCURRENTLY check, on real scratch migration files."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.alembic_concurrently import (
    assert_concurrently_is_in_autocommit_blocks,
    collect_unguarded_concurrently,
    find_unguarded_concurrently,
)

_GUARDED = """
from alembic import op
def upgrade():
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY ix_a ON t (a)")
        op.create_index("ix_b", "t", ["b"], postgresql_concurrently=True)
"""

_UNGUARDED = """
from alembic import op
def upgrade():
    op.execute("CREATE INDEX CONCURRENTLY ix_a ON t (a)")
    op.drop_index("ix_b", postgresql_concurrently=True)
    op.execute("CREATE INDEX ix_c ON t (c)")
"""


def _versions(tmp_path: Path, **files: str) -> Path:
    d = tmp_path / "versions"
    d.mkdir()
    for name, body in files.items():
        (d / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return d


def test_both_spellings_inside_the_block_pass(tmp_path):
    assert find_unguarded_concurrently(_versions(tmp_path, a=_GUARDED)) == set()


def test_both_spellings_outside_the_block_are_found_and_a_plain_index_is_not(tmp_path):
    assert find_unguarded_concurrently(_versions(tmp_path, b=_UNGUARDED)) == {"b.py:4", "b.py:5"}


def _keys(versions: Path) -> list[str]:
    return [f.key for f in collect_unguarded_concurrently(versions)[0]]


def test_a_baseline_admits_old_sites_and_fails_on_a_stale_one(tmp_path):
    versions = _versions(tmp_path, b=_UNGUARDED)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_keys(versions)), encoding="utf-8")
    assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)

    baseline.write_text(json.dumps([*_keys(versions), "alembic-concurrently::gone.py::op.execute('x')"]), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="no longer found"):
        assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)


def test_baseline_keys_survive_an_edit_above_the_call(tmp_path):
    versions = _versions(tmp_path, b=_UNGUARDED)
    before = _keys(versions)
    assert before and all(":4" not in k and ":5" not in k for k in before)
    (versions / "b.py").write_text("import os\nimport sys\n" + (versions / "b.py").read_text(encoding="utf-8"), encoding="utf-8")
    assert _keys(versions) == before
    assert find_unguarded_concurrently(versions) == {"b.py:6", "b.py:7"}


def test_a_missing_baseline_fails_and_a_refresh_writes_it(tmp_path, monkeypatch):
    versions = _versions(tmp_path, b=_UNGUARDED)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)
    monkeypatch.setenv("PY_CI_SHARED_REFRESH", "alembic-concurrently")
    with pytest.raises(pytest.skip.Exception):
        assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)
    monkeypatch.delenv("PY_CI_SHARED_REFRESH")
    assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)


@pytest.mark.parametrize(
    "call",
    [
        'op.execute(f"CREATE INDEX CONCURRENTLY {name} ON t (a)")',
        'op.execute("CREATE INDEX " + "CONCURRENTLY ix ON t (a)")',
        "op.execute(SQL)",
        'op.execute(sqltext="DROP INDEX CONCURRENTLY ix")',
        "op.create_index('ix', 't', ['a'], postgresql_concurrently=1)",
    ],
)
def test_every_spelling_of_the_sql_is_seen(tmp_path, call):
    body = f"SQL = 'DROP INDEX CONCURRENTLY ix'\nname = 'ix'\ndef upgrade():\n    {call}\n"
    assert find_unguarded_concurrently(_versions(tmp_path, m=body)) == {"m.py:4"}
    guarded = f"SQL = 'DROP INDEX CONCURRENTLY ix'\nname = 'ix'\ndef upgrade():\n    with op.get_context().autocommit_block():\n        {call}\n"
    (tmp_path / "g").mkdir()
    assert find_unguarded_concurrently(_versions(tmp_path / "g", m=guarded)) == set()


def test_a_non_concurrent_computed_sql_is_not_flagged(tmp_path):
    body = "SQL = 'CREATE INDEX ix ON t (a)'\ndef upgrade():\n    op.execute(SQL)\n    op.execute(f'DROP INDEX {SQL}')\n    op.create_index('i', 't', ['a'], postgresql_concurrently=False)\n"
    assert find_unguarded_concurrently(_versions(tmp_path, m=body)) == set()


def test_an_unparsable_migration_is_reported_not_skipped(tmp_path):
    versions = _versions(tmp_path, a=_GUARDED, bad="def upgrade(:\n    pass\n")
    found = find_unguarded_concurrently(versions)
    assert len(found) == 1 and next(iter(found)).startswith("bad.py:1: unparsable")
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_concurrently_is_in_autocommit_blocks(versions)
    (versions / "bad.py").unlink()
    assert_concurrently_is_in_autocommit_blocks(versions)


def test_async_with_and_bare_name_autocommit_block_are_guards(tmp_path):
    body = (
        "async def upgrade():\n    async with conn.autocommit_block():\n        op.execute('CREATE INDEX CONCURRENTLY a ON t (a)')\n"
        "def up2():\n    with autocommit_block():\n        op.execute('CREATE INDEX CONCURRENTLY b ON t (b)')\n"
        "def up3():\n    with other_block():\n        op.execute('CREATE INDEX CONCURRENTLY c ON t (c)')\n"
    )
    assert find_unguarded_concurrently(_versions(tmp_path, m=body)) == {"m.py:9"}


def test_a_def_nested_in_the_block_is_not_guarded_by_it(tmp_path):
    body = (
        "def upgrade():\n    with c.autocommit_block():\n        def later():\n            op.execute('DROP INDEX CONCURRENTLY x')\n"
        "        op.execute('DROP INDEX CONCURRENTLY y')\n    later()\n"
    )
    assert find_unguarded_concurrently(_versions(tmp_path, m=body)) == {"m.py:4"}


def test_an_empty_versions_dir_is_not_a_pass(tmp_path):
    (tmp_path / "versions").mkdir()
    with pytest.raises(pytest.fail.Exception, match="reading nothing"):
        assert_concurrently_is_in_autocommit_blocks(tmp_path / "versions")
