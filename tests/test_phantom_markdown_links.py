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


def test_tracked_markdown_files_skips_an_untracked_virtualenv(tmp_path):
    """A local .venv is untracked and full of third-party markdown with dead relative links; the repo's own prose is tracked."""
    import subprocess

    from py_ci_shared.phantom_markdown_links import tracked_markdown_files

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path, "README.md", "See [docs](docs/guide.md).\n")
    (tmp_path / "docs").mkdir()
    _write(tmp_path, "docs/guide.md", "notes\n")
    _write(tmp_path, "setup.py", "\n")
    subprocess.run(["git", "add", "README.md", "docs/guide.md", "setup.py"], cwd=tmp_path, check=True)
    venv_doc = tmp_path / ".venv" / "Lib" / "site-packages" / "pkg-1.0.dist-info" / "README.md"
    venv_doc.parent.mkdir(parents=True)
    venv_doc.write_text("[contributing](CONTRIBUTING.md)\n", encoding="utf-8")

    files = tracked_markdown_files(tmp_path)

    assert sorted(p.relative_to(tmp_path).as_posix() for p in files) == ["README.md", "docs/guide.md"]
    assert find_phantom_markdown_links(files, tmp_path) == []
    assert find_phantom_markdown_links([venv_doc], tmp_path), "the planted .venv link must be dead, or the test above proves nothing"


class TestAuditRegressions:
    def test_a_link_resolves_against_its_own_directory_not_the_repo_root(self, tmp_path):
        (tmp_path / "docs").mkdir()
        _write(tmp_path, "README.md", "root readme\n")
        guide = _write(tmp_path, "docs/guide.md", "[a](README.md)\n[b](../README.md)\n")
        assert find_phantom_markdown_links([guide], tmp_path) == ["docs/guide.md:1: dead markdown-link target 'README.md'"]

    def test_a_leading_slash_is_the_repo_root_not_the_drive_root(self, tmp_path):
        (tmp_path / "docs").mkdir()
        _write(tmp_path, "docs/x.md", "x\n")
        guide = _write(tmp_path, "docs/guide.md", "[a](/docs/x.md)\n[b](/missing.md)\n")
        assert find_phantom_markdown_links([guide], tmp_path) == ["docs/guide.md:2: dead markdown-link target '/missing.md'"]

    def test_fragments_queries_angle_brackets_titles_and_other_extensions(self, tmp_path):
        _write(tmp_path, "real.md", "x\n")
        _write(tmp_path, "logo.png", "x\n")
        (tmp_path / "sub dir").mkdir()
        readme = _write(
            tmp_path,
            "README.md",
            "[c](missing.md#sec) [d](real.md#sec) [e](real.md?x=1) [f](<sub dir>) [g](sub%20dir)\n"
            '![logo](logo.png "Logo") ![gone](gone.png) [h](#local-anchor) [i](notes.rst)\n'
            "[ref]: missing_ref.md\n[ok]: real.md\n[^1]: a footnote, not a link\n",
        )
        out = find_phantom_markdown_links([readme], tmp_path)
        assert out == [
            "README.md:1: dead markdown-link target 'missing.md#sec'",
            "README.md:2: dead markdown-link target 'gone.png'",
            "README.md:2: dead markdown-link target 'notes.rst'",
            "README.md:3: dead markdown-link target 'missing_ref.md'",
        ]

    def test_fenced_code_and_code_spans_are_not_links(self, tmp_path):
        readme = _write(
            tmp_path,
            "README.md",
            "```md\n[x](inside_fence.md)\n```\n~~~\n[y](tilde_fence.md)\n~~~\nuse `[z](span.md)` literally\n[real](after_fence.md)\n",
        )
        assert find_phantom_markdown_links([readme], tmp_path) == ["README.md:8: dead markdown-link target 'after_fence.md'"]

    def test_an_unreadable_file_is_reported(self, tmp_path):
        bad = tmp_path / "bad.md"
        bad.write_bytes(b"[x](y.md) \xff\xfe\n")
        (out,) = find_phantom_markdown_links([bad], tmp_path)
        assert "unreadable" in out
