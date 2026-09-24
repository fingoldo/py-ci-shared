"""Unit tests for the fail-open handler check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.fail_open_handlers import assert_no_new_fail_open_handlers, find_fail_open_handlers


def _module(tmp_path: Path, body: str, name: str = "mod.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _rules(tmp_path: Path, body: str) -> list[str]:
    return sorted(h.rule for h in find_fail_open_handlers([_module(tmp_path, body)], tmp_path))


def test_admit_on_error_is_found(tmp_path):
    body = """
        def apply_gate(specs):
            survivors = []
            for spec in specs:
                try:
                    evaluate(spec)
                except Exception:
                    survivors.append(spec)  # cannot evaluate
                    continue
                survivors.append(spec)
            return survivors
    """
    assert _rules(tmp_path, body) == ["admit_on_error"]


def test_a_rejecting_handler_is_not_admit_on_error(tmp_path):
    body = """
        def apply_gate(specs):
            rejected = []
            for spec in specs:
                try:
                    evaluate(spec)
                except Exception as e:
                    logger.warning("rejecting %s: %s", spec, e)
                    rejected.append(spec.name)
    """
    assert _rules(tmp_path, body) == []


def test_recording_the_failed_element_is_not_admit_on_error(tmp_path):
    body = """
        def fit_all(columns):
            failed = []
            for k in columns:
                try:
                    fit(k)
                except Exception:
                    failed.append(k)
            return failed
    """
    assert _rules(tmp_path, body) == []


def test_a_deciding_function_returning_true_on_error_is_found(tmp_path):
    body = """
        def spec_is_ok(spec):
            try:
                return evaluate(spec) < 1.0
            except Exception:
                return True
    """
    assert _rules(tmp_path, body) == ["gate_returns_true"]


def test_return_true_outside_a_deciding_function_is_not_flagged(tmp_path):
    body = """
        def load(path):
            try:
                read(path)
            except OSError:
                return True
    """
    assert _rules(tmp_path, body) == []


def test_a_quiet_substitution_is_found(tmp_path):
    body = """
        def fit(y):
            try:
                params = heavy_fit(y)
            except Exception as e:
                logger.debug("fit failed, using the global params: %s", e)
                params = GLOBAL
            return params
    """
    assert _rules(tmp_path, body) == ["quiet_substitution"]


@pytest.mark.parametrize(
    "handler",
    [
        'logger.warning("fit failed: %s", e)\n                params = GLOBAL',
        'log_throttle(logger, "k", logging.WARNING, "fit failed: %s", e)\n                params = GLOBAL',
        'logger.debug("fit failed: %s", e)\n                raise',
    ],
)
def test_a_loud_substitution_is_not_flagged(tmp_path, handler):
    body = f"""
        def fit(y):
            try:
                params = heavy_fit(y)
            except Exception as e:
                {handler}
            return params
    """
    assert _rules(tmp_path, body) == []


def test_the_best_effort_marker_is_honoured(tmp_path):
    body = """
        def render(fig):
            try:
                title = compute_title(fig)
            except Exception as e:  # best-effort: a missing title never changes a result
                logger.debug("title failed: %s", e)
                title = ""
            return title
    """
    assert _rules(tmp_path, body) == []


def test_a_finite_guarded_reject_is_found(tmp_path):
    body = """
        import math
        def threshold_gate(specs, scores, thr):
            kept = []
            for spec, score in zip(specs, scores):
                if math.isfinite(score) and score >= thr:
                    continue
                kept.append(spec)
            return kept
    """
    assert _rules(tmp_path, body) == ["finite_guarded_reject"]


def test_a_reject_that_treats_non_finite_as_a_failure_is_not_flagged(tmp_path):
    body = """
        import math
        def threshold_gate(specs, scores, thr):
            kept = []
            for spec, score in zip(specs, scores):
                if not math.isfinite(score) or score >= thr:
                    continue
                kept.append(spec)
            return kept
    """
    assert _rules(tmp_path, body) == []


def test_the_ratchet_accepts_baselined_scopes_and_reports_new_and_stale(tmp_path):
    body = """
        def fit(y):
            try:
                params = heavy_fit(y)
            except Exception as e:
                logger.debug("x: %s", e)
                params = GLOBAL
            return params
    """
    mod = _module(tmp_path, body)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(AssertionError, match="quiet_substitution"):
        assert_no_new_fail_open_handlers([mod], tmp_path, baseline)
    baseline.write_text(json.dumps({"mod.py::fit::quiet_substitution": "pre-existing"}), encoding="utf-8")
    assert_no_new_fail_open_handlers([mod], tmp_path, baseline)
    mod.write_text("def fit(y):\n    return heavy_fit(y)\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="lower or remove"):
        assert_no_new_fail_open_handlers([mod], tmp_path, baseline)


def test_admitting_a_tuple_built_from_the_element_is_found(tmp_path):
    body = """
        def select(specs):
            keep = []
            for spec in specs:
                try:
                    keep.append((spec, score(spec)))
                except Exception:
                    keep.append((spec, 0))
            return keep
    """
    assert _rules(tmp_path, body) == ["admit_on_error"]


def test_recording_a_tuple_into_a_failure_list_is_not(tmp_path):
    body = """
        def select(specs):
            failed = []
            for spec in specs:
                try:
                    score(spec)
                except Exception:
                    failed.append((spec, 0))
            return failed
    """
    assert _rules(tmp_path, body) == []


@pytest.mark.parametrize("level,expected", [("logging.DEBUG", ["quiet_substitution"]), ("DEBUG", ["quiet_substitution"]), ("logging.WARNING", [])])
def test_log_dot_log_is_judged_by_its_level(tmp_path, level, expected):
    body = f"""
        def fit(y):
            try:
                params = heavy_fit(y)
            except Exception as e:
                log.log({level}, "x: %s", e)
                params = GLOBAL
            return params
    """
    assert _rules(tmp_path, body) == expected


@pytest.mark.parametrize("guard", ["not np.isnan(score)", "not math.isinf(score)"])
def test_a_not_isnan_guarded_reject_is_found(tmp_path, guard):
    body = f"""
        def select(specs):
            for spec in specs:
                score = evaluate(spec)
                if {guard} and score > LIMIT:
                    continue
                yield spec
    """
    assert _rules(tmp_path, body) == ["finite_guarded_reject"]


def test_module_level_code_is_scanned(tmp_path):
    body = """
        for spec in SPECS:
            try:
                validate(spec)
            except Exception:
                ACTIVE.append(spec)
    """
    found = find_fail_open_handlers([_module(tmp_path, body)], tmp_path)
    assert [(h.function, h.rule) for h in found] == [("<module>", "admit_on_error")]


def test_same_named_methods_have_distinct_scopes(tmp_path):
    body = """
        class A:
            def check(self, x):
                try:
                    return run(x)
                except Exception:
                    return True

        class B:
            def check(self, x):
                try:
                    return run(x)
                except Exception:
                    return True
    """
    found = find_fail_open_handlers([_module(tmp_path, body)], tmp_path)
    assert sorted(h.scope for h in found) == ["mod.py::A.check::gate_returns_true", "mod.py::B.check::gate_returns_true"]
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mod.py::A.check::gate_returns_true": "known"}), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"in B\.check"):
        assert_no_new_fail_open_handlers([tmp_path / "mod.py"], tmp_path, baseline)


def test_empty_files_unparsable_files_and_a_missing_baseline_fail(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="parsed"):
        assert_no_new_fail_open_handlers([], tmp_path, baseline)
    good = _module(tmp_path, "def f():\n    return 1\n")
    assert_no_new_fail_open_handlers([good], tmp_path, baseline)
    broken = _module(tmp_path, "def f(:\n", name="broken.py")
    with pytest.raises(AssertionError, match=r"broken.py"):
        assert_no_new_fail_open_handlers([good, broken], tmp_path, baseline)
    with pytest.raises(AssertionError, match="does not exist"):
        assert_no_new_fail_open_handlers([good], tmp_path, tmp_path / "missing.json")


def test_a_refresh_writes_counted_scopes_that_then_pass(tmp_path):
    body = """
        def fit(y):
            try:
                a = heavy_fit(y)
            except Exception as e:
                logger.debug("x: %s", e)
                a = GLOBAL
            try:
                b = heavy_fit(y)
            except Exception as e:
                logger.debug("x: %s", e)
                b = GLOBAL
            return a, b
    """
    mod = _module(tmp_path, body)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_no_new_fail_open_handlers([mod], tmp_path, baseline, refresh=True)
    assert sorted(json.loads(baseline.read_text(encoding="utf-8"))) == ["mod.py::fit::quiet_substitution", "mod.py::fit::quiet_substitution#2"]
    assert_no_new_fail_open_handlers([mod], tmp_path, baseline)


def test_a_file_outside_the_root_does_not_raise(tmp_path):
    inner = tmp_path / "a"
    inner.mkdir()
    mod = _module(inner, "def check(x):\n    try:\n        return run(x)\n    except Exception:\n        return True\n")
    found = find_fail_open_handlers([mod], tmp_path / "elsewhere")
    assert [h.rule for h in found] == ["gate_returns_true"] and found[0].path.endswith("a/mod.py")
