"""run_package_doctests: which modules it runs, which it skips, and that it does not import what it cannot test."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.package_doctests import assert_package_doctests_pass, run_package_doctests

_GOOD = '''
def double(x):
    """
    >>> double(2)
    4
    """
    return 2 * x
'''

_BAD = '''
def triple(x):
    """
    >>> triple(2)
    7
    """
    return 3 * x
'''


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A throwaway package on sys.path; modules imported from it are dropped again afterwards."""
    root = tmp_path / "dtpkg"

    def write(rel: str, body: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    write("__init__.py", _GOOD)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield write
    for name in [n for n in sys.modules if n == "dtpkg" or n.startswith("dtpkg.")]:
        del sys.modules[name]


def test_the_package_init_is_tested(tree):
    attempted, failures, _ = run_package_doctests("dtpkg")
    assert attempted == 1 and failures == []


def test_a_failing_example_in_a_submodule_is_reported(tree):
    tree("sub/__init__.py", "")
    tree("sub/mod.py", _BAD)
    _, failures, _ = run_package_doctests("dtpkg")
    assert failures == ["dtpkg.sub.mod: 1 of 1 failed"]


def test_skip_parts_drops_a_nested_segment_a_prefix_cannot_name(tree):
    tree("deep/__init__.py", "")
    tree("deep/_benchmarks/__init__.py", "")
    tree("deep/_benchmarks/bench.py", _BAD)
    _, failures, _ = run_package_doctests("dtpkg", skip_parts=("_benchmarks",))
    assert failures == []


def test_a_module_without_examples_is_not_imported(tree):
    # A script that runs on import: importing it just to find no doctest would execute it.
    tree("script.py", "raise SystemExit('ran on import')\n")
    _, _, unimportable = run_package_doctests("dtpkg")
    assert unimportable == []
    assert "dtpkg.script" not in sys.modules


def test_a_plain_module_is_accepted(tree):
    tree("single.py", _GOOD)
    attempted, failures, _ = run_package_doctests("dtpkg.single")
    assert attempted == 1 and failures == []


def test_an_empty_run_fails(tree):
    tree("sub/__init__.py", "")
    with pytest.raises(pytest.fail.Exception, match="an empty run is not a pass"):
        assert_package_doctests_pass("dtpkg.sub", min_examples=1)
