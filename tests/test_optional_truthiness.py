"""Unit tests for the optional-truthiness check. Real files on disk, no mocking."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.optional_truthiness import BOUND_NAMES, assert_optionals_test_for_none, find_attribute_truthiness_tests, find_truthiness_tests


def _module(tmp_path: Path, body: str, name: str = "m.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class TestFindTruthinessTests:
    def test_an_optional_int_tested_for_truth_is_reported(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit and n >= limit:
                    return
        """,
        )

        findings = find_truthiness_tests(path)

        assert len(findings) == 1
        assert "`limit`" in findings[0]

    def test_is_not_none_is_accepted(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit is not None and n >= limit:
                    return
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_an_optional_collection_is_left_alone(self, tmp_path):
        """An empty list read as absent is usually intentional, and reporting it drowns the
        signal. A dict[str, int] must not be mistaken for an optional int either."""
        path = _module(
            tmp_path,
            """
            def read(tags: list | None = None, mapping: dict[str, int] | None = None):
                if tags:
                    pass
                if mapping:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_an_optional_string_is_left_alone(self, tmp_path):
        """Measured decision: including str produced 159 findings on one repo, nearly all
        of them an empty language code being read as absent, which is correct there."""
        path = _module(
            tmp_path,
            """
            def read(lang: str | None = None):
                if lang:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    def test_a_non_optional_number_is_left_alone(self, tmp_path):
        """Without None in the annotation there is no absent state to confuse 0 with."""
        path = _module(
            tmp_path,
            """
            def read(limit: int = 0):
                if limit:
                    pass
        """,
        )

        assert find_truthiness_tests(path) == []

    @pytest.mark.parametrize("annotation", ["Optional[int]", "Union[int, None]", "float | None"])
    def test_every_optional_spelling_is_recognised(self, tmp_path, annotation):
        path = _module(
            tmp_path,
            f"""
            def read(limit: {annotation} = None):
                if limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1

    def test_a_string_annotation_is_parsed_not_substring_matched(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: "int | None" = None):
                if limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1

    def test_it_reports_a_truthiness_test_inside_a_boolean_chain(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if ready and limit:
                    pass
        """,
        )

        assert len(find_truthiness_tests(path)) == 1


class TestAssertOptionalsTestForNone:
    def test_it_fails_on_a_new_finding(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )

        with pytest.raises(pytest.fail.Exception, match="tested for truth"):
            assert_optionals_test_for_none(files=[path], repo_root=tmp_path)

    def test_a_baselined_finding_is_accepted(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )
        known = find_truthiness_tests(path)

        assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)

    def test_it_fails_when_nothing_was_scanned(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_optionals_test_for_none(files=[], repo_root=tmp_path, min_subjects=1)


class TestAuditRegressions:
    def test_not_ifexp_while_assert_and_comprehension_filters_are_tests_for_truth(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None, items=()):
                if not limit:
                    pass
                size = 10 if limit else 0
                while limit:
                    break
                assert limit
                kept = [i for i in items if limit]
                if limit is None:
                    pass
        """,
        )

        lines = sorted(int(f.split(":")[1]) for f in find_truthiness_tests(path))

        assert lines == [3, 5, 6, 8, 9]

    def test_findings_are_repo_relative_and_duplicates_are_kept(self, tmp_path):
        body = """
            def read(limit: int | None = None):
                if limit:
                    pass
                if limit:
                    pass
        """
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = _module(tmp_path, body, "a/utils.py")
        b = _module(tmp_path, body, "b/utils.py")
        found_a = find_truthiness_tests(a, repo_root=tmp_path)
        assert [f.split(":")[0] for f in found_a] == ["a/utils.py", "a/utils.py"]
        with pytest.raises(pytest.fail.Exception, match=re.escape("b/utils.py")):
            assert_optionals_test_for_none(files=[a, b], repo_root=tmp_path, baseline=found_a)
        with pytest.raises(pytest.fail.Exception, match="1 optional"):
            assert_optionals_test_for_none(files=[a], repo_root=tmp_path, baseline=found_a[:1])
        assert_optionals_test_for_none(files=[a], repo_root=tmp_path, baseline=found_a)

    def test_a_baseline_entry_survives_a_line_shift_and_a_stale_one_fails(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def read(limit: int | None = None):
                if limit:
                    pass
        """,
        )
        known = find_truthiness_tests(path, repo_root=tmp_path)
        path.write_text("# a new comment\n# and another\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        assert find_truthiness_tests(path, repo_root=tmp_path) != known
        assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)
        path.write_text("def read(limit: int | None = None):\n    if limit is not None:\n        pass\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="no longer found"):
            assert_optionals_test_for_none(files=[path], repo_root=tmp_path, baseline=known)

    def test_a_nested_def_rebinding_the_name_is_judged_on_its_own_parameter(self, tmp_path):
        path = _module(
            tmp_path,
            """
            def outer(limit: int | None = None):
                def g(limit: list):
                    if limit:
                        pass
                def h():
                    if limit:
                        pass
                return g, h
        """,
        )

        assert [int(f.split(":")[1]) for f in find_truthiness_tests(path)] == [7]

    def test_annotated_is_unwrapped(self, tmp_path):
        path = _module(
            tmp_path,
            """
            from typing import Annotated, Optional
            def read(a: Annotated[int | None, "doc"] = None, b: Optional[Annotated[float, "x"]] = None, c: Annotated[list | None, 1] = None):
                if a or b or c:
                    pass
        """,
        )

        assert sorted(f.split("`")[1] for f in find_truthiness_tests(path)) == ["a", "b"]

    def test_bom_and_unparsable_files(self, tmp_path):
        bom = tmp_path / "bom.py"
        bom.write_bytes(b"\xef\xbb\xbfdef read(limit: int | None = None):\n    if limit:\n        pass\n")
        bad = _module(tmp_path, "def (:\n", "bad.py")
        assert len(find_truthiness_tests(bom)) == 1
        (problem,) = find_truthiness_tests(bad)
        assert "unparsable" in problem
        with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
            assert_optionals_test_for_none(files=[bad, bom], repo_root=tmp_path, baseline=find_truthiness_tests(bom, repo_root=tmp_path))


def test_a_truth_test_whose_zero_case_a_sibling_comparison_spells_out_is_not_reported(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        textwrap.dedent("""
            from typing import Optional

            def a(n: Optional[int] = None):
                if not n or n <= 0:
                    return 0
                if n and n > 0:
                    return n
                if n is None or n < 1:
                    return 1
                return 0 >= n or not n
            """),
        encoding="utf-8",
    )
    assert find_truthiness_tests(f) == []


def test_a_sibling_comparison_that_disagrees_at_zero_does_not_excuse_it(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        textwrap.dedent("""
            from typing import Optional

            def a(n: Optional[int] = None, m: Optional[int] = None):
                if not n or n > 5:
                    return 0
                if m and m >= 0:
                    return 1
                return n if n else 3
            """),
        encoding="utf-8",
    )
    assert [re.search(r":(\d+):", x).group(1) for x in find_truthiness_tests(f)] == ["5", "7", "9"]


# --- optionals carried on self and forwarded one hop (follow_attributes) ----------------------------------------------


def _tree(tmp_path: Path, files: "dict[str, str]") -> "list[Path]":
    out = []
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        out.append(p)
    return sorted(out)


def _attr(tmp_path: Path, files: "dict[str, str]", **kw) -> "list[tuple[str, str]]":
    """(path:line, expression) per attribute-pass finding."""
    found = find_attribute_truthiness_tests(_tree(tmp_path, files), repo_root=tmp_path, **kw)
    return [(m.group(1), m.group(2)) for m in (re.match(r"^(\S+:\d+): `([^`]+)`", f) for f in found) if m]


MONITOR = """
    from typing import Optional

    class Monitor:
        def __init__(self, time_budget_s: Optional[float] = None, total_iterations: Optional[int] = None):
            self.time_budget_s = time_budget_s
            self.total_iterations = total_iterations

        def check(self, elapsed):
            if self.time_budget_s and elapsed > self.time_budget_s:
                return "stop"
            if self.time_budget_s is not None and elapsed > self.time_budget_s:
                return "stop"
            return f"/{self.total_iterations}" if self.total_iterations else ""
"""


class TestAttributeTruthiness:
    def test_a_budget_stored_on_self_and_tested_for_truth_is_reported(self, tmp_path):
        """mlframe _cb_gpu_monitor.py:391; `total_iterations` is not a budget name and stays out (BOUND_NAMES)."""
        assert _attr(tmp_path, {"m.py": MONITOR}) == [("m.py:10", "self.time_budget_s")]

    def test_a_custom_name_pattern_widens_it(self, tmp_path):
        found = _attr(tmp_path, {"m.py": MONITOR}, bound_names=r"total|budget")
        assert found == [("m.py:10", "self.time_budget_s"), ("m.py:14", "self.total_iterations")]

    def test_getattr_in_a_mixin_and_a_closure_read_the_nearest_classes(self, tmp_path):
        """boruta_shap _fit_explain.py:535 and shap_proxied _shap_proxied_fit.py:184 (a nested function)."""
        files = {
            "fs/boruta/__init__.py": """
                from typing import Optional
                from ._fit import FitMixin

                class Boruta(FitMixin):
                    def __init__(self, max_runtime_mins: Optional[float] = None):
                        self.max_runtime_mins = max_runtime_mins
            """,
            "fs/boruta/_fit.py": """
                class FitMixin:
                    def fit(self):
                        budget = getattr(self, "max_runtime_mins", None)

                        def exhausted(t):
                            if budget and t > budget * 60:
                                return True
                            return False

                        return exhausted
            """,
            # Far away, a class whose max_runtime_mins is a plain float: it must not veto the nearer answer.
            "other/ctx.py": """
                from dataclasses import dataclass

                @dataclass
                class Ctx:
                    max_runtime_mins: float
            """,
        }
        assert _attr(tmp_path, files) == [("fs/boruta/_fit.py:7", "budget")]

    def test_a_class_that_defines_the_attribute_unannotated_is_judged_on_its_own(self, tmp_path):
        """mlframe models/selection.py: `max_train_size=None` unannotated, while another class has it Optional[int]."""
        files = {
            "a.py": """
                class Split:
                    def __init__(self, max_runtime_mins=None):
                        self.max_runtime_mins = max_runtime_mins

                    def run(self):
                        if self.max_runtime_mins:
                            return 1
            """,
            "b.py": """
                from typing import Optional

                class Other:
                    def __init__(self, max_runtime_mins: Optional[float] = None):
                        self.max_runtime_mins = max_runtime_mins
            """,
        }
        assert _attr(tmp_path, files) == []

    def test_disagreeing_classes_leave_a_mixin_unreported(self, tmp_path):
        files = {
            "pkg/a.py": """
                from typing import Optional

                class A:
                    def __init__(self, timeout: Optional[float] = None):
                        self.timeout = timeout
            """,
            "pkg/b.py": """
                class B:
                    timeout: float = 1.0
            """,
            "pkg/mixin.py": """
                class M:
                    def go(self):
                        if self.timeout:
                            return 1
            """,
        }
        assert _attr(tmp_path, files) == []

    def test_a_dataclass_field_and_a_module_level_self_function(self, tmp_path):
        """rfecv `_fit.py`: a module-level `def fit(self, ...)` binding `max_refits = self.max_refits`."""
        files = {
            "rfe/_configs.py": """
                from typing import Optional
                from dataclasses import dataclass

                @dataclass
                class SearchConfig:
                    max_refits: Optional[int] = None
            """,
            "rfe/_fit.py": """
                def fit(self, n):
                    max_refits = self.max_refits
                    total = min(n, max_refits) if max_refits else n
                    if not max_refits or n < 3:
                        return total
                    return 0
            """,
        }
        assert _attr(tmp_path, files) == [("rfe/_fit.py:4", "max_refits"), ("rfe/_fit.py:5", "max_refits")]

    def test_forwarding_one_hop_by_keyword_and_by_position(self, tmp_path):
        """rfecv `_fit_outer_loop.py:379` (keyword) and a positional call to a method."""
        files = {
            "rfe/est.py": """
                from typing import Optional

                class RFE:
                    def __init__(self, max_refits: Optional[int] = None):
                        self.max_refits = max_refits

                    def fit(self):
                        run_iteration(state=None, max_refits=self.max_refits)
                        self.helper(self.max_refits)
                        typed(max_refits=self.max_refits)
                        ambiguous(max_refits=self.max_refits)

                    def helper(self, cap):
                        if cap:
                            return 1
            """,
            "rfe/loop.py": """
                from typing import Optional

                def run_iteration(state, max_refits):
                    if max_refits and state.nsteps >= max_refits:
                        return "break"

                def typed(max_refits: Optional[int] = None):
                    if max_refits:  # an annotated optional: the per-function check reports it, not this pass
                        return 1

                def ambiguous(max_refits):
                    if max_refits:
                        return 1
            """,
            "rfe/other.py": """
                def ambiguous(max_refits):
                    return max_refits
            """,
        }
        assert _attr(tmp_path, files) == [("rfe/est.py:15", "cap"), ("rfe/loop.py:5", "max_refits")]

    def test_a_nested_function_rebinding_the_forwarded_name_is_not_followed(self, tmp_path):
        files = {"m.py": """
                from typing import Optional

                class C:
                    def __init__(self, timeout: Optional[float] = None):
                        self.timeout = timeout

                    def go(self):
                        wait(timeout=self.timeout)

                def wait(timeout):
                    def inner(timeout):
                        return 1 if timeout else 0

                    def closure():
                        return 1 if timeout else 0

                    return inner, closure
            """}
        assert _attr(tmp_path, files) == [("m.py:16", "timeout")]

    def test_a_sibling_comparison_agreeing_at_zero_excuses_a_carried_local(self, tmp_path):
        files = {"m.py": """
                from typing import Optional

                class C:
                    def __init__(self, max_retries: Optional[int] = None):
                        self.max_retries = max_retries

                    def go(self):
                        n = self.max_retries
                        if n and n > 0:
                            return n
                        return 0
            """}
        assert _attr(tmp_path, files) == []

    def test_staticmethods_have_no_instance_parameter(self, tmp_path):
        files = {"m.py": """
                from typing import Optional

                class C:
                    def __init__(self, timeout: Optional[float] = None):
                        self.timeout = timeout

                    @staticmethod
                    def go(other):
                        return 1 if other.timeout else 0
            """}
        assert _attr(tmp_path, files) == []

    def test_unparsable_files_are_left_to_the_per_file_check(self, tmp_path):
        files = _tree(tmp_path, {"m.py": MONITOR, "broken.py": "def (:\n"})
        assert len(find_attribute_truthiness_tests(files, repo_root=tmp_path)) == 1
        with pytest.raises(pytest.fail.Exception, match=r"broken\.py"):
            assert_optionals_test_for_none(files=files, repo_root=tmp_path)

    def test_the_assert_follows_attributes_by_default_and_can_be_told_not_to(self, tmp_path):
        files = _tree(tmp_path, {"m.py": MONITOR})
        with pytest.raises(pytest.fail.Exception, match=r"self\.time_budget_s"):
            assert_optionals_test_for_none(files=files, repo_root=tmp_path)
        assert_optionals_test_for_none(files=files, repo_root=tmp_path, follow_attributes=False)
        accepted = find_attribute_truthiness_tests(files, repo_root=tmp_path)
        assert_optionals_test_for_none(files=files, repo_root=tmp_path, baseline=accepted)


@pytest.mark.parametrize(
    ("name", "bound"),
    [
        ("max_runtime_mins", True),
        ("max_refits", True),
        ("time_budget_s", True),
        ("timeout", True),
        ("request_timeout_s", True),
        ("max_tokens", True),
        ("min_iters", True),
        ("max_nfeatures", False),
        ("max_train_size", False),
        ("max_categorical_cardinality", False),
        ("total_iterations", False),
        ("reporting_interval_mins", False),
    ],
)
def test_bound_names(name, bound):
    assert bool(BOUND_NAMES.search(name)) is bound
