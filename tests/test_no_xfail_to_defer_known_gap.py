"""no_xfail_to_defer reads calls to ``known_gap(reason, gap_closed)`` as xfail sites.

The helper takes its reason as an argument, so before this the xfail rules could not see it: every parked bug could move
behind ``known_gap("...", gap_closed=False)`` and the gate stayed green.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.no_xfail_to_defer import (
    KNOWN_GAP_MODULES,
    RULE_GAP_NEVER_CLOSES,
    RULE_NO_REASON,
    RULE_UNTRACKED,
    assert_no_xfail_to_defer,
    find_xfail_to_defer,
)

SHIPPED = "from py_ci_shared.pytest_known_gap import known_gap\n"


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _found(tmp_path: Path, body: str, **kw: object) -> list[tuple[str, int]]:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_x.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_xfail_to_defer(tests, use_git=False, **kw)  # type: ignore[arg-type]
    return [(f.rule, f.line) for f in findings]


class TestTheReasonIsJudgedLikeAnXfail:
    def test_a_parked_bug_behind_the_helper_is_untracked(self, tmp_path):
        body = SHIPPED + 'def test_a():\n    known_gap("PROD GAP: selector keeps the redundant column", gap_closed=measured())\n'
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 3)]

    @pytest.mark.parametrize(
        "reason", ["numpy 2.1 argsort is unstable on ties", "see #412", "tracked as MLF-88", "https://github.com/numba/numba/issues/1", "upstream issue"]
    )
    def test_an_external_component_or_a_tracked_issue_is_clean(self, tmp_path, reason):
        body = SHIPPED + f'def test_a():\n    known_gap("{reason}", gap_closed=measured())\n'
        assert _found(tmp_path, body) == []

    def test_no_reason_at_all(self, tmp_path):
        body = SHIPPED + 'def test_a():\n    known_gap("", gap_closed=measured())\n'
        assert _found(tmp_path, body) == [(RULE_NO_REASON, 3)]

    def test_the_reason_keyword_and_an_f_string_are_read(self, tmp_path):
        body = (
            SHIPPED
            + 'def test_a(n):\n    known_gap(reason=f"our cache is wrong at n={n}", gap_closed=measured())\n    known_gap(f"polars drops the dtype at n={n}", measured())\n'
        )
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 3)]

    def test_a_reason_held_in_a_variable_is_not_judged_as_for_xfail(self, tmp_path):
        body = SHIPPED + "def test_a():\n    known_gap(GAP, gap_closed=measured())\n"
        assert _found(tmp_path, body) == []

    def test_the_strictness_rule_does_not_apply_the_helper_is_strict_by_construction(self, tmp_path):
        body = SHIPPED + 'def test_a():\n    known_gap("numpy bug", gap_closed=measured())\n'
        assert _found(tmp_path, body, xfail_strict=False) == []


class TestAGapThatCanNeverClose:
    @pytest.mark.parametrize("verdict", ["False", "0", "None"])
    def test_a_falsy_literal_keyword_is_a_plain_xfail(self, tmp_path, verdict):
        body = SHIPPED + f'def test_a():\n    known_gap("numpy 2.1 bug", gap_closed={verdict})\n'
        assert _found(tmp_path, body) == [(RULE_GAP_NEVER_CLOSES, 3)]

    def test_a_falsy_literal_second_positional_argument(self, tmp_path):
        body = SHIPPED + 'def test_a():\n    known_gap("numpy 2.1 bug", False)\n'
        assert _found(tmp_path, body) == [(RULE_GAP_NEVER_CLOSES, 3)]

    @pytest.mark.parametrize("verdict", ["measured()", "kept == 1", "True", "ok"])
    def test_a_verdict_that_can_change_is_clean(self, tmp_path, verdict):
        body = SHIPPED + f'def test_a(kept, ok):\n    known_gap("numpy 2.1 bug", gap_closed={verdict})\n'
        assert _found(tmp_path, body) == []

    @pytest.mark.parametrize(
        "guarded",
        [
            '    if not wired():\n        known_gap("numpy 2.1 bug", gap_closed=False)\n',
            '    try:\n        run()\n    except AttributeError:\n        known_gap("numpy 2.1 bug", gap_closed=False)\n',
            '    if x:\n        pass\n    else:\n        known_gap("numpy 2.1 bug", gap_closed=False)\n',
        ],
    )
    def test_a_literal_verdict_under_a_branch_is_the_measurement_itself(self, tmp_path, guarded):
        """``if not wired: known_gap(..., gap_closed=False)``: a closed gap never reaches the call, so the verdict cannot be wrong."""
        assert _found(tmp_path, SHIPPED + "def test_a(x):\n" + guarded) == []

    def test_a_guard_in_another_function_does_not_excuse_an_unconditional_call(self, tmp_path):
        body = SHIPPED + 'def helper():\n    if x:\n        pass\ndef test_a():\n    known_gap("numpy 2.1 bug", gap_closed=False)\n'
        assert _found(tmp_path, body) == [(RULE_GAP_NEVER_CLOSES, 6)]

    def test_a_branch_does_not_excuse_an_untracked_reason(self, tmp_path):
        body = SHIPPED + 'def test_a(x):\n    if x:\n        known_gap("our bug", gap_closed=False)\n'
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 4)]

    def test_an_untracked_reason_and_a_never_closing_gap_are_both_reported(self, tmp_path):
        body = SHIPPED + 'def test_a():\n    known_gap("our bug", gap_closed=False)\n'
        assert sorted(_found(tmp_path, body)) == sorted([(RULE_UNTRACKED, 3), (RULE_GAP_NEVER_CLOSES, 3)])


class TestHowTheHelperIsResolved:
    @pytest.mark.parametrize(
        "imports",
        [
            "from py_ci_shared.pytest_known_gap import known_gap\n",
            "from py_ci_shared.pytest_known_gap import known_gap as gap\n",
            "import py_ci_shared.pytest_known_gap as pk\n",
            "from py_ci_shared import pytest_known_gap as pk\n",
            "from tests._known_gap import known_gap\n",
            "from tests import _known_gap as pk\n",
            "from ._known_gap import known_gap\n",
            "from . import _known_gap as pk\n",
            "from _known_gap import known_gap\n",
        ],
    )
    def test_every_import_spelling_of_the_shipped_or_local_helper(self, tmp_path, imports):
        call = "gap" if " as gap" in imports else "pk.known_gap" if " as pk" in imports else "known_gap"
        body = imports + f'def test_a():\n    {call}("our bug", gap_closed=measured())\n'
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 3)], imports

    def test_a_local_module_under_another_name_is_configurable(self, tmp_path):
        body = 'from helpers.gaps import known_gap\ndef test_a():\n    known_gap("our bug", gap_closed=measured())\n'
        assert _found(tmp_path, body) == []
        assert _found(tmp_path, body, known_gap_modules=["helpers.gaps"]) == [(RULE_UNTRACKED, 3)]

    def test_the_default_local_module_is_tests_known_gap(self):
        assert KNOWN_GAP_MODULES == ("tests._known_gap",)

    def test_a_function_that_merely_shares_the_name_is_not_the_helper(self, tmp_path):
        body = 'from somewhere_else import known_gap\ndef test_a():\n    known_gap("our bug", gap_closed=False)\n'
        assert _found(tmp_path, body) == []

    def test_a_different_function_in_the_helper_module_is_not_judged(self, tmp_path):
        body = 'from py_ci_shared.pytest_known_gap import __doc__ as d\ndef test_a():\n    print("our bug")\n'
        assert _found(tmp_path, body) == []

    def test_a_bare_name_with_no_import_is_not_the_helper(self, tmp_path):
        assert _found(tmp_path, 'def known_gap(r, gap_closed):\n    pass\ndef test_a():\n    known_gap("our bug", gap_closed=False)\n') == []


class TestExistingBehavioursStayPut:
    def test_an_xfail_marker_in_the_same_file_is_still_judged(self, tmp_path):
        body = (
            SHIPPED
            + 'import pytest\n@pytest.mark.xfail(reason="our bug", strict=True)\ndef test_a():\n    pass\ndef test_b():\n    known_gap("numpy bug", gap_closed=measured())\n'
        )
        assert _found(tmp_path, body) == [(RULE_UNTRACKED, 3)]


class TestCorpusContract:
    def test_bom_unparsable_and_empty(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_bytes(b"\xef\xbb\xbf" + (SHIPPED + 'def test_a():\n    known_gap("our bug", gap_closed=measured())\n').encode())
        assert [f.rule for f in find_xfail_to_defer(tests, use_git=False)[0]] == [RULE_UNTRACKED]
        (tests / "test_bad.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"test_bad\.py"):
            assert_no_xfail_to_defer(tests, use_git=False)
        (tmp_path / "empty").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_xfail_to_defer(tmp_path / "empty", use_git=False)

    def test_the_assert_passes_known_gap_modules_through_and_the_ratchet_counts_the_rule(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text('from helpers.gaps import known_gap\ndef test_a():\n    known_gap("numpy bug", gap_closed=False)\n', encoding="utf-8")
        assert_no_xfail_to_defer(tests, use_git=False)
        with pytest.raises(pytest.fail.Exception, match="known-gap-never-closes"):
            assert_no_xfail_to_defer(tests, known_gap_modules=["helpers.gaps"], use_git=False)
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_xfail_to_defer(tests, baseline_path=bl, known_gap_modules=["helpers.gaps"], refresh=True, use_git=False)
        assert sum(e["count"] for e in json.loads(bl.read_text(encoding="utf-8"))["entries"].values()) == 1
        assert_no_xfail_to_defer(tests, baseline_path=bl, known_gap_modules=["helpers.gaps"], use_git=False)
