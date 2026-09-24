"""`_core.aliases`: a call is matched by what it refers to, not by how it was spelled."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared._core import ImportAliases, module_of, package_of, resolve_relative


def _last_expr(src: str, package=None) -> "str | None":
    tree = ast.parse(src)
    node = tree.body[-1]
    assert isinstance(node, ast.Expr)
    return ImportAliases.from_tree(tree, package=package).qualified_name(node.value)


@pytest.mark.parametrize(
    "src, expected",
    [
        ("import os\nos.environ.get", "os.environ.get"),
        ("import os as o\no.environ.get('X')", "os.environ.get"),
        ("from os import environ\nenviron.get", "os.environ.get"),
        ("from os import environ as E\nE.get", "os.environ.get"),
        ("import importlib as il\nil.reload(m)", "importlib.reload"),
        ("from importlib import reload\nreload(m)", "importlib.reload"),
        ("from unittest import mock\nmock.patch('x')", "unittest.mock.patch"),
        ("from unittest.mock import patch as p\np('x')", "unittest.mock.patch"),
        ("import unittest.mock\nunittest.mock.patch", "unittest.mock.patch"),
        ("import unittest.mock as um\num.patch.object", "unittest.mock.patch.object"),
        ("from inspect import getsource\ngetsource(f)", "inspect.getsource"),
        ("import inspect as ins\nins.getsource", "inspect.getsource"),
    ],
)
def test_absolute_imports_resolve(src, expected):
    assert _last_expr(src) == expected


@pytest.mark.parametrize(
    "src, expected",
    [
        ("print", "print"),  # unbound names resolve to themselves
        ("getsource(f)", "getsource"),  # NOT inspect.getsource: nothing imported it
        ("x.reload", "x.reload"),
    ],
)
def test_unimported_names_are_not_invented(src, expected):
    assert _last_expr(src) == expected


@pytest.mark.parametrize("src", ["f()()", "a[0].b", "(x or y).z", "'s'.join"])
def test_non_name_chains_are_none(src):
    assert _last_expr(src) is None


@pytest.mark.parametrize(
    "src, package, expected",
    [
        ("from . import helpers\nhelpers.run", "pkg.sub", "pkg.sub.helpers.run"),
        ("from .helpers import run as r\nr", "pkg.sub", "pkg.sub.helpers.run"),
        ("from .. import _core\n_core.x", "pkg.sub", "pkg._core.x"),
        ("from ..a.b import c\nc", "pkg.sub.deep", "pkg.sub.a.b.c"),
    ],
)
def test_relative_imports_resolve_against_the_package(src, package, expected):
    assert _last_expr(src, package) == expected


def test_a_relative_import_without_a_package_keeps_its_dots():
    assert _last_expr("from .helpers import run\nrun") == ".helpers.run"
    assert _last_expr("from . import x\nx") == ".x"


def test_resolve_relative_edges():
    assert resolve_relative("a", 0, None) == "a"
    assert resolve_relative("a", 1, "pkg") == "pkg.a"
    assert resolve_relative(None, 2, "pkg.sub") == "pkg"
    assert resolve_relative("a", 3, "pkg.sub") is None  # beyond the top-level package
    assert resolve_relative("a", 1, None) is None


def test_package_and_module_of(tmp_path):
    src = tmp_path / "src" / "pkg"
    assert package_of(src / "a" / "b.py", src, "pkg") == "pkg.a"
    assert package_of(src / "a" / "__init__.py", src, "pkg") == "pkg.a"
    assert package_of(src / "top.py", src, "pkg") == "pkg"
    assert module_of(src / "a" / "b.py", src, "pkg") == "pkg.a.b"
    assert module_of(src / "a" / "__init__.py", src, "pkg") == "pkg.a"


def test_star_imports_bind_nothing():
    aliases = ImportAliases.from_tree(ast.parse("from os import *\n"))
    assert "*" not in aliases and aliases.mapping == {}
