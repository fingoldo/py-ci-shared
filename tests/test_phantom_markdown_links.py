"""Unit tests for the phantom-markdown-link check (llm_bench audit finding).

Real scratch .md files, same no-mocking convention as this package's
other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.phantom_markdown_links import assert_no_phantom_markdown_links, find_phantom_markdown_links


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_dead_link_target_detected(tmp_path):
    readme = _write(tmp_path, "README.md", "See [architecture](docs/does_not_exist.md) for details.\n")
    violations = find_phantom_markdown_links([readme], tmp_path)
    assert len(violations) == 1
    assert "does_not_exist.md" in violations[0]


def test_link_resolving_against_repo_root_not_flagged(tmp_path):
    (tmp_path / "docs").mkdir()
    _write(tmp_path, "docs/architecture.md", "notes\n")
    readme = _write(tmp_path, "README.md", "See [architecture](docs/architecture.md) for details.\n")
    assert find_phantom_markdown_links([readme], tmp_path) == []


def test_link_resolving_against_referencing_files_own_dir_not_flagged(tmp_path):
    subdir = tmp_path / "docs"
    subdir.mkdir()
    _write(tmp_path, "docs/sibling.md", "notes\n")
    doc = _write(tmp_path, "docs/index.md", "See [sibling](sibling.md) for details.\n")
    assert find_phantom_markdown_links([doc], tmp_path) == []


@pytest.mark.parametrize("prefix", ["http://example.com/", "https://example.com/", "git+https://example.com/", "mailto:x@example.com/"])
def test_external_links_never_flagged(tmp_path, prefix):
    readme = _write(tmp_path, "README.md", f"See [x]({prefix}setup.md) for details.\n")
    assert find_phantom_markdown_links([readme], tmp_path) == []


def test_prose_without_link_syntax_not_flagged(tmp_path):
    readme = _write(tmp_path, "README.md", "Configured in round_runner.py and docs/architecture.md.\n")
    assert find_phantom_markdown_links([readme], tmp_path) == []


def test_multiple_dead_links_all_reported(tmp_path):
    readme = _write(tmp_path, "README.md", "[a](missing_a.md) and [b](missing_b.py)\n")
    violations = find_phantom_markdown_links([readme], tmp_path)
    assert len(violations) == 2


class TestAssertNoPhantomMarkdownLinks:
    def test_fails_on_dead_link(self, tmp_path):
        readme = _write(tmp_path, "README.md", "[x](missing.md)\n")
        with pytest.raises(pytest.fail.Exception, match=r"missing.md"):
            assert_no_phantom_markdown_links([readme], tmp_path)

    def test_passes_when_clean(self, tmp_path):
        readme = _write(tmp_path, "README.md", "no links here\n")
        assert_no_phantom_markdown_links([readme], tmp_path)  # does not raise
