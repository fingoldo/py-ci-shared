"""Tests for no_xfail_to_defer: every marker spelling, each rule, the repository's xfail_strict, and the exemptions."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.no_xfail_to_defer import (
    RULE_NO_REASON,
    RULE_NOT_STRICT,
    RULE_UNTRACKED,
    assert_no_xfail_to_defer,
    find_xfail_to_defer,
    read_xfail_strict,
)


def _found(tmp_path: Path, body: str, **kw: object) -> list[tuple[str, int]]:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_x.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_xfail_to_defer(tests, use_git=False, **kw)  # type: ignore[arg-type]
    return [(f.rule, f.line) for f in findings]


class TestXfailMarkers:
    def test_a_parked_bug_is_not_strict_and_untracked(self, tmp_path):
        body = 'import pytest\n@pytest.mark.xfail(reason="PROD GAP: selector keeps the redundant column")\ndef test_a():\n    pass\n'
        assert sorted(_found(tmp_path, body)) == sorted([(RULE_NOT_STRICT, 2), (RULE_UNTRACKED, 2)])

    def test_strict_and_an_external_reason_is_clean(self, tmp_path):
        body = 'import pytest\n@pytest.mark.xfail(reason="numba 0.60 miscompiles prange reductions on Windows", strict=True)\ndef test_a():\n    pass\n'
        assert _found(tmp_path, body) == []

    @pytest.mark.parametrize("reason", ["see #412", "tracked as MLF-88", "https://github.com/numba/numba/issues/1", "upstream issue"])
    def test_a_tracked_issue_counts(self, tmp_path, reason):
        body = f'import pytest\n@pytest.mark.xfail(reason="{reason}", strict=True)\ndef test_a():\n    pass\n'
        assert _found(tmp_path, body) == []

    def test_an_external_limit_may_be_non_strict_because_it_can_pass_by_chance(self, tmp_path):
        body = 'import pytest\n@pytest.mark.xfail(reason="known LLM behaviour under prompt injection", strict=False)\ndef test_a():\n    pass\n'
        assert _found(tmp_path, body) == []

    def test_strict_false_counts_even_with_the_repo_default(self, tmp_path):
        body = 'import pytest\n@pytest.mark.xfail(reason="PROD GAP", strict=False)\ndef test_a():\n    pass\n'
        assert (RULE_NOT_STRICT, 2) in _found(tmp_path, body, xfail_strict=True)

    def test_the_repository_default_is_read_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\nxfail_strict = true\n", encoding="utf-8")
        body = 'import pytest\n@pytest.mark.xfail(reason="numba bug")\ndef test_a():\n    pass\n'
        assert read_xfail_strict(tmp_path) is True
        assert _found(tmp_path, body.replace("numba bug", "our bug")) == [(RULE_UNTRACKED, 2)]

    @pytest.mark.parametrize(
        ("name", "text"),
        [("pytest.ini", "[pytest]\nxfail_strict = true\n"), ("setup.cfg", "[tool:pytest]\nxfail_strict = 1\n"), ("tox.ini", "[pytest]\nxfail_strict=yes\n")],
    )
    def test_ini_files_are_read(self, tmp_path, name, text):
        assert read_xfail_strict(tmp_path) is False
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert read_xfail_strict(tmp_path) is True

    def test_no_reason_pytestmark_param_marks_and_an_alias(self, tmp_path):
        body = """
            import pytest
            from pytest import mark as m
            pytestmark = pytest.mark.xfail
            @pytest.mark.parametrize("v", [1, pytest.param(2, marks=pytest.mark.xfail(strict=True))])
            def test_a(v):
                pass
            @m.xfail(reason="our own regression", strict=True)
            def test_b():
                pass
        """
        assert sorted(_found(tmp_path, body)) == sorted([(RULE_NO_REASON, 4), (RULE_NO_REASON, 5), (RULE_NOT_STRICT, 4), (RULE_UNTRACKED, 8)])

    def test_computed_reasons_are_not_judged_but_their_strictness_is(self, tmp_path):
        body = """
            import pytest
            REASON = "our regression"
            @pytest.mark.xfail(reason=REASON)
            def test_a():
                pass
            @pytest.mark.xfail(reason=REASON + " | measured", strict=True)
            def test_b():
                pass
            @pytest.mark.xfail(reason=f"FS GAP: {1} kept", strict=True)
            def test_c():
                pass
        """
        assert sorted(_found(tmp_path, body)) == sorted([(RULE_NOT_STRICT, 4), (RULE_UNTRACKED, 10)])


class TestImperativeAndSkip:
    def test_imperative_xfail_is_judged_on_its_reason(self, tmp_path):
        body = 'import pytest\ndef test_a():\n    pytest.xfail("our cache is wrong")\ndef test_b():\n    pytest.xfail("polars 1.2 drops the dtype")\n'
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 3)]

    def test_skip_needs_a_reason_unless_conditional_and_skipif_is_not_judged(self, tmp_path):
        body = """
            import sys, pytest
            @pytest.mark.skip
            def test_a():
                pass
            @pytest.mark.skip(reason="needs a GPU")
            def test_b():
                pass
            @pytest.mark.skipif(sys.platform == "win32", reason="")
            def test_c():
                pass
            def test_d():
                pytest.skip()
            def test_e():
                if sys.platform == "win32":
                    pytest.skip()
            def test_f():
                try:
                    import torch
                except ImportError:
                    pytest.skip()
        """
        assert _found(tmp_path, body) == [(RULE_NO_REASON, 3), (RULE_NO_REASON, 13)]


class TestCorpusAndBaseline:
    def test_bom_unparsable_and_empty(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_bytes(b'\xef\xbb\xbfimport pytest\n@pytest.mark.xfail(reason="ours", strict=True)\ndef test_a():\n    pass\n')
        assert [f.rule for f in find_xfail_to_defer(tests, use_git=False)[0]] == [RULE_UNTRACKED]
        (tests / "test_bad.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"test_bad\.py"):
            assert_no_xfail_to_defer(tests, use_git=False)
        (tmp_path / "empty").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_xfail_to_defer(tmp_path / "empty", use_git=False)

    def test_the_count_ratchet(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text('import pytest\n@pytest.mark.xfail(reason="ours", strict=True)\ndef test_a():\n    pass\n', encoding="utf-8")
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_xfail_to_defer(tests, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_no_xfail_to_defer(tests, baseline_path=bl, refresh=True, use_git=False)
        assert sum(e["count"] for e in json.loads(bl.read_text(encoding="utf-8"))["entries"].values()) == 1
        assert_no_xfail_to_defer(tests, baseline_path=bl, use_git=False)
        (tests / "test_b.py").write_text('import pytest\n@pytest.mark.xfail(reason="also ours", strict=True)\ndef test_b():\n    pass\n', encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="untracked-reason"):
            assert_no_xfail_to_defer(tests, baseline_path=bl, use_git=False)
