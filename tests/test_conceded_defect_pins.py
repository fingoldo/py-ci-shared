"""``conceded_defect_pins`` lists tests that concede a defect and pin it exactly, and leaves honest ones alone."""

from __future__ import annotations

from py_ci_shared.conceded_defect_pins import find_conceded_defect_pins


def _write(tmp_path, source, name="test_m.py"):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_conceded_behaviour_pinned_by_equality_is_listed(tmp_path):
    """The shipped shape: the docstring admits the result is wrong, the body asserts that exact result."""
    p = _write(
        tmp_path, "def test_dropout_mean():\n" '    """Dropout does not perfectly reconstruct y; pins the current value."""\n' "    assert compute() == 3.7\n"
    )
    [pin] = find_conceded_defect_pins([p], tmp_path)
    assert (pin.function, pin.phrase.lower()) == ("test_dropout_mean", "does not perfectly")


def test_a_concession_in_the_comment_above_counts_too(tmp_path):
    """Concessions live in a leading comment as often as in the docstring."""
    p = _write(tmp_path, "# the transform degenerates on this input\n" "def test_degenerate():\n" "    np.testing.assert_array_equal(run(), [0, 0, 0])\n")
    assert [c.function for c in find_conceded_defect_pins([p], tmp_path)] == ["test_degenerate"]


def test_a_bounded_assertion_is_not_a_pin(tmp_path):
    """Explaining why a value is lossy while asserting it stays within a bound is the honest use of the same words."""
    p = _write(
        tmp_path,
        "def test_lossy_but_bounded():\n"
        '    """Compression is lossy by design; the error stays small."""\n'
        "    assert error() < 0.01\n"
        "    np.testing.assert_allclose(out(), ref(), rtol=1e-6)\n",
    )
    assert find_conceded_defect_pins([p], tmp_path) == []


def test_an_exact_allclose_is_a_pin(tmp_path):
    """``assert_allclose`` with both tolerances at zero is exact equality under another name."""
    p = _write(tmp_path, "def test_noop():\n" '    """The shrink step is a no-op here."""\n' "    np.testing.assert_allclose(run(), ref(), rtol=0, atol=0)\n")
    assert len(find_conceded_defect_pins([p], tmp_path)) == 1


def test_the_known_defect_convention_is_accepted(tmp_path):
    """A test named ``test_known_defect_<id>_...`` pins a tracked defect on purpose and is not listed."""
    p = _write(
        tmp_path,
        "def test_known_defect_est03_dropout_mean():\n"
        '    """Known defect: does not perfectly reconstruct y until EST-03 lands."""\n'
        "    assert compute() == 3.7\n",
    )
    assert find_conceded_defect_pins([p], tmp_path) == []


def test_a_test_with_no_concession_is_not_listed(tmp_path):
    """Exact equality alone is ordinary; it takes the conceding words to make it a pin of a defect."""
    p = _write(tmp_path, "def test_plain():\n    '''Round-trips.'''\n    assert f(1) == 1\n")
    assert find_conceded_defect_pins([p], tmp_path) == []


def test_a_bom_file_is_read_and_an_unparsable_one_fails(tmp_path):
    import pytest

    src = "def test_dropout_mean():\n    '''The mean is lossy by design.'''\n    assert f() == 0.5\n"
    bom = tmp_path / "test_bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + src.encode())
    assert [c.function for c in find_conceded_defect_pins([bom], tmp_path)] == ["test_dropout_mean"]
    bad = _write(tmp_path, "def test_x(:\n", name="test_bad.py")
    with pytest.raises(AssertionError, match=r"test_bad\.py"):
        find_conceded_defect_pins([bom, bad], tmp_path)
