"""Scope-aware import resolution (ImportAliases.from_tree(scoped=True)) and the gates migrated to it (audit 33-F1)."""

from __future__ import annotations

import ast
from pathlib import Path

from py_ci_shared._core.aliases import ImportAliases
from py_ci_shared.naive_utcnow import find_naive_utcnow
from py_ci_shared.unsafe_deserialization import find_unsafe_deserialization


def _calls(src: str, *, scoped: bool = True) -> dict[int, "str | None"]:
    tree = ast.parse(src)
    res = ImportAliases.from_tree(tree, scoped=scoped)
    return {n.lineno: res.qualified_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}


def test_flat_mode_is_unchanged_a_function_local_import_binds_everywhere():
    src = "import pickle\ndef g():\n    from safe import pickle\ndef f(b):\n    return pickle.loads(b)\n"
    assert _calls(src, scoped=False)[5] == "safe.pickle.loads"


def test_scoped_import_in_another_function_does_not_rebind():
    src = "import pickle\ndef g():\n    from safe import pickle\ndef f(b):\n    return pickle.loads(b)\n"
    assert _calls(src)[5] == "pickle.loads"


def test_a_parameter_shadows_the_import():
    assert _calls("import pickle\ndef f(pickle, b):\n    return pickle.loads(b)\n")[3] is None


def test_a_local_rebind_shadows_the_import_but_the_sibling_still_resolves():
    src = "import pickle\ndef f(b):\n    pickle = safe.L()\n    return pickle.loads(b)\ndef g(b):\n    return pickle.loads(b)\n"
    got = _calls(src)
    assert got[4] is None and got[6] == "pickle.loads"


def test_one_hop_alias_of_a_module_and_of_a_function():
    src = "import pickle\ndef f(b):\n    p = pickle\n    return p.loads(b)\ndef g(b):\n    L = pickle.loads\n    return L(b)\n"
    got = _calls(src)
    assert got[4] == "pickle.loads" and got[7] == "pickle.loads"


def test_a_try_except_import_fallback_does_not_count_as_a_shadow():
    src = "try:\n    import json\nexcept ImportError:\n    json = None\ndef f():\n    return json.loads('1')\n"
    assert _calls(src)[6] == "json.loads"


def test_class_body_names_are_invisible_to_methods_and_builtins_resolve_to_themselves():
    src = "import os\nclass A:\n    os = 1\n    def m(self):\n        return os.getcwd() + str(open('x'))\n"
    tree = ast.parse(src)
    res = ImportAliases.from_tree(tree, scoped=True)
    names = {res.qualified_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert names == {"os.getcwd", "str", "open"}


def _unsafe(tmp_path: Path, src: str) -> list[int]:
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    return [f.line for f in find_unsafe_deserialization(tmp_path, use_git=False)]


def test_unsafe_deserialization_not_silenced_by_a_stray_local_import(tmp_path):
    src = "import pickle\n\n\ndef g():\n    from safe import pickle\n\n\ndef f(b):\n    return pickle.loads(b)\n"
    assert _unsafe(tmp_path, src) == [9]


def test_unsafe_deserialization_ignores_a_parameter_named_like_the_library(tmp_path):
    assert _unsafe(tmp_path, "import pickle\n\n\ndef f(pickle, b):\n    return pickle.loads(b)\n") == []


def test_unsafe_deserialization_follows_a_module_alias_and_a_function_alias(tmp_path):
    src = "import pickle\n\n\ndef f(b):\n    p = pickle\n    return p.loads(b)\n\n\ndef g(b):\n    L = pickle.loads\n    return L(b)\n"
    assert _unsafe(tmp_path, src) == [6, 11]


def test_naive_utcnow_aware_library_exemption_requires_a_real_import(tmp_path):
    (tmp_path / "m.py").write_text("import datetime\n\n\ndef f(arrow=datetime.datetime):\n    return arrow.utcnow()\n", encoding="utf-8")
    assert len(find_naive_utcnow(tmp_path, use_git=False)) == 1
    (tmp_path / "m.py").write_text("import arrow\n\n\ndef f():\n    return arrow.utcnow()\n", encoding="utf-8")
    assert find_naive_utcnow(tmp_path, use_git=False) == []
