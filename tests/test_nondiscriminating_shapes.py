"""Unit tests for the nondiscriminating assertion-shape rules. Parsed source, no mocking, matching this package's convention."""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.nondiscriminating_shapes import shape_reasons


def _reasons(src: str) -> list[str]:
    fn = ast.parse(textwrap.dedent(src)).body[0]
    return shape_reasons(fn)


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
