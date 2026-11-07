"""Unit tests for the fail-open handler check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.fail_open_handlers import assert_no_new_fail_open_handlers, find_fail_open_handlers


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


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


@pytest.mark.parametrize("name", ["missing", "unresolved_modules", "absent"])
def test_recording_the_element_in_a_missing_collection_is_not_admit_on_error(tmp_path, name):
    body = f"""
        def probe(specs):
            {name} = {{}}
            for spec in specs:
                try:
                    load(spec)
                except ModuleNotFoundError:
                    {name}[spec] = 1
            return {name}
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


def test_return_true_outside_a_deciding_function_is_not_gate_returns_true(tmp_path):
    body = """
        def load(path):
            try:
                read(path)
            except OSError:
                return True
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


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


@pytest.mark.parametrize(
    "record",
    ['out.append(f"{path}: cannot be read: {exc}")', "problems.append(path)", "out.append(Problem(path, str(exc)))", 'out.append("%s: broken" % path)'],
)
def test_reporting_the_error_is_not_admit_on_error(tmp_path, record):
    body = f"""
        def scan(paths):
            out = []
            problems = []
            for path in paths:
                try:
                    read(path)
                except OSError as exc:
                    {record}
                    continue
            return out, problems
    """
    assert _rules(tmp_path, body) == []


@pytest.mark.parametrize("record", ["keep.append((spec, exc))", "keep.append(spec)", "keep.append(Wrapped(spec))"])
def test_keeping_the_element_next_to_the_error_is_still_admit_on_error(tmp_path, record):
    body = f"""
        def select(specs):
            keep = []
            for spec in specs:
                try:
                    score(spec)
                except Exception as exc:
                    {record}
            return keep
    """
    assert _rules(tmp_path, body) == ["admit_on_error"]


def _admit_rules(tmp_path, body):
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    return [(h.function, h.rule, h.lineno) for h in find_fail_open_handlers([tmp_path / "m.py"], tmp_path)]


@pytest.mark.parametrize(
    "handler",
    ["out.append(x)", "out += [x]", "out.extend([x])", "out.add(x)", "out[x] = True", "out.insert(0, x)", "yield x"],
)
def test_every_admission_spelling_is_reported_exactly_once(tmp_path, handler):
    src = f"def f(items, out):\n    for x in items:\n        try:\n            check(x)\n        except Exception:\n            {handler}\n"
    assert _admit_rules(tmp_path, src) == [("f", "admit_on_error", 5)]


@pytest.mark.parametrize("handler", ["failed.append(x)", "failed += [x]", "failed.add(x)", "out.append(f'{x}: {e}')"])
def test_recording_the_failure_is_not_an_admission(tmp_path, handler):
    src = f"def f(items, out, failed):\n    for x in items:\n        try:\n            check(x)\n        except Exception as e:\n            {handler}\n"
    assert _admit_rules(tmp_path, src) == []


def test_a_flag_set_true_in_the_handler_and_returned_is_return_true(tmp_path):
    src = "def check_allowed(x):\n    ok = False\n    try:\n        ok = check(x)\n    except Exception:\n        ok = True\n    return ok\n"
    assert _admit_rules(tmp_path, src) == [("check_allowed", "gate_returns_true", 5)]


def test_returning_the_exceptions_own_data_is_not_a_silent_default(tmp_path):
    body = """
        def read(conn):
            try:
                return conn.run()
            except DBError as exc:
                return exc.sqlstate
    """
    assert _rules(tmp_path, body) == []
    assert _rules(tmp_path, body.replace("return exc.sqlstate", "return None")) == ["silent_default_return"]


@pytest.mark.parametrize(
    "stored",
    ["count", "counts.get(x, 0) + 1"],
)
def test_a_counter_keyed_by_the_loop_element_is_not_admit_on_error(tmp_path, stored):
    # audit 37-F25: `d[name] = count` stores a number under the element as a key; it does not admit the element
    src = (
        "def f(items, counts, failures):\n    for x in items:\n        try:\n            check(x)\n        except Exception:\n"
        f"            count = {stored}\n            counts[x] = count\n"
    )
    assert _admit_rules(tmp_path, src) == []


# 2026-11-06 false positives: an optional import, an absent file, a message-carrying status and a cache miss are reasoned
# fallbacks. The same shapes with a broader catch or a required input stay flagged.


@pytest.mark.parametrize("caught", ["ImportError", "ModuleNotFoundError", "(ImportError, ModuleNotFoundError)"])
def test_an_optional_import_that_returns_a_default_is_not_flagged(tmp_path, caught):
    body = f"""
        def load_tqdm():
            try:
                import tqdm
            except {caught}:
                return None
            return tqdm.tqdm
    """
    assert _rules(tmp_path, body) == []


def test_a_handler_catching_an_import_error_and_an_os_error_is_still_flagged(tmp_path):
    body = """
        def load_tqdm(path):
            try:
                import tqdm
            except (ImportError, OSError):
                return None
            return tqdm.tqdm
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_an_absent_file_that_returns_a_default_is_not_flagged(tmp_path):
    body = """
        def read_payload(path):
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None
    """
    assert _rules(tmp_path, body) == []


def test_an_unreadable_file_that_returns_a_default_is_still_flagged(tmp_path):
    body = """
        def read_payload(path):
            try:
                return path.read_bytes()
            except OSError:
                return None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_a_status_record_that_carries_the_reason_is_not_flagged(tmp_path):
    body = """
        def summary(conn):
            try:
                return count(conn)
            except sqlite3.OperationalError:
                return DatasetSummary("VSAC", None, "not installed in this checkout")
    """
    assert _rules(tmp_path, body) == []


def test_a_bare_default_with_no_reason_is_still_flagged(tmp_path):
    body = """
        def summary(conn):
            try:
                return count(conn)
            except sqlite3.OperationalError:
                return DatasetSummary("VSAC", None, None)
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


@pytest.mark.parametrize("name", ["_file_stamp", "_kb_cache_token", "embedded_seed_stamp", "_probe_digest", "_signature", "_cache_token"])
def test_a_cache_or_stamp_miss_that_returns_none_is_not_flagged(tmp_path, name):
    body = f"""
        def {name}(path):
            try:
                return path.stat().st_mtime
            except OSError:
                return None
    """
    assert _rules(tmp_path, body) == []


def test_a_gate_helper_that_returns_none_on_error_is_still_flagged(tmp_path):
    body = """
        def load_candidates(path):
            try:
                return read(path)
            except OSError:
                return None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_handing_the_exception_to_the_caller_is_not_a_quiet_substitution(tmp_path):
    body = """
        import logging
        log = logging.getLogger(__name__)

        def attempt(batch):
            error = None
            try:
                return call(batch)
            except (ValueError, RuntimeError) as e:
                log.debug("batch failed")
                error = e
            return error
    """
    assert _rules(tmp_path, body) == []


def test_a_quiet_substitution_with_a_fallback_value_is_still_flagged(tmp_path):
    body = """
        import logging
        log = logging.getLogger(__name__)

        def attempt(batch):
            result = None
            try:
                result = call(batch)
            except ValueError:
                log.debug("batch failed")
                result = 0
            return result
    """
    assert _rules(tmp_path, body) == ["quiet_substitution"]


def test_a_warning_log_throttle_handler_that_stores_a_constant_is_not_admit_on_error(tmp_path):
    # mlframe _dcd_pair_su_batch.py::pair_su_batch (audit 2026-11-06 class A): the element scores 0.0 and is logged at WARNING
    body = """
        import logging

        def pair_su_batch(pairs, out):
            for i, pair in enumerate(pairs):
                try:
                    out[i] = score(pair)
                except Exception as e:
                    log_throttle(logger, "pair_su.score", logging.WARNING, "pair %r scores 0.0: %s", pair, e)
                    out[i] = 0.0
    """
    assert _rules(tmp_path, body) == []


def test_a_warning_handler_that_keeps_the_element_is_still_admit_on_error(tmp_path):
    # the loud log does not launder an admission that keeps the element
    body = """
        import logging

        def apply_gate(specs):
            survivors = []
            for spec in specs:
                try:
                    evaluate(spec)
                except Exception as e:
                    logger.warning("cannot evaluate %s: %s", spec, e)
                    survivors.append(spec)
    """
    assert _rules(tmp_path, body) == ["admit_on_error"]


@pytest.mark.parametrize(
    "body",
    [
        """
        def parse_age(birth):
            try:
                parts = [int(p) for p in birth.split("-")]
                return parts[0]
            except (ValueError, TypeError):
                return None
        """,
        """
        def parse_when(text):
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
        """,
    ],
)
def test_an_unparseable_value_that_returns_none_is_flagged(tmp_path, body):
    # The silent_default_return spec flags a parse that falls back to a default (test_silent_default_return.py).
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_a_parse_guard_around_a_lookup_is_still_flagged(tmp_path):
    body = """
        def load_age(path):
            try:
                return int(read_text(path))
            except ValueError:
                return None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_a_parse_guard_catching_os_error_is_still_flagged(tmp_path):
    body = """
        def parse_age(birth):
            try:
                return int(birth)
            except OSError:
                return None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_a_missing_local_table_that_answers_none_is_not_flagged(tmp_path):
    body = """
        def display_ru(code, db_path=None):
            db = sqlite3.connect(db_path)
            try:
                row = db.execute("SELECT display_ru FROM loinc_code_ru WHERE code = ?", (code,)).fetchone()
            except sqlite3.OperationalError:
                return None
            finally:
                db.close()
            return row[0] if row else None
    """
    assert _rules(tmp_path, body) == []


def test_a_broad_sqlite_error_that_answers_none_is_still_flagged(tmp_path):
    body = """
        def display_ru(code, db_path=None):
            db = sqlite3.connect(db_path)
            try:
                row = db.execute("SELECT display_ru FROM loinc_code_ru WHERE code = ?", (code,)).fetchone()
            except sqlite3.Error:
                return None
            return row[0] if row else None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_an_operational_error_around_a_non_sqlite_call_is_still_flagged(tmp_path):
    body = """
        def load_rows(path):
            try:
                return read_rows(path)
            except sqlite3.OperationalError:
                return None
    """
    assert _rules(tmp_path, body) == ["silent_default_return"]


def test_a_debug_log_throttle_handler_that_substitutes_a_value_is_a_quiet_substitution(tmp_path):
    # audit 2026-11-06 class E / tracker 37-F40: log_throttle at DEBUG is as quiet as logger.debug, so the fallback is silent
    body = """
        import logging

        def fit(batch):
            try:
                result = run(batch)
            except Exception as e:
                log_throttle(logger, "fit.batch", logging.DEBUG, "batch failed: %s", e)
                result = 0
            return result
    """
    assert _rules(tmp_path, body) == ["quiet_substitution"]


def test_a_warning_log_throttle_handler_that_substitutes_a_value_is_accepted(tmp_path):
    # the negative control: a loud log_throttle substitution is not flagged, so the DEBUG rule does not over-flag
    body = """
        import logging

        def fit(batch):
            try:
                result = run(batch)
            except Exception as e:
                log_throttle(logger, "fit.batch", logging.WARNING, "batch failed: %s", e)
                result = 0
            return result
    """
    assert _rules(tmp_path, body) == []


@pytest.mark.parametrize(
    "exc, stmt, flagged",
    [
        ("KeyError", "return d[k]", False),
        ("StopIteration", "return next(it)", False),
        ("queue.Empty", "return q.get_nowait()", False),
        ("ProcessLookupError", "return os.kill(pid, 0)", False),
        ("ZeroDivisionError", "return a / b", False),
        ("OSError", "return d[k]", True),
        ("Exception", "return d[k]", True),
        ("TimeoutError", "return d[k]", True),
        ("KeyError", "x = prepare(d); return d[k]", True),
    ],
)
def test_a_control_flow_exception_around_one_statement_is_not_a_silent_default(tmp_path, exc, stmt, flagged):
    """Audit 41-F2: ``try: return d[k] except KeyError: return None`` is ``d.get(k)``."""
    body = f"""
        def lookup(d, k, it, q, pid, a, b):
            try:
                {stmt}
            except {exc}:
                return None
    """
    assert (_rules(tmp_path, body) == ["silent_default_return"]) is flagged


class TestGateSwallowsError:
    """40-F9: a gate-named function that turns a failure into a pass by pass, continue, a clean exit or a skip."""

    @pytest.mark.parametrize(
        "handler",
        [
            "except Exception:\n        sys.exit(0)",
            "except OSError:\n        pass",
            "except SyntaxError:\n        continue",
            "except Exception:\n        pytest.skip('cannot read')",
            "except (OSError, ValueError):\n        print(e)",
            "except:\n        pass",
        ],
    )
    def test_the_swallowing_shapes_are_found_in_a_gate_named_function(self, tmp_path, handler):
        indented = handler.replace(chr(10) + " " * 8, chr(10) + " " * 12)
        body = f"import sys\n\n\ndef check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        {indented}\n    return 0\n"
        assert _rules(tmp_path, body) == ["gate_swallows_error"]

    @pytest.mark.parametrize(
        "text",
        [
            "def helper(files):\n    for f in files:\n        try:\n            run(f)\n        except OSError:\n            pass\n",  # not gate-named
            "def check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        except ImportError:\n            pass\n",  # optional dependency
            "def check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        except OSError:\n            logger.warning('x')\n            continue\n",
            "def check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        except OSError:\n            raise\n",
            "def check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        except OSError:  # best-effort: cache probe\n            pass\n",
            "def check_gate(files):\n    for f in files:\n        try:\n            run(f)\n        except Exception:\n            sys.exit(2)\n",
        ],
    )
    def test_negative_controls(self, tmp_path, text):
        assert "gate_swallows_error" not in _rules(tmp_path, text)
