"""Unit tests for the nondiscriminating assertion-shape rules. Parsed source, no mocking, matching this package's convention."""

from __future__ import annotations

import ast
import textwrap

import pytest

from py_ci_shared._core import ImportAliases
from py_ci_shared.nondiscriminating_shapes import shape_reasons


def _reasons(src: str, extra_shapes=("nonempty-only-assert",)) -> list[str]:
    fn = ast.parse(textwrap.dedent(src)).body[0]
    return shape_reasons(fn, extra_shapes=extra_shapes)


@pytest.mark.parametrize("cmp", ["0 < rmse < 100", "0.5 < rmse < 50", "-1 <= r <= 10"])
def test_wide_literal_ranges_are_found(cmp):
    assert _reasons(f"def test_x():\n    assert {cmp}\n") == ["wide-literal-range"]


@pytest.mark.parametrize("cmp", ["0 <= p <= 1", "0.9 < ratio < 1.1", "1 <= k <= 5", "lo < x < hi"])
def test_narrow_or_probability_ranges_are_not_flagged(cmp):
    assert _reasons(f"def test_x():\n    assert {cmp}\n") == []


@pytest.mark.parametrize("cmp", ["pred.min() > 0.5 * y.min()", "pred.max() < 1.5 * y.max()", "np.max(pred) <= y.max() * 2"])
def test_envelope_asserts_are_found(cmp):
    assert _reasons(f"def test_x():\n    assert {cmp}\n") == ["envelope-assert"]


def test_a_plain_min_comparison_is_not_an_envelope():
    assert _reasons("def test_x():\n    assert pred.min() > y.min()\n") == []


def test_a_median_error_in_a_round_trip_test_is_found():
    src = """
        def test_forward_inverse_round_trip():
            back = inverse(forward(y))
            assert np.median(np.abs(back - y)) < 1e-6
    """
    assert _reasons(src) == ["median-roundtrip"]


def test_a_median_error_elsewhere_or_a_max_error_is_not_flagged():
    assert _reasons("def test_scores():\n    assert np.median(np.abs(a - b)) < 1\n") == []
    assert _reasons("def test_round_trip():\n    assert np.max(np.abs(a - b)) < 1e-9\n") == []


def test_a_data_dependent_skip_is_found():
    src = """
        def test_selection():
            result = run_discovery(frame)
            if not result.specs:
                pytest.skip("no spec survived")
            assert result.specs[0].gain > 0
    """
    assert _reasons(src) == ["late-skip"]


@pytest.mark.parametrize(
    "src",
    [
        "def test_gpu():\n    if not has_gpu():\n        pytest.skip('no gpu')\n    x = run()\n    assert x\n",
        "def test_env():\n    x = run()\n    if sys.platform == 'win32':\n        pytest.skip('posix only')\n    assert x\n",
        "def test_dep():\n    x = run()\n    try:\n        import torch\n    except ImportError:\n        pytest.skip('torch missing')\n    assert x\n",
    ],
)
def test_environment_probe_skips_are_not_flagged(src):
    assert _reasons(src) == []


class TestAuditRegressions:
    def test_an_identifier_containing_os_is_not_an_environment_probe(self):
        src = "def test_x():\n    loss = train()\n    if loss is None:\n        pytest.skip('no loss')\n    assert loss < 1\n"
        assert _reasons(src) == ["late-skip"]
        probe = "def test_x():\n    loss = train()\n    if os.environ.get('CI'):\n        pytest.skip('ci')\n    assert loss < 1\n"
        assert _reasons(probe) == []

    @pytest.mark.parametrize(
        "cond",
        [
            "not HAS_TORCH",
            "not torch.cuda.is_available()",
            "shutil.which('dot') is None",
            "not TORCH_AVAILABLE",
            "not callbacks_supported()",
            "(int(major), int(minor)) < (7, 0)",
            "vram_total < 4 * 1024**3",
        ],
    )
    def test_real_probes_are_still_recognised(self, cond):
        assert _reasons(f"def test_x():\n    x = run()\n    if {cond}:\n        pytest.skip('env')\n    assert x\n") == []

    def test_a_skip_in_a_try_body_is_not_exempt_but_one_in_an_except_is(self):
        body = "def test_x():\n    x = run()\n    try:\n        if not x:\n            pytest.skip('empty')\n    finally:\n        pass\n    assert x\n"
        assert _reasons(body) == ["late-skip"]
        handler = "def test_x():\n    x = run()\n    try:\n        import torch\n    except ImportError:\n        pytest.skip('torch')\n    assert x\n"
        assert _reasons(handler) == []

    @pytest.mark.parametrize("cmp", ["100 > rmse > 0", "50 >= r >= -1"])
    def test_reversed_wide_ranges_are_found(self, cmp):
        assert _reasons(f"def test_x():\n    assert {cmp}\n") == ["wide-literal-range"]

    @pytest.mark.parametrize("cond", ["majority_share < 0.1", "minority_count == 0", "n_supporters < 3", "total_rows < 100"])
    def test_data_decided_skips_with_similar_words_are_still_flagged(self, cond):
        """``supported``/``major``/``minor``/``vram`` are matched as WHOLE identifier parts: ``majority_share``,
        ``minority_count`` and ``n_supporters`` are data, and skipping on them still disables the test with the data."""
        src = f"def test_x():\n    result = run()\n    if {cond}:\n        pytest.skip('degenerate data')\n    assert result\n"
        assert _reasons(src) == ["late-skip"]

    def test_a_median_canary_next_to_a_per_element_check_is_not_flagged(self):
        """``assert median(...) > 2  # canary: the fixture amplifies`` followed by ``assert_allclose(moved, isolated)`` is a
        precondition plus a real check; only a median that is the sole verdict hides half-wrong rows."""
        canary = (
            "def test_inverse_is_local():\n"
            "    assert np.median(np.abs(dense) / np.abs(sparse)) > 2.0\n"
            "    np.testing.assert_allclose(moved, isolated, rtol=1e-6)\n"
        )
        assert _reasons(canary) == []
        assert_next = "def test_inverse_is_local():\n    assert np.median(np.abs(a - b)) < 1e-6\n    assert a.shape == b.shape\n"
        assert _reasons(assert_next) == []

    def test_a_sole_median_verdict_in_an_inverse_test_is_still_flagged(self):
        src = "def test_roundtrip():\n    assert np.median(np.abs(back - x)) < 1e-6\n"
        assert _reasons(src) == ["median-roundtrip"]

    def test_a_baseline_file_existence_check_is_an_environment_probe(self):
        """The write-baseline-on-first-run convention (``if not BASELINE_PATH.exists(): write(); pytest.skip(...)``)
        used by dozens of meta-gates checks filesystem state, not what the computed data says; it must not be
        flagged as a late-skip like a data-decided one."""
        src = (
            "def test_x():\n"
            "    current = compute()\n"
            "    if refresh_requested() or not BASELINE_PATH.exists():\n"
            "        BASELINE_PATH.write_text(dump(current))\n"
            "        pytest.skip('baseline written')\n"
            "    assert current <= load_baseline()\n"
        )
        assert _reasons(src) == []

    def test_a_reversed_narrow_range_is_not_flagged(self):
        assert _reasons("def test_x():\n    assert 1.1 > ratio > 0.9\n") == []

    @pytest.mark.parametrize(
        "compute",
        ["result: Frame = run()", "if (result := run()) is None:\n        pass", "with run() as result:\n        pass"],
        ids=["annassign", "walrus", "with"],
    )
    def test_annassign_walrus_and_with_count_as_computing(self, compute):
        src = f"def test_x():\n    {compute}\n    if not result:\n        pytest.skip('no data')\n    assert result\n"
        assert _reasons(src) == ["late-skip"]

    def test_from_pytest_import_skip_is_resolved_with_aliases(self):
        module = ast.parse("from pytest import skip\n\ndef test_x():\n    x = run()\n    if not x:\n        skip('empty')\n    assert x\n")
        func = module.body[1]
        assert shape_reasons(func, aliases=ImportAliases.from_tree(module)) == ["late-skip"]
        other = ast.parse("from unittest import skip\n\ndef test_x():\n    x = run()\n    if not x:\n        skip('empty')\n    assert x\n")
        assert shape_reasons(other.body[1], aliases=ImportAliases.from_tree(other)) == []


class TestNonemptyOnlyAssert:
    @pytest.mark.parametrize("cmp", ["len(result) > 0", "len(result) >= 1", "0 < len(result)", "1 <= len(result)"])
    def test_a_sole_nonemptiness_assertion_is_found(self, cmp):
        src = f"def test_dedup_removes_duplicates():\n    result = dedup([1, 1, 2])\n    assert {cmp}\n"
        assert _reasons(src) == ["nonempty-only-assert"]

    def test_a_nonemptiness_assertion_alongside_a_value_assertion_is_not_flagged(self):
        """The nearest correct code: a second assertion on an actual value already gives it a real floor."""
        src = "def test_dedup_removes_duplicates():\n    result = dedup([1, 1, 2])\n    assert len(result) > 0\n    assert result == [1, 2]\n"
        assert _reasons(src) == []

    def test_the_shape_is_off_unless_asked_for(self):
        src = "def test_dedup_removes_duplicates():\n    result = dedup([1, 1, 2])\n    assert len(result) > 0\n"
        assert _reasons(src, extra_shapes=()) == []
        assert _reasons(src) == ["nonempty-only-assert"]

    def test_a_value_assertion_alone_is_not_flagged(self):
        assert _reasons("def test_x():\n    result = dedup([1, 1, 2])\n    assert result == [1, 2]\n") == []
