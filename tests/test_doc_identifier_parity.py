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


def test_the_default_corpus_skips_gitignored_files_and_keeps_untracked_ones(tmp_path):
    """A gitignored data dump is not where identifiers are defined, and reading it raised MemoryError on glossum."""
    import subprocess

    from py_ci_shared.doc_identifier_parity import default_corpus_files

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("data/\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dump.json").write_text("{}", encoding="utf-8")
    (tmp_path / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    names = {p.name for p in default_corpus_files(tmp_path)}
    assert "new_module.py" in names
    assert "dump.json" not in names


@pytest.mark.parametrize("runner", ["uv run", "uv run --with rich", "poetry run"])
def test_a_runner_does_not_make_our_own_script_flags_foreign(tmp_path: Path, runner: str):
    doc = f"```\n{runner} python tool.py --mispeled-flag\n```\nPass `--mispeled-flag`.\n"
    root, docs, corpus = _repo(tmp_path, doc, source="parser.add_argument('--misspelled-flag')\n")
    assert len(_find(root, docs, corpus)) == 1


def test_a_runner_launching_an_external_tool_still_excuses_its_flags(tmp_path: Path):
    doc = "```\nuv run python -m pip install --upgrade-strategy eager x\nuvx ruff check --output-format github\nuv run --with rich python x.py\n```\n"
    doc += "`--upgrade-strategy`, `--output-format` and `--with` are other tools' flags.\n"
    root, docs, corpus = _repo(tmp_path, doc)
    assert _find(root, docs, corpus) == []


def test_a_non_ascii_untracked_file_is_in_the_default_corpus(tmp_path):
    import subprocess

    from py_ci_shared.doc_identifier_parity import default_corpus_files

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "déjà.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    assert "déjà.py" in {p.name for p in default_corpus_files(tmp_path)}


def test_a_doc_outside_the_root_does_not_raise(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "run.py").write_text("", encoding="utf-8")
    doc = tmp_path / "elsewhere.md"
    doc.write_text("Pass `--absent-flag`.\n", encoding="utf-8")
    problems = _find(root, [doc], [root / "run.py"])
    assert len(problems) == 1 and "elsewhere.md:1:" in problems[0]


def test_a_bom_doc_is_read(tmp_path: Path):
    root, docs, corpus = _repo(tmp_path, "")
    docs[0].write_bytes(b"\xef\xbb\xbf`--absent-flag` on line one\n")
    assert [p.split(": ", 1)[0] for p in _find(root, docs, corpus)] == ["README.md:1"]
