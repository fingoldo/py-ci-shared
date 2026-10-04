"""Tests for py_ci_shared.pytest_known_gap."""

from __future__ import annotations

import pytest

from py_ci_shared.pytest_known_gap import known_gap

REASON = "numpy 2.1 argsort is unstable on ties (numpy#1234)"


def test_an_open_gap_xfails_with_the_reason():
    with pytest.raises(pytest.xfail.Exception) as raised:
        known_gap(REASON, gap_closed=False)
    assert raised.type is pytest.xfail.Exception
    assert str(raised.value) == REASON


def test_a_closed_gap_fails_the_test_and_names_the_reason():
    with pytest.raises(pytest.fail.Exception) as raised:
        known_gap(REASON, gap_closed=True)
    assert raised.type is pytest.fail.Exception, "a closed gap must FAIL, not xfail: that is the whole point of the helper"
    assert REASON in str(raised.value)
    assert "closed" in str(raised.value)


def test_both_spellings_of_the_second_argument_work():
    """``known_gap(reason, gap_closed=...)`` is the form mlframe wrote; the positional form is the same call."""
    with pytest.raises(pytest.fail.Exception):
        known_gap(REASON, True)
    with pytest.raises(pytest.xfail.Exception):
        known_gap(REASON, False)


@pytest.mark.parametrize("verdict", [1, "yes", [0]])
def test_a_truthy_verdict_closes_the_gap(verdict):
    with pytest.raises(pytest.fail.Exception) as raised:
        known_gap(REASON, gap_closed=verdict)  # type: ignore[arg-type]
    assert raised.type is pytest.fail.Exception


@pytest.mark.parametrize("verdict", [0, "", None, []])
def test_a_falsy_verdict_keeps_the_gap_open(verdict):
    with pytest.raises(pytest.xfail.Exception) as raised:
        known_gap(REASON, gap_closed=verdict)  # type: ignore[arg-type]
    assert raised.type is pytest.xfail.Exception


def test_the_second_argument_is_required():
    with pytest.raises(TypeError):
        known_gap(REASON)  # type: ignore[call-arg]


def test_nothing_after_a_closed_gap_runs():
    """The helper stops the test where it stands, in both states: code after it never executes."""
    reached: list[str] = []
    for verdict in (True, False):
        with pytest.raises((pytest.fail.Exception, pytest.xfail.Exception)):
            known_gap(REASON, gap_closed=verdict)
            reached.append(f"after {verdict}")
    assert reached == []
