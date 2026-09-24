"""``order_losing_filters`` flags a mask built from an index that the same function also selects positionally."""

from __future__ import annotations

from py_ci_shared.order_losing_filters import find_order_losing_filters


def _scan(tmp_path, source):
    """Scan ``source`` written to ``tmp_path/m.py``."""
    p = tmp_path / "m.py"
    p.write_text(source, encoding="utf-8")
    return find_order_losing_filters([p], tmp_path)


def test_the_polars_twin_of_an_iloc_branch_is_flagged(tmp_path):
    """The shipped shape: ``.iloc[idx]`` for pandas, a mask from ``idx`` wrapped in a Series for polars."""
    found = _scan(tmp_path,
                  "def rows(frame, idx):\n"
                  "    if hasattr(frame, 'iloc'):\n"
                  "        return frame.iloc[idx]\n"
                  "    mask = np.zeros(frame.height, dtype=bool)\n"
                  "    mask[idx] = True\n"
                  "    return frame.filter(pl.Series(mask))\n")
    assert [(f.scope, f.mask) for f in found] == [("rows", "mask")]


def test_a_membership_mask_and_a_take_are_flagged(tmp_path):
    """``np.isin`` builds the mask and ``.take`` is the positional twin."""
    found = _scan(tmp_path,
                  "def rows(a, frame, idx):\n"
                  "    x = a.take(idx)\n"
                  "    keep = np.isin(np.arange(len(frame)), idx)\n"
                  "    return x, frame[keep]\n")
    assert [f.mask for f in found] == ["keep"]


def test_a_mask_with_no_positional_twin_is_not_flagged(tmp_path):
    """Selecting by a mask alone keeps frame order on purpose; only the pairing with a positional branch is a hazard."""
    assert _scan(tmp_path,
                 "def rows(frame, idx):\n"
                 "    mask = np.zeros(len(frame), bool)\n"
                 "    mask[idx] = True\n"
                 "    return frame.filter(mask)\n") == []


def test_a_mask_from_another_index_is_not_flagged(tmp_path):
    """The mask must come from the same index the positional branch uses."""
    assert _scan(tmp_path,
                 "def rows(frame, idx, other):\n"
                 "    head = frame.iloc[idx]\n"
                 "    mask = np.zeros(len(frame), bool)\n"
                 "    mask[other] = True\n"
                 "    return head, frame[mask]\n") == []
