"""Tests for py_ci_shared.statement_real_engine_coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.statement_real_engine_coverage import assert_every_statement_has_an_engine_test, find_statements_without_engine_test

BOM = b"\xef\xbb\xbf"

QUERIES = (
    'SEED_COVERED_SQL = "SELECT id FROM seed_jobs WHERE open"\n'
    'SEED_UNCOVERED_SQL = "UPDATE seed_jobs SET open = false WHERE id = %s"\n'
    'SEED_PROSE = "Withdrawal failed"\n'
)
ENGINE_TEST = "from seedpkg.queries import SEED_COVERED_SQL\n\n\ndef test_it(pg_cursor):\n    pg_cursor.execute(SEED_COVERED_SQL)\n"

KW = {"engine_test_dirs": ["tests/integration"], "exclude_top_dirs": ["tests"], "use_git": False}


def _corpus(tmp_path: Path, files: dict[str, bytes | str]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _base(**extra: bytes | str) -> dict[str, bytes | str]:
    return {"seedpkg/queries.py": QUERIES, "tests/integration/test_q.py": ENGINE_TEST, **extra}


def test_reports_the_seeded_violation(tmp_path):
    found = find_statements_without_engine_test(_corpus(tmp_path, _base()), **KW)
    assert [(f.path, f.line, f.rule, f.key) for f in found] == [
        ("seedpkg/queries.py", 2, "statement-without-engine-test", "seedpkg.queries.SEED_UNCOVERED_SQL")
    ]
    assert "Add one" in found[0].message and "tests/integration" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    files = _base(**{"tests/integration/test_q.py": ENGINE_TEST + "\n\ndef test_two(pg_cursor):\n    pg_cursor.execute(q.SEED_UNCOVERED_SQL)\n"})
    assert find_statements_without_engine_test(_corpus(tmp_path, files), **KW) == []


def test_a_reference_from_a_unit_test_outside_the_tier_does_not_count(tmp_path):
    files = _base(
        **{
            "tests/unit/test_fake.py": "from seedpkg.queries import SEED_UNCOVERED_SQL\n\n\ndef test_it(fake_cursor):\n    fake_cursor.execute(SEED_UNCOVERED_SQL)\n"
        }
    )
    assert [f.key for f in find_statements_without_engine_test(_corpus(tmp_path, files), **KW)] == ["seedpkg.queries.SEED_UNCOVERED_SQL"]


def test_an_import_alias_is_followed_one_level(tmp_path):
    files = _base(
        **{
            "seedpkg/api.py": "from seedpkg.queries import SEED_UNCOVERED_SQL as CLOSE_SQL\n",
            "tests/integration/test_q.py": ENGINE_TEST + "\n\ndef test_two(pg_cursor):\n    pg_cursor.execute(api.CLOSE_SQL)\n",
        }
    )
    assert find_statements_without_engine_test(_corpus(tmp_path, files), **KW) == []


def test_an_alias_in_the_engine_test_itself_credits_the_original(tmp_path):
    files = _base(
        **{"tests/integration/test_q.py": "from seedpkg.queries import SEED_COVERED_SQL, SEED_UNCOVERED_SQL as Q\n\n\ndef test_it(c):\n    c.execute(Q)\n"}
    )
    assert find_statements_without_engine_test(_corpus(tmp_path, files), **KW) == []


def test_a_string_mention_is_not_a_reference(tmp_path):
    files = _base(**{"tests/integration/test_q.py": ENGINE_TEST + '\n\n# SEED_UNCOVERED_SQL\nNOTE = "SEED_UNCOVERED_SQL"\n'})
    assert [f.key for f in find_statements_without_engine_test(_corpus(tmp_path, files), **KW)] == ["seedpkg.queries.SEED_UNCOVERED_SQL"]


def test_marker_tier_counts_a_marked_function_only(tmp_path):
    marked = (
        "import pytest\nfrom seedpkg import queries\n\n\n@pytest.mark.integration\ndef test_real(pg):\n    pg.execute(queries.SEED_COVERED_SQL)\n\n\n"
        "def test_fake(cur):\n    cur.execute(queries.SEED_UNCOVERED_SQL)\n"
    )
    files = {"seedpkg/queries.py": QUERIES, "tests/test_q.py": marked}
    kw = {"engine_test_dirs": [], "engine_markers": ["integration"], "exclude_top_dirs": ["tests"], "use_git": False}
    assert [f.key for f in find_statements_without_engine_test(_corpus(tmp_path, files), **kw)] == ["seedpkg.queries.SEED_UNCOVERED_SQL"]


def test_marker_tier_counts_a_module_pytestmark_whole_file(tmp_path):
    marked = "import pytest\nfrom seedpkg import queries\n\npytestmark = [pytest.mark.integration]\n\n\ndef test_a(pg):\n    pg.execute(queries.SEED_COVERED_SQL, queries.SEED_UNCOVERED_SQL)\n"
    kw = {"engine_test_dirs": [], "engine_markers": ["integration"], "exclude_top_dirs": ["tests"], "use_git": False}
    assert find_statements_without_engine_test(_corpus(tmp_path, {"seedpkg/queries.py": QUERIES, "tests/test_q.py": marked}), **kw) == []


def test_a_tier_must_be_defined(tmp_path):
    with pytest.raises(ValueError, match="engine_test_dirs"):
        find_statements_without_engine_test(_corpus(tmp_path, _base()), engine_test_dirs=[], exclude_top_dirs=["tests"], use_git=False)


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_statements_without_engine_test(_corpus(tmp_path / "a", _base()), **KW)
    bom = find_statements_without_engine_test(_corpus(tmp_path / "b", {k: BOM + str(v).encode("utf-8") for k, v in _base().items()}), **KW)
    assert [f.key for f in bom] == [f.key for f in plain] == ["seedpkg.queries.SEED_UNCOVERED_SQL"]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, _base(**{"seedpkg/broken.py": "def broken(:\n"}))
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_statements_without_engine_test(root, **KW)
    assert len(find_statements_without_engine_test(root, allow_unparsed=True, **KW)) == 1


def test_an_unparsable_engine_test_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, _base(**{"tests/integration/test_bad.py": "def broken(:\n"}))
    with pytest.raises(UnparsedFilesError, match=r"test_bad.py"):
        find_statements_without_engine_test(root, **KW)


def test_an_empty_corpus_fails_the_floor(tmp_path):
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    with pytest.raises(EmptyScanError):
        find_statements_without_engine_test(tmp_path, **KW)


def test_a_scan_that_finds_no_statements_fails_instead_of_passing(tmp_path):
    root = _corpus(tmp_path, {"seedpkg/m.py": "X = 1\n", "tests/integration/test_q.py": "def test_a():\n    pass\n"})
    with pytest.raises(EmptyScanError, match="statement constant"):
        find_statements_without_engine_test(root, **KW)


def test_an_empty_engine_tier_fails_instead_of_listing_everything_as_a_finding(tmp_path):
    root = _corpus(tmp_path, {"seedpkg/queries.py": QUERIES})
    (root / "tests" / "integration").mkdir(parents=True)
    with pytest.raises(EmptyScanError, match="real-engine"):
        find_statements_without_engine_test(root, **KW)


def test_assert_without_a_baseline_fails_with_the_fix_verb(tmp_path):
    with pytest.raises(AssertionError, match=r"(?s)Add a real-engine test.*SEED_UNCOVERED_SQL"):
        assert_every_statement_has_an_engine_test(_corpus(tmp_path, _base()), **KW)


def _write_baseline(path: Path, entries: dict[str, str]) -> None:
    path.write_text(
        json.dumps({"schema": 1, "gate": "statement_real_engine_coverage", "entries": {k: {"count": 1, "note": v} for k, v in entries.items()}}),
        encoding="utf-8",
    )


def test_baseline_accepts_a_listed_statement_with_a_reason(tmp_path):
    root = _corpus(tmp_path / "c", _base())
    _write_baseline(tmp_path / "b.json", {"seedpkg.queries.SEED_UNCOVERED_SQL": "closes jobs; engine test lands with the queue rewrite"})
    assert_every_statement_has_an_engine_test(root, tmp_path / "b.json", **KW)


def test_baseline_rejects_a_new_statement(tmp_path):
    root = _corpus(tmp_path / "c", _base())
    _write_baseline(tmp_path / "b.json", {})
    with pytest.raises(pytest.fail.Exception, match="SEED_UNCOVERED_SQL"):
        assert_every_statement_has_an_engine_test(root, tmp_path / "b.json", **KW)


def test_baseline_entry_that_now_has_an_engine_test_is_stale(tmp_path):
    root = _corpus(tmp_path / "c", _base())
    _write_baseline(tmp_path / "b.json", {"seedpkg.queries.SEED_UNCOVERED_SQL": "reason", "seedpkg.queries.SEED_COVERED_SQL": "has a test now"})
    with pytest.raises(pytest.fail.Exception, match="SEED_COVERED_SQL"):
        assert_every_statement_has_an_engine_test(root, tmp_path / "b.json", **KW)


def test_refresh_shrinks_and_never_adds(tmp_path):
    root = _corpus(tmp_path / "c", _base())
    baseline = tmp_path / "b.json"
    _write_baseline(baseline, {"seedpkg.queries.SEED_COVERED_SQL": "stale"})
    with pytest.raises(pytest.fail.Exception, match="SEED_UNCOVERED_SQL"):
        assert_every_statement_has_an_engine_test(root, baseline, refresh=True, **KW)
    assert json.loads(baseline.read_text(encoding="utf-8"))["entries"] == {}


def test_a_baseline_entry_without_a_reason_is_rejected(tmp_path):
    root = _corpus(tmp_path / "c", _base())
    _write_baseline(tmp_path / "b.json", {"seedpkg.queries.SEED_UNCOVERED_SQL": "NEEDS-JUSTIFICATION: say why"})
    with pytest.raises(pytest.fail.Exception, match="NEEDS-JUSTIFICATION"):
        assert_every_statement_has_an_engine_test(root, tmp_path / "b.json", **KW)
