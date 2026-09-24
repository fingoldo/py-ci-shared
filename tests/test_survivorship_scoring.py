"""``survivorship_scoring`` finds the metrics scored only where the prediction was finite."""

from __future__ import annotations

import pytest

from py_ci_shared.survivorship_scoring import assert_no_survivorship_scoring, find_survivorship_scoring


def _write(tmp_path, source, name="m.py"):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_metric_over_the_finite_mask_is_reported(tmp_path):
    """The shipped shape: the mask comes from the prediction, and both sides are indexed by it."""
    p = _write(
        tmp_path, "import numpy as np\n" "\n" "\n" "def score(y, pred):\n" "    finite = np.isfinite(pred)\n" "    return rmse(y[finite], pred[finite])\n"
    )
    found = find_survivorship_scoring([p], tmp_path)
    assert [(f.function, f.metric, f.mask) for f in found] == [("score", "rmse", "finite")]


def test_a_mask_built_from_several_isfinite_terms_still_counts(tmp_path):
    """``ok = np.isfinite(pred) & np.isfinite(y)`` and a mask derived from it are the same shape."""
    p = _write(
        tmp_path,
        "import numpy as np\n"
        "\n"
        "\n"
        "def score(y, pred):\n"
        "    ok = np.isfinite(pred) & np.isfinite(y)\n"
        "    use = ok\n"
        "    return mean_squared_error(y[use], pred[use])\n",
    )
    assert [f.mask for f in find_survivorship_scoring([p], tmp_path)] == ["use"]


def test_scoring_the_whole_population_is_not_reported(tmp_path):
    """The fix itself: the rows are filled and every row is scored."""
    p = _write(
        tmp_path,
        "import numpy as np\n"
        "\n"
        "\n"
        "def score(y, pred, y_fit):\n"
        "    finite = np.isfinite(pred)\n"
        "    filled = median_filled(pred, y_fit)\n"
        "    return rmse(y, filled), int(finite.sum())\n",
    )
    assert find_survivorship_scoring([p], tmp_path) == []


def test_reporting_the_dropped_fraction_is_a_remedy(tmp_path):
    """A diagnostic that scores survivors on purpose passes when it says how many rows it dropped."""
    p = _write(
        tmp_path,
        "import numpy as np\n"
        "\n"
        "\n"
        "def score(y, pred):\n"
        "    finite = np.isfinite(pred)\n"
        "    dropped_frac = 1.0 - finite.mean()\n"
        "    return rmse(y[finite], pred[finite]), dropped_frac\n",
    )
    assert find_survivorship_scoring([p], tmp_path) == []


def test_counting_the_finite_rows_for_a_floor_is_not_a_remedy(tmp_path):
    """Rejecting below a finite-row floor leaves the survivors scored alone, which is the defect."""
    p = _write(
        tmp_path,
        "import numpy as np\n"
        "\n"
        "\n"
        "def score(y, pred):\n"
        "    finite = np.isfinite(pred)\n"
        "    n_finite = int(finite.sum())\n"
        "    if n_finite < 50:\n"
        "        return float('inf')\n"
        "    return rmse(y[finite], pred[finite])\n",
    )
    assert len(find_survivorship_scoring([p], tmp_path)) == 1


def test_an_unrelated_mask_or_a_single_masked_side_is_left_alone(tmp_path):
    """A mask that is not about finiteness, and a call with only one side masked, are not this defect."""
    p = _write(
        tmp_path,
        "import numpy as np\n"
        "\n"
        "\n"
        "def score(y, pred, group):\n"
        "    sel = group == 1\n"
        "    finite = np.isfinite(pred)\n"
        "    return rmse(y[sel], pred[sel]), rmse(y, pred[finite])\n",
    )
    assert find_survivorship_scoring([p], tmp_path) == []


def test_the_assert_names_the_site_and_honours_the_allowlist(tmp_path):
    """The failure names path, function and metric; an allowed site passes with its reason."""
    p = _write(
        tmp_path, "import numpy as np\n" "\n" "\n" "def score(y, pred):\n" "    finite = np.isfinite(pred)\n" "    return rmse(y[finite], pred[finite])\n"
    )
    with pytest.raises(AssertionError, match="score"):
        assert_no_survivorship_scoring([p], tmp_path)
    assert_no_survivorship_scoring([p], tmp_path, allowed={"m.py::score": "a coverage diagnostic, not a verdict"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_no_survivorship_scoring([p], tmp_path, allowed={"m.py::score": ""})


def test_a_stale_allowlist_entry_and_an_empty_scan_are_rejected(tmp_path):
    """An allowed site that no longer scores survivors, and a scan that lost its files, both fail."""
    p = _write(tmp_path, "def score(y, pred):\n    return rmse(y, pred)\n")
    with pytest.raises(AssertionError, match="no longer score survivors"):
        assert_no_survivorship_scoring([p], tmp_path, allowed={"m.py::score": "was masked once"})
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_no_survivorship_scoring([], tmp_path, min_files=1)


def test_an_unparsable_file_fails_and_the_floor_counts_parsed_files(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"bad\.py"):
        find_survivorship_scoring([bad], tmp_path)
    assert find_survivorship_scoring([bad], tmp_path, allow_unparsed=True) == []
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_no_survivorship_scoring([bad], tmp_path, min_files=1)
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    assert_no_survivorship_scoring([good], tmp_path, min_files=1)


def test_a_bom_file_outside_the_root_is_scanned(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    src = tmp_path / "x.py"
    body = "import numpy as np\ndef score(y, p):\n    m = np.isfinite(p)\n    return mean_squared_error(y[m], p[m])\n"
    src.write_bytes(b"\xef\xbb\xbf" + body.encode())
    found = find_survivorship_scoring([src], root)
    assert [(s.function, s.lineno) for s in found] == [("score", 4)] and found[0].path.endswith("/x.py")
