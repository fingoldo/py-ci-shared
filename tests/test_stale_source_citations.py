"""Tests for stale_source_citations: each rule, each resolution guard, and the self-verifying controls."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.stale_source_citations import (
    RULE_PAST_END,
    RULE_SELF,
    RULE_SYMBOL,
    assert_no_stale_source_citations,
    find_stale_source_citations,
)


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _found(root: Path, **kw: object) -> list[tuple[str, str, int]]:
    findings, _ = find_stale_source_citations(root, use_git=False, **kw)  # type: ignore[arg-type]
    return [(f.rule, f.path, f.line) for f in findings]


_TARGET = "\n".join(["# pad"] * 9 + ["def fit(x):", "    return x"]) + "\n"  # fit is on line 10 of 11


class TestSelfCitation:
    def test_a_log_message_citing_another_line_of_its_own_file(self, tmp_path):
        root = _tree(tmp_path, {"pkg/mah.py": "import logging\nlog = logging.getLogger()\n\ndef f(e):\n    log.debug('suppressed in mah.py:2: %s', e)\n"})
        assert _found(root) == [(RULE_SELF, "pkg/mah.py", 5)]

    def test_a_correct_self_citation_and_a_multi_line_call_citing_its_first_line_are_clean(self, tmp_path):
        body = (
            "import logging\nlog = logging.getLogger()\n\ndef f(e):\n    log.debug('at mah.py:5: %s', e)\n"
            "    log.debug(\n        'at mah.py:6: %s',\n        e,\n    )\n"
        )
        assert _found(_tree(tmp_path, {"pkg/mah.py": body})) == []

    def test_a_docstring_or_comment_citing_its_own_file_inside_the_file_is_not_a_self_citation(self, tmp_path):
        body = '"""See mah.py:4 for the loop."""\n# mirrors mah.py:4\ndef f():\n    return 1\n'
        assert _found(_tree(tmp_path, {"pkg/mah.py": body})) == []


class TestPastEnd:
    def test_a_citation_past_the_end_of_the_cited_file(self, tmp_path):
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "pkg/b.py": "# the loop in a.py:400 does this\nX = 1\n"})
        assert _found(root) == [(RULE_PAST_END, "pkg/b.py", 1)]

    def test_its_own_file_past_the_end_even_in_a_docstring(self, tmp_path):
        root = _tree(tmp_path, {"pkg/p.py": '"""Extracted from the p.py:1372 mega-try body."""\nX = 1\n'})
        assert _found(root) == [(RULE_PAST_END, "pkg/p.py", 1)]

    def test_within_the_file_ambiguous_names_and_other_packages_are_not_judged(self, tmp_path):
        root = _tree(
            tmp_path,
            {
                "pkg/a.py": _TARGET,
                "pkg/b.py": "# a.py:10 fits\n# core.py:2992 raises in xgboost\n# utils.py:999 twice\nX = 1\n",
                "other/core.py": "X = 1\n",
                "pkg/x/utils.py": "X = 1\n",
                "pkg/y/utils.py": "X = 1\n",
            },
        )
        assert _found(root) == []

    def test_a_path_with_directories_resolves_across_packages(self, tmp_path):
        root = _tree(tmp_path, {"other/core.py": "X = 1\n", "pkg/b.py": "# other/core.py:50\nX = 1\n"})
        assert _found(root) == [(RULE_PAST_END, "pkg/b.py", 1)]


class TestSymbolAtLine:
    @pytest.mark.parametrize("citation", ["`fit` (a.py:10)", "a.py:10 `fit`", "fit() at a.py:11", "a.py:9 (fit)", "`a.fit` in a.py:11"])
    def test_a_symbol_at_or_near_its_cited_line_is_clean(self, tmp_path, citation):
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "pkg/b.py": f"# see {citation}\nX = 1\n"})
        assert _found(root) == []

    @pytest.mark.parametrize("citation", ["`fit` (a.py:2)", "a.py:3 `fit`", "predict() at a.py:10"])
    def test_a_symbol_away_from_its_cited_line_is_reported(self, tmp_path, citation):
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "pkg/b.py": f"# see {citation}\nX = 1\n"})
        assert _found(root) == [(RULE_SYMBOL, "pkg/b.py", 1)]

    def test_rules_can_be_selected(self, tmp_path):
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "pkg/b.py": "# see `fit` (a.py:2) and a.py:400\nX = 1\n"})
        assert [r for r, _, _ in _found(root)] == sorted([RULE_PAST_END, RULE_SYMBOL])
        assert [r for r, _, _ in _found(root, rules=[RULE_SYMBOL])] == [RULE_SYMBOL]


class TestExtraGlobs:
    def test_a_markdown_citation_past_the_end_of_a_py_source_is_reported(self, tmp_path):
        """A ``.md`` doc citing a stale line of a ``.py`` source in the same corpus is caught, opt-in."""
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "docs/ARCHITECTURE.md": "See `pkg/a.py:400` for the routing table.\n"})
        assert _found(root) == []  # not scanned by default
        assert _found(root, extra_globs=["*.md"]) == [(RULE_PAST_END, "docs/ARCHITECTURE.md", 1)]

    def test_a_markdown_citation_that_still_resolves_stays_clean(self, tmp_path):
        """The nearest correct code: the same doc citing a line the target file still has."""
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "docs/ARCHITECTURE.md": "See `pkg/a.py:1` for the header.\n"})
        assert _found(root, extra_globs=["*.md"]) == []

    def test_a_deleted_file_citation_in_markdown_is_not_this_gates_job(self, tmp_path):
        """A citation of a file that no longer exists at all is pyutilz's ``stale_source_citation`` territory."""
        root = _tree(tmp_path, {"pkg/a.py": _TARGET, "docs/ARCHITECTURE.md": "See `src/pkg/_old_router.py` for the routing table.\n"})
        assert _found(root, extra_globs=["*.md"]) == []


class TestCorpusAndBaseline:
    def test_bom_unparsable_empty_and_the_ratchet(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "a.py").write_bytes(b"\xef\xbb\xbf# a.py:99\nX = 1\n")
        assert _found(root) == [(RULE_PAST_END, "a.py", 1)]
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_stale_source_citations(root, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_no_stale_source_citations(root, baseline_path=bl, refresh=True, use_git=False)
        assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
        assert_no_stale_source_citations(root, baseline_path=bl, use_git=False)
        (root / "b.py").write_text("# a.py:77\nX = 1\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="line-past-end"):
            assert_no_stale_source_citations(root, baseline_path=bl, use_git=False)
        (root / "bad.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
            assert_no_stale_source_citations(root, use_git=False)
        (tmp_path / "e").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_stale_source_citations(tmp_path / "e", use_git=False)
