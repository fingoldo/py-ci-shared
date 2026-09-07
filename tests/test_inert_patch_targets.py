"""`inert_patch_targets.scan` catches the reset that invents an attribute, and stays quiet otherwise.

Each positive case is a real shape found in a production tree; each negative case is a shape an
earlier, greedier version of this check reported, and every one of those would have been a reason to
switch the check off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.inert_patch_targets import module_index, scan


@pytest.fixture
def project(tmp_path):
    """A tiny package: `pkg/_owner.py` holds the state, `pkg/_facade.py` re-exports the function."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_owner.py").write_text("_SINGLETON = None\ncounter = 0\n\n\ndef reset():\n    pass\n", encoding="utf-8")
    (pkg / "_facade.py").write_text("from pkg._owner import reset\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    return tmp_path, pkg, tests


def _scan(tmp_path, tests, body: str):
    path = tests / "test_probe.py"
    path.write_text(body, encoding="utf-8")
    return scan([path], module_index([tmp_path], package_root=tmp_path))


class TestTheDefect:
    def test_resetting_a_name_the_module_does_not_have_is_caught(self, project):
        tmp_path, _pkg, tests = project
        findings = _scan(tmp_path, tests, "import pkg._facade as f\n\n\ndef test_x():\n    f._SINGLETON = None\n")

        assert len(findings) == 1
        assert findings[0].target == "pkg._facade._SINGLETON"

    def test_the_message_names_the_module_that_owns_it(self, project):
        """Without this the reader has to go looking; with it the fix is the next keystroke."""
        tmp_path, _pkg, tests = project
        findings = _scan(tmp_path, tests, "import pkg._facade as f\n\n\ndef test_x():\n    f.counter = 0\n")

        assert "pkg._owner" in findings[0].detail


class TestWhatMustStayQuiet:
    def test_setting_a_name_the_module_really_has(self, project):
        tmp_path, _pkg, tests = project
        assert not _scan(tmp_path, tests, "import pkg._owner as o\n\n\ndef test_x():\n    o.counter = 5\n")

    def test_setting_a_name_the_module_only_imported(self, project):
        """Stubbing a module's own imported dependency is the ordinary idiom, not a defect."""
        tmp_path, _pkg, tests = project
        assert not _scan(tmp_path, tests, "import pkg._facade as f\n\n\ndef test_x():\n    f.reset = lambda: None\n")

    def test_a_module_that_forwards_at_run_time_is_skipped(self, project):
        """A `__setattr__` proxy or a `globals()` re-export loop decides its attributes at run time.
        The source cannot answer, and it is where the author is most likely doing this on purpose --
        an early version reported twelve findings against exactly such a package."""
        tmp_path, pkg, tests = project
        (pkg / "_proxy.py").write_text(
            "import pkg._owner as _o\n\n\ndef __getattr__(name):\n    return getattr(_o, name)\n", encoding="utf-8"
        )
        assert not _scan(tmp_path, tests, "import pkg._proxy as p\n\n\ndef test_x():\n    p._SINGLETON = None\n")

    def test_an_alias_rebound_in_another_function_is_not_that_module(self, project):
        """`import pkg._facade as f` in one test and `f = SomeObject()` in another: a file-wide alias
        map reads the second one's attribute assignment as a module reset. Found the hard way."""
        tmp_path, _pkg, tests = project
        body = (
            "import pkg._owner\n\n\n"
            "def test_a():\n    import pkg._facade as f\n    assert f is not None\n\n\n"
            "def test_b():\n    f = object.__new__(type('T', (), {}))\n    f._SINGLETON = None\n"
        )
        assert not _scan(tmp_path, tests, body)

    def test_a_third_party_module_is_never_reported(self, project):
        tmp_path, _pkg, tests = project
        assert not _scan(tmp_path, tests, "import logging\n\n\ndef test_x():\n    logging.SOMETHING = 1\n")


class TestATupleUnpackingBindsEveryNameInIt:
    """`a, b, c = None, None, None` binds three module attributes, not zero.

    Collecting only bare `Name` targets bound none of them, so a test patching any of the four
    names on pyutilz's `system.distributed` was reported as inventing an attribute the module
    very much has -- five findings, all of them this. A false report here is expensive: it sends
    the reader to "fix" a patch that was correct, and the obvious fix is to delete it.
    """

    @staticmethod
    def _with_state(tmp_path, pkg, tests, module_body, test_body):
        """Write a state module and a probe test, then scan."""
        (pkg / "_state.py").write_text(module_body, encoding="utf-8")
        return _scan(tmp_path, tests, test_body)

    def test_a_name_bound_by_tuple_unpacking_is_not_reported(self, project):
        tmp_path, pkg, tests = project

        found = self._with_state(
            tmp_path, pkg, tests,
            'm_app, m_ip = None, None\n',
            'import pkg._state as state\n\n\ndef test_it():\n    state.m_ip = 0\n',
        )

        assert found == [], [f.target for f in found]

    def test_every_name_in_the_tuple_counts_not_just_the_first(self, project):
        tmp_path, pkg, tests = project

        found = self._with_state(
            tmp_path, pkg, tests,
            'a, b, c, d = 1, 2, 3, 4\n',
            'import pkg._state as state\n\n\ndef test_it():\n    state.a = 9\n    state.b = 9\n    state.c = 9\n    state.d = 9\n',
        )

        assert found == [], [f.target for f in found]

    def test_a_starred_target_unpacks_the_same_way(self, project):
        tmp_path, pkg, tests = project

        found = self._with_state(
            tmp_path, pkg, tests,
            'first, *rest = 1, 2, 3\n',
            'import pkg._state as state\n\n\ndef test_it():\n    state.first = 9\n    state.rest = []\n',
        )

        assert found == [], [f.target for f in found]

    def test_a_name_that_really_is_absent_is_still_reported(self, project):
        """The narrowing must not swallow the thing the check is for."""
        tmp_path, pkg, tests = project

        found = self._with_state(
            tmp_path, pkg, tests,
            'a, b = 1, 2\n',
            'import pkg._state as state\n\n\ndef test_it():\n    state.never_defined = 9\n',
        )

        assert [f.target for f in found] == ["pkg._state.never_defined"]


class TestAPresenceGuardedAssignmentInventsNothing:
    """The careful shape -- a sentinel, a guarded set, a guarded restore -- was being reported.

    That is the pattern this check WANTS: the assignment runs only when the module really has the
    attribute, so it cannot create one. Reporting it says a correct test is inventing state, and
    the obvious response to the finding is to delete the guard. Found on two repositories.

    The guard usually sits in a `finally` several scopes below the `getattr` that produced the
    sentinel, which is why the sentinel is resolved across the whole file rather than the
    current block.
    """

    @staticmethod
    def _run(tmp_path, pkg, tests, test_body):
        """Write a state module with no `absent` attribute, then scan *test_body*."""
        (pkg / "_state.py").write_text('_MTIME = 0\n', encoding="utf-8")
        return _scan(tmp_path, tests, test_body)

    def test_a_sentinel_guarded_set_and_restore_is_not_reported(self, project):
        tmp_path, pkg, tests = project

        found = self._run(tmp_path, pkg, tests, "import pkg._state as state\n\n\n_SENTINEL = object()\n\n\ndef test_it():\n    saved = getattr(state, 'absent', _SENTINEL)\n    if saved is not _SENTINEL:\n        state.absent = 0\n    try:\n        pass\n    finally:\n        if saved is not _SENTINEL:\n            state.absent = saved\n")

        assert found == [], [f.target for f in found]

    def test_a_hasattr_guard_is_recognised_too(self, project):
        tmp_path, pkg, tests = project

        found = self._run(tmp_path, pkg, tests, "import pkg._state as state\n\n\ndef test_it():\n    if hasattr(state, 'absent'):\n        state.absent = 1\n")

        assert found == [], [f.target for f in found]

    def test_an_unguarded_restore_is_still_reported(self, project):
        """The actual defect: `getattr(mod, NAME, 0)` then an unconditional set, which invents
        the attribute and leaves it behind for the process."""
        tmp_path, pkg, tests = project

        found = self._run(tmp_path, pkg, tests, "import pkg._state as state\n\n\ndef test_it():\n    saved = getattr(state, 'absent', 0)\n    state.absent = saved\n")

        assert [f.target for f in found] == ["pkg._state.absent"]
