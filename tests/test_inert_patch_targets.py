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
