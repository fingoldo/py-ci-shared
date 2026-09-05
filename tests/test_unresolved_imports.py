"""The unresolved-import scanner must catch the real shapes and stay quiet on the legitimate ones."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.unresolved_imports import ModuleIndex, find_unresolved_from_imports


def _pkg(root: Path, name: str, files: dict[str, str]) -> Path:
    """Write a package `name` under `root` from {relative path: source}."""
    base = root / name
    for rel, src in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
    return root


def _scan(root: Path) -> list[str]:
    """Run the scanner over a synthetic tree rooted at `root`."""
    index = ModuleIndex([root], [root])
    return find_unresolved_from_imports([root], index, resolvable_prefixes=("pkg",))


def test_a_removed_name_at_module_scope_is_caught(tmp_path):
    """The 39-shard shape: a module-scope import of a name a refactor deleted."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "helpers.py": "def kept():\n    return 1\n",
            "consumer.py": "from pkg.helpers import removed\n",
        },
    )
    problems = _scan(root)
    assert any("does not define 'removed'" in p and "module scope" in p for p in problems), problems


def test_a_removed_name_inside_a_function_is_caught_and_labelled(tmp_path):
    """The broken-default shape: the import waits for a branch, so import-time checks miss it."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "helpers.py": "def kept():\n    return 1\n",
            "consumer.py": "def fit(x=None):\n    if x is None:\n        from pkg.helpers import removed\n\n        return removed()\n    return x\n",
        },
    )
    problems = _scan(root)
    assert any("does not define 'removed'" in p and "inside a function" in p for p in problems), problems


def test_a_missing_module_is_caught(tmp_path):
    """`from pkg.nonexistent import X` -- the module itself is gone, not just the name."""
    root = _pkg(tmp_path, "pkg", {"__init__.py": "", "consumer.py": "from pkg.nonexistent import thing\n"})
    assert any("does not exist" in p for p in _scan(root)), _scan(root)


def test_a_relative_import_from_a_plain_module_resolves_to_its_package(tmp_path):
    """`from .sibling import X` inside pkg/mod.py means pkg.sibling, not pkg.mod.sibling.

    Getting this wrong reports every relative import in a tree as a missing module -- 3932 of them on the
    repository this was built against, which is indistinguishable from the scanner being useless.
    """
    root = _pkg(
        tmp_path,
        "pkg",
        {"__init__.py": "", "sibling.py": "def thing():\n    return 1\n", "mod.py": "from .sibling import thing\n"},
    )
    assert _scan(root) == []


def test_a_submodule_counts_as_a_name_of_its_package(tmp_path):
    """`from pkg.sub import leaf` is valid when pkg/sub/leaf.py exists, though __init__ binds no `leaf`."""
    root = _pkg(
        tmp_path,
        "pkg",
        {"__init__.py": "", "sub/__init__.py": "", "sub/leaf.py": "VALUE = 1\n", "consumer.py": "from pkg.sub import leaf\n"},
    )
    assert _scan(root) == []


def test_tuple_unpacking_binds_every_name(tmp_path):
    """`a, b = factory()` publishes both names; reading only bare-Name targets reports them undefined."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "registry.py": "def _factory():\n    return (1, 2)\n\n\n_alpha, _beta = _factory()\n",
            "consumer.py": "from pkg.registry import _alpha, _beta\n",
        },
    )
    assert _scan(root) == []


def test_a_dynamic_facade_is_not_judged(tmp_path):
    """A module that publishes names through `globals()[...]` cannot be resolved by parsing it."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "facade.py": "import sys\n\nfor _n in ('a', 'b'):\n    globals()[_n] = _n\n",
            "consumer.py": "from pkg.facade import a\n",
        },
    )
    assert _scan(root) == []


def test_a_conditional_definition_binds_its_name(tmp_path):
    """A version-guarded or ImportError-fallback definition is still a definition."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "compat.py": "import sys\n\nif sys.version_info >= (3, 11):\n    def thing():\n        return 1\nelse:\n    def thing():\n        return 2\n",
            "consumer.py": "from pkg.compat import thing\n",
        },
    )
    assert _scan(root) == []


def test_a_third_party_target_is_not_judged(tmp_path):
    """Only prefixes the caller declares resolvable are checked; absence elsewhere proves nothing."""
    root = _pkg(tmp_path, "pkg", {"__init__.py": "", "consumer.py": "from numpy.linalg import definitely_not_a_real_name\n"})
    assert _scan(root) == []


def test_an_implicit_namespace_package_is_importable(tmp_path):
    """A directory of modules with no __init__.py is importable since 3.3; absent is not missing."""
    root = _pkg(
        tmp_path,
        "pkg",
        {"__init__.py": "", "ns/leaf.py": "VALUE = 1\n", "consumer.py": "from pkg.ns import leaf\n"},
    )
    assert _scan(root) == []


def test_an_import_with_an_ImportError_fallback_is_not_a_finding(tmp_path):
    """An optional dependency or optional baseline snapshot handles its own absence."""
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "consumer.py": "try:\n    from pkg.absent import thing\nexcept ImportError:\n    thing = None\n",
        },
    )
    assert _scan(root) == []


def test_an_import_asserted_to_fail_is_not_a_finding(tmp_path):
    """`with pytest.raises(ImportError): from x import y` -- the absence IS the contract being pinned.

    Reporting this one would be actively wrong: the scanner would be demanding the repo break its own test.
    """
    root = _pkg(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "consumer.py": "import pytest\n\n\ndef test_gone():\n    with pytest.raises(ImportError):\n        from pkg.absent import thing\n",
        },
    )
    assert _scan(root) == []
