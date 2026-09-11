"""Unit tests for the shared Alembic CONCURRENTLY check, on real scratch migration files."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.alembic_concurrently import assert_concurrently_is_in_autocommit_blocks, find_unguarded_concurrently

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


def test_a_baseline_admits_old_sites_and_fails_on_a_stale_one(tmp_path):
    versions = _versions(tmp_path, b=_UNGUARDED)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(["b.py:4", "b.py:5"]), encoding="utf-8")
    assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)

    baseline.write_text(json.dumps(["b.py:4", "b.py:5", "gone.py:9"]), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="no longer occur"):
        assert_concurrently_is_in_autocommit_blocks(versions, baseline=baseline)


def test_an_empty_versions_dir_is_not_a_pass(tmp_path):
    (tmp_path / "versions").mkdir()
    with pytest.raises(pytest.fail.Exception, match="reading nothing"):
        assert_concurrently_is_in_autocommit_blocks(tmp_path / "versions")
