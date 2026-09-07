"""The pyproject-reading checkers must work on the interpreters this package claims to support.

``tomllib`` is stdlib only from 3.11, and ``requires-python`` here is ``>=3.9``. Five checkers read a
``pyproject.toml``, so on 3.9 and 3.10 every one of them raised ``ModuleNotFoundError: No module named
'tomllib'``. A consumer whose CI matrix includes those versions got a red shard that said nothing about
the code being checked -- observed on mlframe's 3.10 shard, where the entry-point gate failed this way.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_the_compat_module_exposes_a_working_parser():
    """Whichever parser is used, it has to parse."""
    from py_ci_shared._toml_compat import tomllib

    assert tomllib.loads('[project]\nname = "x"\n')["project"]["name"] == "x"


def test_the_fallback_is_used_when_tomllib_is_absent(monkeypatch):
    """The 3.9 / 3.10 path: the branch that never runs on a modern interpreter.

    Simulated rather than skipped, so the fallback is exercised on every version this suite runs on --
    a fallback that only executes on interpreters nobody tests locally is how this bug survived.
    """
    pytest.importorskip("tomli")
    real_import = builtins.__import__

    def _no_tomllib(name, *args, **kwargs):
        """Raise for tomllib only, exactly as a 3.10 interpreter would."""
        if name == "tomllib":
            raise ModuleNotFoundError("No module named 'tomllib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_tomllib)
    monkeypatch.delitem(sys.modules, "py_ci_shared._toml_compat", raising=False)
    monkeypatch.delitem(sys.modules, "tomllib", raising=False)

    module = importlib.import_module("py_ci_shared._toml_compat")
    assert module.tomllib.__name__ == "tomli", "the fallback did not resolve to tomli"
    assert module.tomllib.loads('a = 1\n') == {"a": 1}


@pytest.mark.parametrize(
    "module_name",
    ["config_drift_check", "docs_inventory_parity", "entry_points_resolvable", "gate_integrity"],
)
def test_no_checker_imports_tomllib_directly(module_name):
    """A new direct import would reintroduce the failure on exactly the versions nobody runs locally."""
    from pathlib import Path

    import py_ci_shared

    source = (Path(py_ci_shared.__file__).parent / f"{module_name}.py").read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in source.splitlines() if ln.strip() == "import tomllib"]
    assert not offenders, f"{module_name} imports tomllib directly; use ``from ._toml_compat import tomllib``"
