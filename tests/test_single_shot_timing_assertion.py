"""Tests for py_ci_shared.single_shot_timing_assertion."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.single_shot_timing_assertion import (
    RULE,
    RULE_SKIP,
    RULE_TIGHT,
    assert_single_shot_timing_assertion,
    find_single_shot_timing_assertion,
)

BOM = b"\xef\xbb\xbf"

SINGLE_SHOT = """
import time

def test_seed_speedup():
    t0 = time.perf_counter()
    serial()
    t_serial = time.perf_counter() - t0
    t0 = time.perf_counter()
    parallel()
    t_parallel = time.perf_counter() - t0
    assert t_serial / t_parallel >= 1.15
"""


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, body: str, **kw: object) -> list[tuple[str, int]]:
    root = _corpus(tmp_path, {"test_x.py": textwrap.dedent(body).encode()})
    return [(f.rule, f.line) for f in find_single_shot_timing_assertion(root, use_git=False, **kw)]  # type: ignore[arg-type]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"test_x.py": textwrap.dedent(SINGLE_SHOT).encode()})
    (finding,) = find_single_shot_timing_assertion(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("test_x.py", 11, RULE)
    assert finding.message.startswith("test_seed_speedup: compares wall-clock durations measured once per side")


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    body = """
        import time

        def _once(fn):
            t0 = time.perf_counter()
            fn()
            return time.perf_counter() - t0

        def test_seed_speedup():
            t_serial = min(_once(serial) for _ in range(3))
            t_parallel = min(_once(parallel) for _ in range(3))
            assert t_serial / t_parallel >= 1.15
    """
    assert _found(tmp_path, body) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = [
        (f.rule, f.line)
        for f in find_single_shot_timing_assertion(_corpus(tmp_path / "a", {"test_x.py": textwrap.dedent(SINGLE_SHOT).encode()}), use_git=False)
    ]
    bommed = [
        (f.rule, f.line)
        for f in find_single_shot_timing_assertion(_corpus(tmp_path / "b", {"test_x.py": BOM + textwrap.dedent(SINGLE_SHOT).encode()}), use_git=False)
    ]
    assert plain == bommed == [(RULE, 11)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"test_ok.py": b"x = 1\n", "test_broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"test_broken\.py"):
        find_single_shot_timing_assertion(root, use_git=False)
    with pytest.raises(AssertionError, match=r"test_broken\.py"):
        assert_single_shot_timing_assertion(root, use_git=False)
    assert find_single_shot_timing_assertion(root, use_git=False, allow_unparsed=True) == []
    assert_single_shot_timing_assertion(root, use_git=False, allow_unparsed=True)


def test_an_empty_corpus_fails_the_floor(tmp_path):
    root = _corpus(tmp_path, {"notes.txt": b"nothing"})
    with pytest.raises(EmptyScanError):
        find_single_shot_timing_assertion(root, use_git=False)
    one = _corpus(tmp_path / "one", {"test_a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_single_shot_timing_assertion(one, use_git=False, min_files=2)
    assert find_single_shot_timing_assertion(one, use_git=False, min_files=1) == []


def test_the_assert_names_the_site_and_the_baseline_ratchets(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path, {"test_x.py": textwrap.dedent(SINGLE_SHOT).encode()})
    with pytest.raises(AssertionError, match=r"test_x\.py:11"):
        assert_single_shot_timing_assertion(root, use_git=False)
    baseline = tmp_path / "bl.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_single_shot_timing_assertion(root, use_git=False, baseline_path=baseline)
    with pytest.raises(pytest.skip.Exception):
        assert_single_shot_timing_assertion(root, use_git=False, baseline_path=baseline, refresh=True)
    assert_single_shot_timing_assertion(root, use_git=False, baseline_path=baseline)


class TestSingleShotShapes:
    def test_a_bare_ceiling_on_one_run(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                assert elapsed < 5.0
        """
        assert _found(tmp_path, body) == [(RULE, 7)]

    def test_a_ceiling_written_inline(self, tmp_path):
        body = """
            from time import perf_counter as pc
            def test_a():
                t0 = pc()
                run()
                assert pc() - t0 <= 2
        """
        assert _found(tmp_path, body) == [(RULE, 6)]

    def test_a_lower_bound_on_one_run_cannot_flake_from_contention(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                assert elapsed >= 0.2
                assert 0.2 <= elapsed
        """
        assert _found(tmp_path, body) == []

    def test_a_ceiling_with_the_measurement_on_the_big_side(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                assert 5.0 > elapsed
                assert 5.0 < elapsed
        """
        assert _found(tmp_path, body) == [(RULE, 7)]

    def test_a_ratio_through_a_named_speedup(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter(); cpu(); t_cpu = time.perf_counter() - t0
                t0 = time.perf_counter(); gpu(); t_gpu = time.perf_counter() - t0
                speedup = t_cpu / t_gpu
                assert speedup >= 3.0
        """
        assert _found(tmp_path, body) == [(RULE, 7)]

    def test_a_timing_helper_defined_in_the_module_counts_as_one_run(self, tmp_path):
        body = """
            import time
            def _wall(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                a = _wall(slow)
                b = _wall(fast)
                assert a / b >= 2
        """
        assert _found(tmp_path, body) == [(RULE, 10)]

    def test_a_helper_from_another_module_is_known_by_name(self, tmp_path):
        body = """
            from helpers import wall
            def test_a():
                assert wall(slow) / wall(fast) >= 2
        """
        assert _found(tmp_path, body) == []
        assert _found(tmp_path, body, single_shot_helpers=["wall"]) == [(RULE, 4)]

    def test_timeit_with_number_one_is_one_run(self, tmp_path):
        body = """
            import timeit
            def test_a():
                a = timeit.timeit(slow, number=1)
                b = timeit.timeit(fast, number=1)
                assert a / b > 2
        """
        assert _found(tmp_path, body) == [(RULE, 6)]

    def test_the_unittest_spelling_is_read(self, tmp_path):
        body = """
            import time
            class T:
                def test_a(self):
                    t0 = time.perf_counter()
                    run()
                    d = time.perf_counter() - t0
                    self.assertLess(d, 1.0)
                    self.assertGreater(d, 0.0)
        """
        assert _found(tmp_path, body) == [(RULE, 8)]

    def test_each_finding_names_its_function(self, tmp_path):
        root = _corpus(tmp_path, {"test_x.py": (textwrap.dedent(SINGLE_SHOT) + textwrap.dedent(SINGLE_SHOT.replace("seed_speedup", "other"))).encode()})
        names = sorted(f.message.split(":")[0] for f in find_single_shot_timing_assertion(root, use_git=False))
        assert names == ["test_other", "test_seed_speedup"]

    def test_a_non_test_function_is_not_read(self, tmp_path):
        body = SINGLE_SHOT.replace("test_seed_speedup", "seed_speedup")
        assert _found(tmp_path, body) == []


class TestAcceptedShapes:
    @pytest.mark.parametrize(
        "assign",
        [
            pytest.param("t = min(_once(f) for _ in range(3))", id="min-of-generator"),
            pytest.param("t = sorted([_once(f) for _ in range(3)])[0]", id="sorted-first"),
            pytest.param("t = statistics.median(_once(f) for _ in range(5))", id="median"),
            pytest.param("t = best_of(f)", id="best-of-helper"),
            pytest.param("t = timeit.repeat(f, number=1, repeat=3)", id="timeit-repeat"),
            pytest.param("t = timeit.timeit(f)", id="timeit-default-number"),
        ],
    )
    def test_best_of_n_sides_are_clean(self, tmp_path, assign):
        body = f"""
            import statistics, time, timeit
            def _once(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                {assign}
                u = min(_once(g) for _ in range(3))
                assert t / u >= 1.5
                assert t < 9.0
        """
        assert _found(tmp_path, body) == []

    def test_the_loop_form_of_best_of_n(self, tmp_path):
        body = """
            import time
            def test_a():
                times = []
                for _ in range(3):
                    t0 = time.perf_counter()
                    run()
                    times.append(time.perf_counter() - t0)
                best = min(times)
                assert best / 1.0 >= 0.1
                assert best < 5.0
        """
        assert _found(tmp_path, body) == []

    def test_a_duration_assigned_in_a_loop_is_repeated(self, tmp_path):
        body = """
            import time
            def test_a():
                for _ in range(3):
                    t0 = time.perf_counter()
                    run()
                    last = time.perf_counter() - t0
                assert last < 5.0
        """
        assert _found(tmp_path, body) == []

    def test_a_helper_and_a_work_count_are_not_comparisons(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                n = run()
                elapsed = time.perf_counter() - t0
                assert perf_speedup_floor(elapsed, 1.0, 3.0)
                assert n == 1000
                assert elapsed
        """
        assert _found(tmp_path, body) == []

    def test_a_comparison_of_unmeasured_numbers_in_a_timing_test_is_not_read(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                run()
                time.perf_counter() - t0
                ratio = 4 / 2
                assert ratio >= 1.0
                assert 4 / 2 >= 1.0
        """
        assert _found(tmp_path, body) == []

    @pytest.mark.parametrize("marker", ["hang_guard", "perf"])
    def test_exempt_markers_on_the_function_class_or_module(self, tmp_path, marker):
        ceiling = "def test_a():\n    t0 = time.perf_counter()\n    run()\n    assert time.perf_counter() - t0 < 90\n"
        for body in (
            f"import time, pytest\n@pytest.mark.{marker}\n{ceiling}",
            f"import time, pytest\n@pytest.mark.{marker}\nclass TestX:\n    {ceiling.replace(chr(10), chr(10) + '    ')}",
            f"import time, pytest\npytestmark = pytest.mark.{marker}\n{ceiling}",
        ):
            assert _found(tmp_path, body) == []
        assert _found(tmp_path, f"import time, pytest\n{ceiling}") == [(RULE, 5)]
        assert _found(tmp_path, f"import time, pytest\n@pytest.mark.{marker}\n{ceiling}", exempt_markers=["other"]) == [(RULE, 6)]

    def test_a_nested_helper_best_of_does_not_launder_an_outer_single_shot(self, tmp_path):
        body = """
            import time
            def test_a():
                def _best():
                    return min(1, 2)
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                assert elapsed < 1.0
        """
        assert _found(tmp_path, body) == [(RULE, 9)]


class TestFalsePositiveShapesFromRealCorpora:
    def test_a_helper_returning_a_result_and_a_timing_only_taints_the_timing(self, tmp_path):
        body = """
            import time
            def _fit(cap):
                t0 = time.perf_counter()
                recovered = run(cap)
                total = time.perf_counter() - t0
                return recovered, total
            def test_a():
                rec_a, t_a = _fit(None)
                rec_b, t_b = _fit(100)
                assert rec_b >= rec_a - 1
                assert t_b < t_a
        """
        assert _found(tmp_path, body) == [(RULE, 12)]

    def test_a_mean_over_a_loop_between_the_stamps_is_not_one_sample(self, tmp_path):
        body = """
            import time
            def test_a():
                t0 = time.perf_counter()
                for _ in range(20):
                    work()
                mean = (time.perf_counter() - t0) / 20
                assert mean < 0.5
        """
        assert _found(tmp_path, body) == []

    def test_a_loop_before_the_first_stamp_does_not_average_it(self, tmp_path):
        body = """
            import time
            def test_a():
                for _ in range(20):
                    warm()
                t0 = time.perf_counter()
                work()
                one = time.perf_counter() - t0
                assert one < 0.5
        """
        assert _found(tmp_path, body) == [(RULE, 9)]

    @pytest.mark.parametrize("bound", ["perf_time_budget(30.0)", "scaled_budget * 2", "perf_speedup_floor(3.0)"])
    def test_a_bound_the_host_scales_is_not_a_single_shot_claim(self, tmp_path, bound):
        body = f"""
            import time
            def test_a():
                scaled_budget = perf_time_budget(5.0)
                t0 = time.perf_counter()
                work()
                wall = time.perf_counter() - t0
                assert wall < {bound}
        """
        assert _found(tmp_path, body) == []

    def test_a_name_assigned_from_a_calibration_helper_carries_it(self, tmp_path):
        body = """
            import time
            def _w(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                floor = perf_speedup_floor(2.0)
                speedup = _w(a) / _w(b)
                assert speedup >= floor
                assert speedup >= 2.0
        """
        assert _found(tmp_path, body) == [(RULE, 11)]

    def test_a_ratio_held_in_a_name_is_a_required_ratio_not_a_race(self, tmp_path):
        body = """
            import time
            def _w(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                a = min(_w(f) for _ in range(5))
                b = min(_w(g) for _ in range(5))
                speedup = a / b
                assert speedup >= 1.1
        """
        assert _found(tmp_path, body) == []

    def test_an_offset_on_the_bound_is_not_read_as_a_race(self, tmp_path):
        body = """
            import time
            def _w(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                a = min(_w(f) for _ in range(5))
                b = min(_w(g) for _ in range(5))
                assert a <= 3.0 * b + 5.0
        """
        assert _found(tmp_path, body) == []


class TestTightRace:
    @pytest.mark.parametrize(
        ("expr", "flagged"),
        [
            ("a <= b * 1.05", True),
            ("a >= b * 0.95", True),
            ("a <= b * 1.24", True),
            ("a <= b * 1.25", False),
            ("a <= b * 1.5", False),
            ("a / b >= 1.1", False),
            ("a / b <= 1.5", False),
            ("a <= b", False),
            ("a * 1.0 <= b", True),
        ],
    )
    def test_the_slack_boundary_even_for_best_of_n(self, tmp_path, expr, flagged):
        body = f"""
            import time
            def _w(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                a = min(_w(f) for _ in range(5))
                b = min(_w(g) for _ in range(5))
                assert {expr}
        """
        assert _found(tmp_path, body) == ([(RULE_TIGHT, 10)] if flagged else [])

    def test_a_tight_single_shot_race_is_reported_once_as_single_shot(self, tmp_path):
        body = """
            import time
            def _w(fn):
                t0 = time.perf_counter()
                fn()
                return time.perf_counter() - t0
            def test_a():
                a = _w(f)
                b = _w(g)
                assert a <= b * 1.05
        """
        assert _found(tmp_path, body) == [(RULE, 10)]

    def test_two_durations_read_off_a_result_are_a_race(self, tmp_path):
        body = """
            def test_a():
                stats = run()
                assert stats.saved_seconds > 0.05 * stats.median_seconds
        """
        assert _found(tmp_path, body) == [(RULE, 4)]

    def test_one_reported_duration_against_a_config_value_is_not_a_race(self, tmp_path):
        body = """
            def test_a():
                stats = run()
                assert stats.elapsed_seconds < cfg.timeout_seconds
                assert stats.elapsed_seconds < 5
        """
        assert _found(tmp_path, body) == []


class TestSkipAfterMeasure:
    def test_xdist_skip_after_the_timing_is_reported(self, tmp_path):
        body = """
            import os, time, pytest
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                if os.environ.get("PYTEST_XDIST_WORKER"):
                    pytest.skip("timing unreliable under -n")
                assert min(elapsed, elapsed) >= 0
        """
        assert _found(tmp_path, body) == [(RULE_SKIP, 8)]

    def test_the_reason_alone_is_enough(self, tmp_path):
        body = """
            import time, pytest
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                if elapsed > 1:
                    pytest.skip("machine is too loaded for a timing check")
        """
        assert _found(tmp_path, body) == [(RULE_SKIP, 8)]

    def test_skipping_before_measuring_is_honest_and_not_reported(self, tmp_path):
        body = """
            import os, time, pytest
            def test_a():
                if os.environ.get("PYTEST_XDIST_WORKER"):
                    pytest.skip("timing unreliable under -n")
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                assert min(elapsed, elapsed) >= 0
        """
        assert _found(tmp_path, body) == []

    def test_an_unrelated_skip_after_the_timing_is_not_reported(self, tmp_path):
        body = """
            import time, pytest
            def test_a():
                t0 = time.perf_counter()
                run()
                elapsed = time.perf_counter() - t0
                if not have_gpu():
                    pytest.skip("no CUDA device")
        """
        assert _found(tmp_path, body) == []

    def test_a_test_that_never_measures_may_skip_for_xdist(self, tmp_path):
        body = """
            import pytest
            def test_a():
                if xdist_active():
                    pytest.skip("shared runner, order matters")
        """
        assert _found(tmp_path, body) == []


def test_extra_timer_calls_are_recognised(tmp_path):
    body = """
        from mypkg.clock import now
        def test_a():
            t0 = now()
            run()
            elapsed = now() - t0
            assert elapsed < 1.0
    """
    assert _found(tmp_path, body) == []
    assert _found(tmp_path, body, timer_calls=["mypkg.clock.now"]) == [(RULE, 7)]


def test_only_test_files_are_read_by_default(tmp_path):
    root = _corpus(tmp_path, {"helpers.py": textwrap.dedent(SINGLE_SHOT).encode(), "test_a.py": b"x = 1\n"})
    assert find_single_shot_timing_assertion(root, use_git=False) == []
    assert len(find_single_shot_timing_assertion(root, use_git=False, patterns=["*.py"])) == 1
