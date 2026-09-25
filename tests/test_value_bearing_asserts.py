"""`value_bearing_asserts` separates narrowing from value checks, and its ratchet only shrinks."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from py_ci_shared.value_bearing_asserts import assert_no_value_bearing_asserts, find_value_bearing_asserts, is_narrowing_assert


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _test(src: str) -> ast.expr:
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.Assert)
    return node.test


class TestTheClassifier:
    @pytest.mark.parametrize(
        "src", ["assert a is not None", "assert a is None", "assert x", "assert self.conn", "assert rows[0]", "assert a is not None and b is not None"]
    )
    def test_narrowing_forms(self, src):
        assert is_narrowing_assert(_test(src))

    @pytest.mark.parametrize(
        "src", ["assert a + b < 1.0", "assert n >= 1", "assert x in ('a', 'b')", "assert a == b", "assert f(x)", "assert a is not None and n > 0"]
    )
    def test_value_checks(self, src):
        assert not is_narrowing_assert(_test(src))

    def test_isinstance_is_a_value_check_unless_allowed(self):
        test = _test("assert isinstance(x, int)")
        assert not is_narrowing_assert(test)
        assert is_narrowing_assert(test, allow_isinstance=True)

    def test_allowing_isinstance_does_not_allow_what_rides_with_it(self):
        assert not is_narrowing_assert(_test("assert isinstance(p, tuple) and len(p) in (2, 3)"), allow_isinstance=True)


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    (root / "tests").mkdir(parents=True)
    (root / "a.py").write_text("def f(x):\n    assert x is not None\n    assert x > 0\n    return x\n", encoding="utf-8")
    (root / "tests" / "test_a.py").write_text("def test_f():\n    assert f(1) == 1\n", encoding="utf-8")
    return root


class TestTheScan:
    def test_production_only_and_counted(self, tmp_path):
        offenders, seen = find_value_bearing_asserts(_package(tmp_path))
        assert offenders == ["a.py:3  x > 0"]
        assert seen == 2, "the tests/ assert must not be counted"


class TestTheRatchet:
    def test_a_value_assert_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match=re.escape("a.py:3")):
            assert_no_value_bearing_asserts(_package(tmp_path))

    def test_a_baseline_accepts_debt_and_a_stale_entry_fails(self, tmp_path):
        root = _package(tmp_path)
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps(["a.py::x > 0"]), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)
        baseline.write_text(json.dumps(["a.py::x > 0", "gone.py::y < 1"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("gone.py")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_a_missing_baseline_fails_and_is_written_only_on_refresh(self, tmp_path, monkeypatch):
        """Re-framed 2026-09-24 (TZ-17): a missing baseline used to be written and SKIP, so a wrong path or a
        deleted file was a permanent pass. Now it fails naming the refresh command, and only a refresh writes it."""
        monkeypatch.delenv("PY_CI_SHARED_REFRESH", raising=False)
        root = _package(tmp_path)
        baseline = tmp_path / "b.json"
        with pytest.raises(pytest.fail.Exception, match=re.escape("--refresh-value-asserts-baseline")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        assert not baseline.exists()
        with pytest.raises(pytest.skip.Exception):
            assert_no_value_bearing_asserts(root, baseline_path=baseline, refresh=True)
        data = json.loads(baseline.read_text(encoding="utf-8"))
        assert data["entries"] == {"a.py::x > 0": {"count": 1, "note": ""}}
        assert_no_value_bearing_asserts(root, baseline_path=baseline)  # the written file now passes

    def test_refresh_via_env_var_as_under_xdist(self, tmp_path, monkeypatch):
        root = _package(tmp_path)
        baseline = tmp_path / "b.json"
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", "value-asserts")
        with pytest.raises(pytest.skip.Exception):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        assert baseline.exists()
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", "some-other-gate")
        baseline.unlink()
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)


class TestAuditRegressions:
    def test_a_bom_file_is_scanned(self, tmp_path):
        """TZ-4: a BOM file returned `([], 0)`."""
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "a.py").write_bytes(b"\xef\xbb\xbfdef f(x):\r\n    assert x > 0\r\n")
        assert find_value_bearing_asserts(root) == (["a.py:2  x > 0"], 1)
        (root / "a.py").write_bytes(b"\xef\xbb\xbfdef f(x):\r\n    assert x is not None\r\n")
        assert find_value_bearing_asserts(root) == ([], 1)

    def test_an_unparsable_file_is_listed_and_fails(self, tmp_path):
        root = _package(tmp_path)
        (root / "b.py").write_text("def (:\n", encoding="utf-8")
        offenders, _ = find_value_bearing_asserts(root)
        assert offenders == ["a.py:3  x > 0", "b.py:1  <unparsable: invalid syntax>"]
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps(["a.py::x > 0"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("b.py:1: unparsable")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        (root / "b.py").write_text("y = 1\n", encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_duplicate_asserts_are_counted_not_collapsed(self, tmp_path):
        """TZ-15: two identical `assert n > 0` shared one key, so baselining one accepted both."""
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "a.py").write_text("def f(n):\n    assert n > 0\n    assert n > 0\n", encoding="utf-8")
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps(["a.py::n > 0"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("a.py:3  n > 0")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        baseline.write_text(json.dumps(["a.py::n > 0", "a.py::n > 0"]), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_long_expressions_are_keyed_in_full_and_legacy_keys_still_match(self, tmp_path):
        """TZ-15: keys truncated at 90 chars made two different long asserts collide."""
        root = tmp_path / "pkg"
        root.mkdir()
        long_a = "a_really_long_variable_name_number_one + another_really_long_variable_name_two + third_term < 1"
        long_b = long_a.replace("< 1", "< 2")
        assert long_a[:90] == long_b[:90]
        (root / "a.py").write_text(f"def f():\n    assert {long_a}\n    assert {long_b}\n", encoding="utf-8")
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps([f"a.py::{long_a}"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("< 2")):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        baseline.write_text(json.dumps([f"a.py::{long_a}", f"a.py::{long_b}"]), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)
        # a baseline written before the fix (one truncated key per assert) still accepts them
        baseline.write_text(json.dumps([f"a.py::{long_a[:90]}", f"a.py::{long_b[:90]}"]), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_strict_mode_treats_bare_truthiness_as_a_value_check(self):
        """TZ-16 (opt-in): `assert self.enabled` tests truthiness, which -O deletes."""
        for src in ("assert self.enabled", "assert x", "assert rows[0]"):
            assert is_narrowing_assert(_test(src))
            assert not is_narrowing_assert(_test(src), strict=True)
        assert is_narrowing_assert(_test("assert x is not None and y is None"), strict=True)

    def test_needs_justification_entries_are_rejected(self, tmp_path):
        """MT-1 applied through the shared Baseline: a recorded-but-unreasoned entry does not pass."""
        root = _package(tmp_path)
        baseline = tmp_path / "b.json"
        baseline.write_text(json.dumps({"a.py::x > 0": "NEEDS-JUSTIFICATION: fill me"}), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="NEEDS-JUSTIFICATION"):
            assert_no_value_bearing_asserts(root, baseline_path=baseline)
        baseline.write_text(json.dumps({"a.py::x > 0": "guards an internal invariant, checked upstream"}), encoding="utf-8")
        assert_no_value_bearing_asserts(root, baseline_path=baseline)

    def test_the_file_floor(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        with pytest.raises(pytest.fail.Exception, match="only 0 file"):
            assert_no_value_bearing_asserts(root, min_asserts_seen=0)
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        assert_no_value_bearing_asserts(root, min_asserts_seen=0)

    def test_the_floor(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="not reaching the code"):
            assert_no_value_bearing_asserts(_package(tmp_path), min_asserts_seen=10)
