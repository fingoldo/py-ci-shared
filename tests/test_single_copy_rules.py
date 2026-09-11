"""Tests for the four single-copy rules moved into py_ci_shared: fail messages, SQL verifier coverage, doctests, field bounds."""

from __future__ import annotations

import sys
import re
import textwrap
from pathlib import Path
from typing import Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.fail_message_quality import ACTIONABLE_RE, assert_fail_messages_actionable, fail_message_problems
from py_ci_shared.package_doctests import assert_package_doctests_pass, run_package_doctests
from py_ci_shared.pydantic_field_bounds import assert_field_bounds_enforced, find_unenforced_literals, find_unenforced_numeric_bounds
from py_ci_shared.sql_verifier_coverage import assert_verifier_covers_statements, sql_constants, verifier_lists


class TestFailMessages:
    @pytest.mark.parametrize("text", ["baseline mismatch: 3 entries differ", "pkg/mod.py has 3 problems", "Addressee mismatch"])
    def test_what_is_not_actionable(self, text):
        assert not ACTIONABLE_RE.search(text)

    @pytest.mark.parametrize("text", ["refresh with --refresh-x", "expected <name> in <file>", "Remove the stale entry"])
    def test_what_is(self, text):
        assert ACTIONABLE_RE.search(text)

    def test_the_scan_judges_static_text_and_counts_dynamic(self, tmp_path):
        f = tmp_path / "test_x.py"
        f.write_text('import pytest\n\ndef test_a():\n    pytest.fail("bad: thing")\n    pytest.fail(f"{x} dynamic")\n    pytest.fail("Fix the thing")\n', encoding="utf-8")
        audited, bad = fail_message_problems([f])
        assert audited == 3 and len(bad) == 1 and "bad: thing" in bad[0]

    def test_an_empty_directory_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="collector"):
            assert_fail_messages_actionable(tmp_path)


class TestSqlVerifierCoverage:
    def _tree(self, tmp_path, listed='("q", ("A",))', excluded='{"q.B": "an ad-hoc diagnostic nobody runs in production, kept for reference"}'):
        (tmp_path / "q.py").write_text('A = "SELECT 1"\nB = f"UPDATE t SET x = 1"\nC = "not sql"\n', encoding="utf-8")
        (tmp_path / "scripts").mkdir(exist_ok=True)
        verifier = tmp_path / "scripts" / "verify.py"
        verifier.write_text(f"STATEMENTS = [{listed}]\nEXCLUDED = {excluded}\n", encoding="utf-8")
        return verifier

    def test_the_constants_and_the_lists_are_read(self, tmp_path):
        verifier = self._tree(tmp_path)
        assert sql_constants(tmp_path, exclude_top_dirs={"scripts"}) == {"q.A", "q.B"}
        listed, excluded = verifier_lists(verifier)
        assert listed == {"q.A"} and set(excluded) == {"q.B"}

    def test_full_coverage_passes(self, tmp_path):
        assert_verifier_covers_statements(tmp_path, self._tree(tmp_path), exclude_top_dirs={"scripts"})

    def test_a_missing_statement_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match=re.escape("q.B")):
            assert_verifier_covers_statements(tmp_path, self._tree(tmp_path, excluded="{}"), exclude_top_dirs={"scripts"})

    def test_a_stale_entry_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match=re.escape("q.GONE")):
            assert_verifier_covers_statements(tmp_path, self._tree(tmp_path, listed='("q", ("A", "GONE"))'), exclude_top_dirs={"scripts"})

    def test_a_thin_reason_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="real reason"):
            assert_verifier_covers_statements(tmp_path, self._tree(tmp_path, excluded='{"q.B": "unused"}'), exclude_top_dirs={"scripts"})


class TestPackageDoctests:
    def _package(self, tmp_path, monkeypatch, body):
        pkg = tmp_path / "dtpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "mod.py").write_text(textwrap.dedent(body), encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        for name in [n for n in sys.modules if n == "dtpkg" or n.startswith("dtpkg.")]:
            monkeypatch.delitem(sys.modules, name)
        return "dtpkg"

    def test_a_passing_doctest_counts(self, tmp_path, monkeypatch):
        name = self._package(tmp_path, monkeypatch, '''
            def f():
                """
                >>> f()
                'hi x'
                """
                return "hi x"
            ''')
        attempted, failures, _ = run_package_doctests(name)
        assert attempted == 1 and failures == []

    def test_a_wrong_doctest_fails(self, tmp_path, monkeypatch):
        name = self._package(tmp_path, monkeypatch, '''
            def f():
                """
                >>> f()
                'hix'
                """
                return "hi x"
            ''')
        with pytest.raises(pytest.fail.Exception, match="failed"):
            assert_package_doctests_pass(name)

    def test_no_examples_fails(self, tmp_path, monkeypatch):
        name = self._package(tmp_path, monkeypatch, "def f():\n    return 1\n")
        with pytest.raises(pytest.fail.Exception, match="an empty run"):
            assert_package_doctests_pass(name)


pydantic = pytest.importorskip("pydantic")


class _Good(pydantic.BaseModel):
    n: int = pydantic.Field(1, ge=0)
    mode: Literal["a", "b"] = "a"


class _Bad(pydantic.BaseModel):
    n: int = pydantic.Field(1, json_schema_extra={"minimum": 0})
    mode: str = "a"

    @pydantic.field_validator("mode", mode="before")
    @classmethod
    def _lower(cls, v):
        return str(v).lower()


class _BadLiteral(pydantic.BaseModel):
    mode: Literal["a", "b"] = "a"

    @pydantic.field_validator("mode", mode="wrap")
    @classmethod
    def _swallow(cls, v, handler):
        try:
            return handler(v)
        except Exception:
            return v


class TestFieldBounds:
    def test_an_enforced_model_passes(self):
        assert find_unenforced_numeric_bounds([_Good]) == (1, [])
        assert find_unenforced_literals([_Good]) == (1, [])
        assert_field_bounds_enforced([_Good], require_literal=True)

    def test_a_literal_that_swallows_its_error_is_caught(self):
        audited, bad = find_unenforced_literals([_BadLiteral])
        assert audited == 1 and bad and "_BadLiteral.mode" in bad[0]

    def test_a_model_list_with_nothing_bounded_fails_the_floor(self):
        with pytest.raises(pytest.fail.Exception, match="nothing to look at"):
            assert_field_bounds_enforced([_Bad])
