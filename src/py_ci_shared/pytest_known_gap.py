"""``known_gap(reason, gap_closed)``: record an open limit as an expected failure that turns red the day it closes.

A bare ``pytest.xfail("...")`` in the middle of a test has two faults. It stops the test where it stands, so nothing after
it runs, and when the limit goes away nothing says so: the test keeps reporting "xfailed" while the code underneath has
been fixed for months. ``known_gap`` takes the verdict of the REAL contract assertion, evaluated on the measured value::

    from py_ci_shared.pytest_known_gap import known_gap

    def test_selector_drops_the_redundant_column():
        kept = select(frame)
        known_gap("numpy 2.1 argsort is unstable on ties (numpy#1234)", gap_closed="redundant" not in kept)

While ``gap_closed`` is false the test xfails with *reason*. Once the contract holds the test FAILS with a message naming the
gap, so the entry is removed instead of silently hiding the fix. The reason is a plain argument, which is why
:mod:`py_ci_shared.no_xfail_to_defer` reads calls to this function like any other xfail: the reason must name an external
component or a tracked issue, and a ``gap_closed`` that is the literal ``False`` (a gap that can never close, so it can
never fail) is a finding.
"""

from __future__ import annotations

import pytest

__all__ = ["known_gap"]


def known_gap(reason: str, gap_closed: bool) -> None:
    """Xfail with *reason* while the gap is open; fail loudly once *gap_closed* (the contract assertion's verdict) is true."""
    if gap_closed:
        pytest.fail(f"known gap is closed, remove it from the gap registry: {reason}", pytrace=False)
    pytest.xfail(reason)
