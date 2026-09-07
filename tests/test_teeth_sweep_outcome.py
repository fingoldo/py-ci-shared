"""`read_pytest_outcome` decides every sweep verdict, so it must not miss a killed mutation.

WHAT THIS COST. The sweep reported SURVIVED -- "nothing watches this fix" -- for a mutation that
renamed a function. The rename broke an importer, pytest failed at COLLECTION and printed

    ERROR tests/test_x.py::test_y

and the reader filtered on lines starting with `FAILED`. There were none, so the tool concluded the
fix was unwatched, when it was watched so thoroughly the suite could not load without it. The
verdict was inverted, and inverted in the direction that costs work: it sends someone to write a
regression test that already exists, and it inflates the "unwatched fixes" count an audit publishes.

Both directions are pinned. A filter widened until it matches pytest's banners and log lines would
report every clean run as a kill, which is the same tool broken the other way round.
"""

from __future__ import annotations

from py_ci_shared.teeth_sweep import read_pytest_outcome

_A_FAILURE = """
tests/test_thing.py .F                                                   [100%]
=================================== FAILURES ===================================
FAILED tests/test_thing.py::TestIt::test_the_contract - AssertionError
1 failed, 1 passed in 3.21s
"""

_A_COLLECTION_ERROR = """
==================================== ERRORS ====================================
_______________ ERROR collecting tests/test_thing.py _______________
ImportError: cannot import name 'split_job_pub_response' from 'job_details_response_split'
ERROR tests/test_thing.py::TestIt::test_the_contract
ERROR tests/test_thing.py::TestIt::test_the_other
2 errors in 4.02s
"""

_A_CLEAN_RUN = """
tests/test_thing.py ..                                                   [100%]
2026-09-07 22:00:00 [ERROR] some_module: a logged error that is not a test result
2 passed in 1.10s
"""


class TestItNamesWhatDidNotPass:
    def test_a_failed_test_is_named(self):
        summary, named = read_pytest_outcome(_A_FAILURE)

        assert named == ["FAILED tests/test_thing.py::TestIt::test_the_contract - AssertionError"]
        assert summary == "1 failed, 1 passed in 3.21s"

    def test_a_collection_error_is_named_too(self):
        """THE DEFECT. No FAILED line exists here, and the mutation was very much killed."""
        summary, named = read_pytest_outcome(_A_COLLECTION_ERROR)

        assert len(named) == 2, f"a collection error must count as a kill, got {named}"
        assert all(ln.startswith("ERROR tests/") for ln in named)
        assert summary == "2 errors in 4.02s", "the summary line must be found for an error-only run too"

    def test_a_clean_run_names_nothing(self):
        """The other direction, and it is the same tool broken: a filter widened until it matches
        pytest's `ERRORS` banner or a logged ERROR line reports every clean run as a kill, so every
        mutation looks watched and the sweep stops finding anything."""
        summary, named = read_pytest_outcome(_A_CLEAN_RUN)

        assert named == [], f"a passing run named {named}"
        assert summary == "2 passed in 1.10s"

    def test_the_errors_banner_is_not_a_test_name(self):
        """`ERROR ` keeps its trailing space for this: the section banner is `==== ERRORS ====`."""
        _summary, named = read_pytest_outcome("==================================== ERRORS ====================================\n1 passed in 0.1s\n")

        assert named == []


class TestTheSummaryLine:
    def test_a_run_with_no_recognisable_summary_says_so(self):
        """Silence here would read as "the suite passed". A sweep that cannot tell what happened
        must say that rather than pick a default."""
        summary, named = read_pytest_outcome("the process died before pytest printed anything\n")

        assert summary == "NO RESULT LINE"
        assert named == []
