"""``order_losing_filters`` flags a mask built from an index that the same function also selects positionally."""

from __future__ import annotations

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.order_losing_filters import assert_no_order_losing_filters, find_order_losing_filters


def _scan(tmp_path, source):
    """Scan ``source`` written to ``tmp_path/m.py``."""
    p = tmp_path / "m.py"
    p.write_text(source, encoding="utf-8")
    return find_order_losing_filters([p], tmp_path)


def test_the_polars_twin_of_an_iloc_branch_is_flagged(tmp_path):
    """The shipped shape: ``.iloc[idx]`` for pandas, a mask from ``idx`` wrapped in a Series for polars."""
    found = _scan(
        tmp_path,
        "def rows(frame, idx):\n"
        "    if hasattr(frame, 'iloc'):\n"
        "        return frame.iloc[idx]\n"
        "    mask = np.zeros(frame.height, dtype=bool)\n"
        "    mask[idx] = True\n"
        "    return frame.filter(pl.Series(mask))\n",
    )
    assert [(f.scope, f.mask) for f in found] == [("rows", "mask")]


def test_a_membership_mask_and_a_take_are_flagged(tmp_path):
    """``np.isin`` builds the mask and ``.take`` is the positional twin."""
    found = _scan(
        tmp_path, "def rows(a, frame, idx):\n" "    x = a.take(idx)\n" "    keep = np.isin(np.arange(len(frame)), idx)\n" "    return x, frame[keep]\n"
    )
    assert [f.mask for f in found] == ["keep"]


def test_a_mask_with_no_positional_twin_is_not_flagged(tmp_path):
    """Selecting by a mask alone keeps frame order on purpose; only the pairing with a positional branch is a hazard."""
    assert _scan(tmp_path, "def rows(frame, idx):\n" "    mask = np.zeros(len(frame), bool)\n" "    mask[idx] = True\n" "    return frame.filter(mask)\n") == []


def test_a_mask_from_another_index_is_not_flagged(tmp_path):
    """The mask must come from the same index the positional branch uses."""
    assert (
        _scan(
            tmp_path,
            "def rows(frame, idx, other):\n"
            "    head = frame.iloc[idx]\n"
            "    mask = np.zeros(len(frame), bool)\n"
            "    mask[other] = True\n"
            "    return head, frame[mask]\n",
        )
        == []
    )


_SHUFFLED = (
    "def rows(frame, idx):\n"
    "    if hasattr(frame, 'iloc'):\n"
    "        return frame.iloc[idx]\n"
    "    mask = np.zeros(frame.height, dtype=bool)\n"
    "    mask[idx] = True\n"
    "    return frame.filter(mask)\n"
)


def test_a_bom_file_is_scanned_like_a_plain_one(tmp_path):
    """A leading BOM must not hide the filter inside the file."""
    plain = _scan(tmp_path, _SHUFFLED)
    bom = tmp_path / "b.py"
    bom.write_bytes(b"\xef\xbb\xbf" + _SHUFFLED.encode("utf-8"))
    found = find_order_losing_filters([bom], tmp_path)
    assert [(f.scope, f.mask) for f in found] == [(f.scope, f.mask) for f in plain] == [("rows", "mask")]


def test_an_unparsable_file_is_reported_not_skipped(tmp_path):
    good = tmp_path / "good.py"
    good.write_text(_SHUFFLED, encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"bad\.py"):
        find_order_losing_filters([good, bad], tmp_path)
    assert len(find_order_losing_filters([good, bad], tmp_path, allow_unparsed=True)) == 1
    assert len(find_order_losing_filters([good], tmp_path)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_order_losing_filters([], tmp_path)
    assert find_order_losing_filters([], tmp_path, min_files=0) == []


def test_assert_fails_on_unlisted_stale_and_reasonless_entries(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(_SHUFFLED, encoding="utf-8")
    key = find_order_losing_filters([src], tmp_path)[0].key
    assert_no_order_losing_filters([src], tmp_path, {key: "idx is sorted by construction"})
    with pytest.raises(AssertionError, match="rows"):
        assert_no_order_losing_filters([src], tmp_path)
    with pytest.raises(AssertionError, match=r"stale entry gone\.py"):
        assert_no_order_losing_filters([src], tmp_path, {key: "r", "gone.py::f::m": "r"})
    with pytest.raises(AssertionError, match="gives no reason"):
        assert_no_order_losing_filters([src], tmp_path, {key: " "})
