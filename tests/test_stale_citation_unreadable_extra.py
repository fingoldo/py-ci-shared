"""An extra-glob file that cannot be read is reported by name, not dropped with its citations unchecked (audit 40-F4)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.stale_source_citations import RULE_UNREADABLE, find_stale_source_citations


def _corpus(tmp_path: Path, readme: bytes) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    (tmp_path / "README.md").write_bytes(readme)
    return tmp_path


def test_an_unreadable_markdown_file_is_named_as_unscanned(tmp_path: Path) -> None:
    """Negative control (40-F4): the old gate skipped the file with `continue`; nothing named it."""
    root = _corpus(tmp_path, b"\xff see src/foo.py:912 for the loop\n")
    findings, _scan = find_stale_source_citations(root, use_git=False, extra_globs=("*.md",))
    named = [f for f in findings if f.rule == RULE_UNREADABLE]
    assert [f.path for f in named] == ["README.md"], findings


def test_a_readable_markdown_citation_is_still_checked(tmp_path: Path) -> None:
    root = _corpus(tmp_path, b"see src/foo.py:912 for the loop\n")
    findings, _scan = find_stale_source_citations(root, use_git=False, extra_globs=("*.md",))
    assert any(f.path == "README.md" and f.rule == "line-past-end" for f in findings), findings
    assert not [f for f in findings if f.rule == RULE_UNREADABLE]
