"""exists_exact_case and the reference gates that use it (audit 33-F4). On a case-sensitive filesystem the gates already
agreed; on Windows and macOS, Path.exists() said yes to a wrong-case spelling that 404s on a Linux runner."""

from __future__ import annotations

from py_ci_shared._core import exists_exact_case
from py_ci_shared.comment_repo_paths import find_comment_repo_path_problems
from py_ci_shared.phantom_markdown_links import find_phantom_markdown_links


def test_exists_exact_case_compares_the_last_component_and_every_parent(tmp_path):
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "exists.sh").write_text("x", encoding="utf-8")
    assert exists_exact_case(tmp_path / "tool" / "exists.sh", tmp_path)
    assert not exists_exact_case(tmp_path / "tool" / "EXISTS.sh", tmp_path)
    assert not exists_exact_case(tmp_path / "TOOL" / "exists.sh", tmp_path)
    assert not exists_exact_case(tmp_path / "tool" / "missing.sh", tmp_path)
    assert exists_exact_case(tmp_path / "tool")
    assert not exists_exact_case(tmp_path / "TOOL")


def test_a_wrong_case_markdown_link_is_dead(tmp_path):
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("[a](REAL.md) and [b](real.md)\n", encoding="utf-8")
    assert find_phantom_markdown_links([readme], tmp_path) == ["README.md:1: dead markdown-link target 'REAL.md'"]


def test_a_wrong_case_comment_path_is_reported(tmp_path):
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "exists.sh").write_text("x", encoding="utf-8")
    (tmp_path / "m.py").write_text("# see tool/EXISTS.sh\n# see tool/exists.sh\n", encoding="utf-8")
    problems = find_comment_repo_path_problems(tmp_path, roots=("tool",))
    assert [(p.line, p.message) for p in problems] == [(1, "names `tool/EXISTS.sh`, which does not exist under the repo root")]
