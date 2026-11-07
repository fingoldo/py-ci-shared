"""Tests for py_ci_shared.cli_flag_spelling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cli_flag_spelling import assert_cli_flag_spelling, find_cli_flag_spelling_splits

BOM = b"\xef\xbb\xbf"


def _script(*flags: str) -> str:
    return "import argparse\np = argparse.ArgumentParser()\n" + "".join(f"p.add_argument({f}, action='store_true')\n" for f in flags)


def _write(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _find(tmp_path: Path, files: dict):
    _write(tmp_path, files)
    return find_cli_flag_spelling_splits(tmp_path, use_git=False)


def test_reports_the_seeded_violation_on_the_minority_spelling(tmp_path):
    found = _find(tmp_path, {"a.py": _script("'--dry_run'"), "b.py": _script("'--dry_run'"), "c.py": _script("'--dry-run'")})
    assert [(f.path, f.line, f.rule) for f in found] == [("c.py", 3, "cli-flag-spelling-split")]
    assert found[0].message.startswith("--dry-run is spelled differently from --dry_run elsewhere (--dry-run x1, --dry_run x2)")


def test_a_tie_keeps_the_alphabetically_first_spelling(tmp_path):
    found = _find(tmp_path, {"a.py": _script("'--dry_run'"), "b.py": _script("'--dry-run'")})
    assert [(f.path, f.message.split()[0]) for f in found] == [("a.py", "--dry_run")]


def test_an_alias_call_closes_the_split(tmp_path):
    files = {"a.py": _script("'--dry_run'"), "b.py": _script("'--dry_run'"), "c.py": _script("'--dry-run', '--dry_run'")}
    assert _find(tmp_path, files) == []


def test_one_spelling_everywhere_single_dash_and_other_stems_are_clean(tmp_path):
    files = {"a.py": _script("'--dry-run'", "'-v'", "'--batch-size'"), "b.py": _script("'--dry-run'", "'-v'", "'--batch_size_total'")}
    assert _find(tmp_path, files) == []


def test_variable_flags_are_ignored(tmp_path):
    assert _find(tmp_path, {"a.py": _script("FLAG"), "b.py": _script("'--x-y'")}) == []


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    a, b = _script("'--dry_run'"), _script("'--dry-run'")
    plain = _find(tmp_path / "b", {"a.py": BOM + a.encode(), "b.py": BOM + b.encode(), "c.py": a})
    assert [(f.path, f.line) for f in plain] == [("b.py", 3)]
    crlf = _find(tmp_path / "c", {"a.py": a.replace("\n", "\r\n"), "b.py": b.replace("\n", "\r\n"), "c.py": a})
    assert [(f.path, f.line) for f in crlf] == [("b.py", 3)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, {"a.py": _script("'--dry_run'"), "b.py": _script("'--dry-run'"), "c.py": _script("'--dry_run'"), "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_cli_flag_spelling_splits(tmp_path, use_git=False)
    assert len(find_cli_flag_spelling_splits(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_cli_flag_spelling_splits(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    _write(tmp_path, {"a.py": _script("'--dry_run'"), "b.py": _script("'--dry_run'"), "c.py": _script("'--dry-run'")})
    with pytest.raises(AssertionError, match=r"--dry-run"):
        assert_cli_flag_spelling(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_cli_flag_spelling_splits(tmp_path, use_git=False)
    base.write_text(json.dumps({"schema": 1, "gate": "cli_flag_spelling", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8")
    assert_cli_flag_spelling(tmp_path, use_git=False, baseline_path=base)
    _write(tmp_path, {"d.py": _script("'--x-y'"), "e.py": _script("'--x_y'"), "f.py": _script("'--x_y'")})
    with pytest.raises(pytest.fail.Exception, match=r"d\.py"):
        assert_cli_flag_spelling(tmp_path, use_git=False, baseline_path=base)


def test_the_shortest_possible_long_option_is_still_compared(tmp_path):
    """Three characters is the floor (`--` alone is the end-of-options marker). Only `-` and `_` count as spelling variants, so the
    shortest splittable stem is the lone separator: `--_` x2 against `---` x1. (Audit 44-F8 proposed `--n` vs `--N`; case never splits.)"""
    found = _find(tmp_path, {"a.py": _script("'--_'"), "b.py": _script("'--_'"), "c.py": _script("'---'")})
    assert [(f.path, f.message.split()[0]) for f in found] == [("c.py", "---")]
