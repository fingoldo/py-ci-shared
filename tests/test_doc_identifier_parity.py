"""Tests for py_ci_shared.doc_identifier_parity: the near miss it exists for, and each exclusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.doc_identifier_parity import assert_doc_identifiers_exist, find_absent_doc_identifiers


def _repo(tmp_path: Path, doc: str, source: str = "") -> tuple[Path, list[Path], list[Path]]:
    (tmp_path / "README.md").write_text(doc, encoding="utf-8")
    (tmp_path / "run.py").write_text(source, encoding="utf-8")
    return tmp_path, [tmp_path / "README.md"], [tmp_path / "run.py"]


def _find(root: Path, docs: list[Path], corpus: list[Path], **kwargs) -> list[str]:
    return find_absent_doc_identifiers(root, doc_files=docs, corpus_files=corpus, **kwargs)


def test_the_near_miss_is_reported(tmp_path: Path):
    """The README says `--grammar`; the script declares only `--no-grammar`, which does not contain `--grammar`."""
    root, docs, corpus = _repo(tmp_path, "Pass `--grammar` to annotate.\n", 'parser.add_argument("--no-grammar")\n')
    assert _find(root, docs, corpus) == ["README.md:1: `--grammar` occurs nowhere in the source, config or tests"]


def test_a_flag_that_exists_nowhere_is_reported_with_its_line(tmp_path: Path):
    root, docs, corpus = _repo(tmp_path, "intro\nPass `--annotate-all` to annotate.\n", 'parser.add_argument("--no-grammar")\n')
    assert _find(root, docs, corpus) == ["README.md:2: `--annotate-all` occurs nowhere in the source, config or tests"]


def test_an_identifier_needs_two_underscores_and_must_exist(tmp_path: Path):
    doc = "`feature_grammar_annotations` enables it; `one_underscore` is prose.\n"
    root, docs, corpus = _repo(tmp_path, doc, "feature_x = 1\n")
    assert [p.split("`")[1] for p in _find(root, docs, corpus)] == ["feature_grammar_annotations"]


def test_html_comments_are_not_read_and_fenced_blocks_are(tmp_path: Path):
    doc = "<!-- audit note: `--old-flag` was renamed -->\n```\npython run.py `--missing-flag`\n```\n"
    root, docs, corpus = _repo(tmp_path, doc)
    assert [p.split("`")[1] for p in _find(root, docs, corpus)] == ["--missing-flag"]


def test_a_flag_the_doc_shows_belonging_to_another_tool_is_not_ours(tmp_path: Path):
    """pip's `--force-reinstall`, shown on a fenced pip line, is explained in prose without being reported."""
    doc = "```\npip install --no-deps --force-reinstall pkg\n```\n`--force-reinstall` is there because pip keeps what is installed.\n"
    root, docs, corpus = _repo(tmp_path, doc)
    assert _find(root, docs, corpus) == []


def test_python_dash_m_pip_is_an_external_command_too(tmp_path: Path):
    doc = "```\npython -m pip install --upgrade-strategy eager pkg\n```\nSee `--upgrade-strategy`.\n"
    root, docs, corpus = _repo(tmp_path, doc)
    assert _find(root, docs, corpus) == []


def test_our_own_command_line_does_not_excuse_a_missing_flag(tmp_path: Path):
    doc = "```\npython scripts/run.py --not-declared\n```\nPass `--not-declared`.\n"
    root, docs, corpus = _repo(tmp_path, doc)
    assert len(_find(root, docs, corpus)) == 1


def test_excluded_docs_and_ignored_tokens_are_skipped(tmp_path: Path):
    (tmp_path / "CHANGELOG.md").write_text("Renamed `old_setting_name` long ago.\n", encoding="utf-8")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "next.md").write_text("A future `planned_field_name`.\n", encoding="utf-8")
    root, docs, corpus = _repo(tmp_path, "Use `--external-only`.\n")
    docs += [tmp_path / "CHANGELOG.md", tmp_path / "plans" / "next.md"]
    found = _find(root, docs, corpus, exclude_docs={"CHANGELOG.md", "plans/"}, ignore={"--external-only"})
    assert found == []


def test_the_assert_names_every_problem(tmp_path: Path):
    root, _, _ = _repo(tmp_path, "Pass `--gone-flag`.\n")
    with pytest.raises(pytest.fail.Exception, match=r"README\.md:1: `--gone-flag`"):
        assert_doc_identifiers_exist(root, doc_files=[root / "README.md"], corpus_files=[root / "run.py"])
