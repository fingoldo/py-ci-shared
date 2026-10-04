"""Tests for py_ci_shared.optional_imports_guarded."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared._core import Baseline, CorpusError, EmptyScanError, UnparsedFilesError
from py_ci_shared import optional_imports_guarded as gate

BOM = b"\xef\xbb\xbf"
# a tmp_path is no git checkout; asking git about it costs seconds per call on Windows
find_unguarded_optional_imports = functools.partial(gate.find_unguarded_optional_imports, use_git=False)
assert_optional_imports_guarded = functools.partial(gate.assert_optional_imports_guarded, use_git=False)
PYPROJECT = """[project]
name = "mypkg"
version = "0.1.0"
dependencies = ["numpy>=1.20", "pyyaml; python_version >= '3.8'", "scikit-learn"]

[project.optional-dependencies]
db = ["zstandard"]
boosting = ["catboost", "lightgbm"]
all = ["mypkg[db,boosting]", "properscoring"]
"""


def _project(tmp_path: Path, source: str, *, pyproject: str = PYPROJECT, name: str = "mod.py", raw: Optional[bytes] = None) -> Path:
    package = tmp_path / "src" / "mypkg"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / name).write_bytes(raw if raw is not None else source.encode("utf-8"))
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return tmp_path


def _found(tmp_path: Path, source: str, **kwargs) -> list:
    return find_unguarded_optional_imports(_project(tmp_path, source), **kwargs)


def test_reports_the_seeded_violation_with_the_extra_that_holds_it(tmp_path):
    (finding,) = _found(tmp_path, "import numpy\nimport zstandard\n")
    assert (finding.path, finding.line, finding.rule) == ("src/mypkg/mod.py", 2, "optional-import-unguarded")
    assert "'zstandard'" in finding.message and "only in the 'all', 'db' extra" in finding.message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    source = "import numpy\ntry:\n    import zstandard\nexcept ImportError:\n    zstandard = None\n\n\ndef f():\n    import catboost\n"
    assert _found(tmp_path, source) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _found(tmp_path / "a", "import zstandard\n")
    root = _project(tmp_path / "b", "", raw=BOM + b"import zstandard\n")
    bom = find_unguarded_optional_imports(root)
    assert [(f.line, f.message) for f in bom] == [(f.line, f.message) for f in plain] and len(plain) == 1


def test_an_unparsable_module_fails_by_name_unless_allowed(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    (root / "src" / "mypkg" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unguarded_optional_imports(root)
    assert len(find_unguarded_optional_imports(root, allow_unparsed=True)) == 1


def test_floor_counts_parsed_modules_and_a_missing_package_is_an_error(tmp_path):
    root = _project(tmp_path, "x = 1\n")
    assert find_unguarded_optional_imports(root, min_files=2) == []
    with pytest.raises(EmptyScanError):
        find_unguarded_optional_imports(root, min_files=3)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    with pytest.raises(CorpusError, match="no package directory"):
        find_unguarded_optional_imports(empty)


def test_a_missing_or_unreadable_pyproject_cannot_be_judged_around(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    (root / "pyproject.toml").unlink()
    with pytest.raises(UnparsedFilesError, match="cannot read the project's dependencies"):
        find_unguarded_optional_imports(root)
    (root / "pyproject.toml").write_text("[project\nname = 1\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match="cannot read the project's dependencies"):
        find_unguarded_optional_imports(root, allow_unparsed=True)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("try:\n    import zstandard\nexcept ImportError:\n    zstandard = None\n", id="try-ImportError"),
        pytest.param("try:\n    import zstandard\nexcept ModuleNotFoundError:\n    zstandard = None\n", id="try-ModuleNotFoundError"),
        pytest.param("try:\n    import zstandard\nexcept (ValueError, ImportError):\n    zstandard = None\n", id="try-tuple"),
        pytest.param("try:\n    import zstandard\nexcept Exception:\n    zstandard = None\n", id="try-Exception"),
        pytest.param("try:\n    import zstandard\nexcept:\n    zstandard = None\n", id="try-bare"),
        pytest.param("try:\n    import zstandard\nexcept ImportError:\n    raise RuntimeError('install the db extra')\n", id="try-reraise"),
        pytest.param("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import zstandard\n", id="TYPE_CHECKING"),
        pytest.param("import typing\nif typing.TYPE_CHECKING:\n    import zstandard\n", id="typing.TYPE_CHECKING"),
        pytest.param(
            "try:\n    import zstandard\n    _HAS = True\nexcept ImportError:\n    _HAS = False\nif _HAS:\n    import catboost\n", id="availability-flag"
        ),
        pytest.param("import importlib.util\nif importlib.util.find_spec('zstandard'):\n    import zstandard\n", id="find_spec"),
        pytest.param("def f():\n    import zstandard\n", id="function"),
        pytest.param("class A:\n    def f(self):\n        from zstandard import ZstdCompressor\n", id="method"),
        pytest.param("import contextlib\nwith contextlib.suppress(ImportError):\n    import zstandard\n", id="suppress"),
        pytest.param("import zstandard  # optional-import-ok: leaf module, only imported by the db backend\n", id="allow-comment"),
        pytest.param("from zstandard import (  # optional-import-ok: leaf\n    ZstdCompressor,\n)\n", id="allow-comment-on-multiline"),
        pytest.param("import numpy\nimport yaml\nfrom sklearn.base import BaseEstimator\n", id="core-dependencies-incl-name-mismatch"),
        pytest.param(
            "import os, sys\nimport json\nfrom collections import abc\nfrom . import sibling\nfrom .sibling import x\nimport mypkg\n",
            id="stdlib-relative-first-party",
        ),
        pytest.param("from __future__ import annotations\n", id="future"),
        pytest.param(
            'import pytest  # optional-import-ok: the test dependency itself\npytest.importorskip("zstandard")\nimport zstandard\n', id="importorskip-statement"
        ),
        pytest.param(
            'import pytest  # optional-import-ok: the test dependency itself\nzstd = pytest.importorskip("zstandard")\nfrom zstandard import ZstdCompressor\n',
            id="importorskip-assigned",
        ),
        pytest.param(
            'from pytest import importorskip  # optional-import-ok: the test dependency itself\nimportorskip("zstandard.backend")\nimport zstandard.backend\n',
            id="importorskip-imported-name-and-dotted",
        ),
    ],
)
def test_every_documented_guard_is_accepted(tmp_path, source):
    assert _found(tmp_path, source) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import zstandard\n", id="bare"),
        pytest.param("from zstandard import ZstdCompressor\n", id="from-import"),
        pytest.param("import zstandard.backend\n", id="dotted"),
        pytest.param("try:\n    import zstandard\nexcept ValueError:\n    pass\n", id="try-wrong-exception"),
        pytest.param("try:\n    pass\nexcept ImportError:\n    import zstandard\n", id="import-in-the-handler"),
        pytest.param("try:\n    import numpy\nexcept ImportError:\n    pass\nelse:\n    import zstandard\n", id="try-else"),
        pytest.param("try:\n    pass\nfinally:\n    import zstandard\n", id="try-finally"),
        pytest.param("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    import zstandard\n", id="TYPE_CHECKING-else"),
        pytest.param("import sys\nif sys.platform == 'win32':\n    import zstandard\n", id="platform-test-is-not-a-guard"),
        pytest.param("_HAS = True\nif _HAS:\n    import zstandard\n", id="a-flag-not-set-in-a-try"),
        pytest.param("class A:\n    import zstandard\n", id="class-body"),
        pytest.param("import contextlib\nwith contextlib.suppress(ValueError):\n    import zstandard\n", id="suppress-wrong-exception"),
        pytest.param("with open('f') as fh:\n    import zstandard\n", id="with-open"),
        pytest.param("for i in range(1):\n    import zstandard\n", id="loop"),
        pytest.param("import zstandard  # optional-import-ok:\n", id="allow-comment-without-a-reason"),
        pytest.param(
            'import pytest  # optional-import-ok: the test dependency itself\nimport zstandard\npytest.importorskip("zstandard")\n',
            id="importorskip-after-the-import",
        ),
        pytest.param(
            'import pytest  # optional-import-ok: the test dependency itself\npytest.importorskip("catboost")\nimport zstandard\n',
            id="importorskip-of-another-package",
        ),
        pytest.param(
            "import pytest  # optional-import-ok: the test dependency itself\npytest.importorskip(name)\nimport zstandard\n",
            id="importorskip-of-a-computed-name",
        ),
        pytest.param("# optional-import-ok: wrong line\nimport zstandard\n", id="allow-comment-on-the-line-above"),
    ],
)
def test_the_unguarded_shapes_are_reported(tmp_path, source):
    assert len(_found(tmp_path, source)) == 1


def test_an_import_inside_a_function_nested_in_a_try_is_lazy_and_one_outside_is_not(tmp_path):
    source = "def f():\n    try:\n        pass\n    finally:\n        import zstandard\nimport catboost\n"
    assert [f.line for f in _found(tmp_path, source)] == [6]


def test_two_distributions_in_one_statement_are_reported_separately_by_line_and_name(tmp_path):
    findings = _found(tmp_path, "import numpy, catboost, lightgbm\nimport properscoring\n")
    assert [(f.line, "catboost" in f.message, "lightgbm" in f.message, "properscoring" in f.message) for f in findings] == [
        (1, True, False, False),
        (1, False, True, False),
        (2, False, False, True),
    ]


def test_an_extra_reached_through_a_self_reference_names_every_extra_that_holds_it(tmp_path):
    (finding,) = _found(tmp_path, "import catboost\n")
    assert "'all', 'boosting' extra" in finding.message
    (only_all,) = _found(tmp_path / "b", "import properscoring\n")
    assert "only in the 'all' extra" in only_all.message


def test_an_undeclared_import_is_reported_unless_switched_off(tmp_path):
    (finding,) = _found(tmp_path, "import requests\n")
    assert "declared nowhere" in finding.message and "'requests'" in finding.message
    assert _found(tmp_path / "b", "import requests\n", flag_undeclared=False) == []
    assert len(_found(tmp_path / "c", "import zstandard\n", flag_undeclared=False)) == 1


def test_name_map_maps_an_import_name_to_its_distribution(tmp_path):
    assert _found(tmp_path, "import cv2\n", name_map={"cv2": "numpy"}) == []
    (finding,) = _found(tmp_path / "b", "import cv2\n", name_map={"cv2": ["zstandard"]})
    assert "'zstandard'" in finding.message and "'db'" in finding.message
    (without,) = _found(tmp_path / "c", "import cv2\n")
    assert "'opencv-python'" in without.message


def test_the_import_name_is_normalised_to_the_distribution_name(tmp_path):
    source = "import properscoring\n"
    (finding,) = _found(tmp_path, source)
    assert "'properscoring'" in finding.message
    underscored = PYPROJECT.replace('"properscoring"', '"some-pkg"')
    (named,) = find_unguarded_optional_imports(_project(tmp_path / "b", "import some_pkg\n", pyproject=underscored))
    assert "'some-pkg'" in named.message


def test_ignore_skips_a_module_by_path_fragment(tmp_path):
    root = _project(tmp_path, "import zstandard\n", name="neural_impl.py")
    assert len(find_unguarded_optional_imports(root)) == 1
    assert find_unguarded_optional_imports(root, ignore=("mypkg/neural_impl",)) == []


def test_packages_and_pyproject_are_parameters(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    other = root / "lib" / "otherpkg"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("import catboost\n", encoding="utf-8")
    only_lib = find_unguarded_optional_imports(root, packages=["lib/otherpkg"])
    assert [f.path for f in only_lib] == ["lib/otherpkg/__init__.py"]
    both = find_unguarded_optional_imports(root, packages=["src/mypkg", "lib/otherpkg"])
    assert [f.path for f in both] == ["lib/otherpkg/__init__.py", "src/mypkg/mod.py"]
    moved = root / "meta" / "pyproject.toml"
    moved.parent.mkdir()
    moved.write_text(PYPROJECT.replace('"zstandard"', '"other"'), encoding="utf-8")
    (finding,) = find_unguarded_optional_imports(root, packages=["src/mypkg"], pyproject=moved, flag_undeclared=True)
    assert "declared nowhere" in finding.message


def test_package_directories_are_found_in_src_and_at_the_top_level_but_not_tests(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    for name in ("tests", "docs"):
        (root / name).mkdir()
        (root / name / "__init__.py").write_text("import catboost\n", encoding="utf-8")
    top = root / "flatpkg"
    top.mkdir()
    (top / "__init__.py").write_text("import lightgbm\n", encoding="utf-8")
    assert sorted(f.path for f in find_unguarded_optional_imports(root)) == ["flatpkg/__init__.py", "src/mypkg/mod.py"]


def test_dynamic_dependencies_are_read_from_the_setuptools_file_list(tmp_path):
    pyproject = (
        '[project]\nname = "mypkg"\nversion = "1"\ndynamic = ["dependencies"]\n\n[tool.setuptools.dynamic]\ndependencies = {file = ["requirements.txt"]}\n'
    )
    root = _project(tmp_path, "import numpy\nimport requests\n", pyproject=pyproject)
    (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (finding,) = find_unguarded_optional_imports(root)
    assert "'requests'" in finding.message


def test_the_assert_names_the_module_and_a_baseline_accepts_it(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    with pytest.raises(AssertionError, match=r"src/mypkg/mod\.py:1"):
        assert_optional_imports_guarded(root)
    baseline = tmp_path / "baseline.json"
    Baseline(baseline, gate="optional_imports_guarded").enforce(find_unguarded_optional_imports(root), refresh=True, grow=True)
    assert_optional_imports_guarded(root, baseline_path=baseline)
    (root / "src" / "mypkg" / "second.py").write_text("import catboost\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="catboost"):
        assert_optional_imports_guarded(root, baseline_path=baseline)


def test_benchmark_and_vendored_directories_are_skipped_by_default_and_scannable_on_request(tmp_path):
    root = _project(tmp_path, "x = 1\n")
    for name in ("_benchmarks", "benchmarks", "_vendored", "profiling"):
        folder = root / "src" / "mypkg" / name
        folder.mkdir()
        (folder / "run.py").write_text("import zstandard\n", encoding="utf-8")
    assert find_unguarded_optional_imports(root) == []
    assert len(find_unguarded_optional_imports(root, ignore=())) == 4
    assert [f.path for f in find_unguarded_optional_imports(root, ignore=("/_vendored/",))] == [
        "src/mypkg/_benchmarks/run.py",
        "src/mypkg/benchmarks/run.py",
        "src/mypkg/profiling/run.py",
    ]


def test_extra_namespaces_allow_only_the_names_they_list_inside_their_path(tmp_path):
    root = _project(tmp_path, "import zstandard\n")
    neural = root / "src" / "mypkg" / "neural"
    neural.mkdir()
    (neural / "net.py").write_text("import catboost\nimport lightgbm\n", encoding="utf-8")
    found = find_unguarded_optional_imports(root, extra_namespaces={"mypkg/neural": ["catboost"]})
    assert [(f.path, "lightgbm" in f.message) for f in found] == [("src/mypkg/mod.py", False), ("src/mypkg/neural/net.py", True)]


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for rel, text in files.items():
        path = root / "src" / "mypkg" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_reachable_from_judges_only_what_importing_the_package_loads(tmp_path):
    root = _tree(
        tmp_path,
        {
            "__init__.py": "from . import core\nfrom .sub import leaf\n",
            "core.py": "import zstandard\nfrom .helpers import h\n",
            "helpers.py": "import catboost\n",
            "sub/__init__.py": "",
            "sub/leaf.py": "from .. import extra_free\nimport lightgbm\n",
            "extra_free.py": "x = 1\n",
            "llm.py": "import properscoring\n",
            "lazy.py": "import shap\n",
        },
    )
    everything = find_unguarded_optional_imports(root)
    assert sorted({f.path for f in everything}) == [
        "src/mypkg/core.py",
        "src/mypkg/helpers.py",
        "src/mypkg/lazy.py",
        "src/mypkg/llm.py",
        "src/mypkg/sub/leaf.py",
    ]
    reached = find_unguarded_optional_imports(root, reachable_from=["mypkg"], flag_undeclared=False)
    assert sorted({f.path for f in reached}) == ["src/mypkg/core.py", "src/mypkg/helpers.py", "src/mypkg/sub/leaf.py"]


def test_a_guarded_first_party_import_does_not_pull_its_target_into_the_base_import(tmp_path):
    root = _tree(
        tmp_path,
        {
            "__init__.py": "try:\n    from . import heavy\nexcept ImportError:\n    heavy = None\n\n\ndef f():\n    from . import later\n",
            "heavy.py": "import zstandard\n",
            "later.py": "import catboost\n",
        },
    )
    assert find_unguarded_optional_imports(root, reachable_from=["mypkg"]) == []
    assert len(find_unguarded_optional_imports(root)) == 2


def test_reachable_from_follows_absolute_and_multi_level_relative_imports_and_names_a_missing_module(tmp_path):
    root = _tree(
        tmp_path,
        {
            "__init__.py": "import mypkg.a.b\n",
            "a/__init__.py": "",
            "a/b.py": "from ..c import value\n",
            "c.py": "import zstandard\n",
            "unreached.py": "import catboost\n",
        },
    )
    assert [f.path for f in find_unguarded_optional_imports(root, reachable_from=["mypkg"])] == ["src/mypkg/c.py"]
    with pytest.raises(CorpusError, match="nope"):
        find_unguarded_optional_imports(root, reachable_from=["nope"])


def test_a_sibling_file_is_first_party_only_for_a_script_never_inside_a_package(tmp_path):
    """``python dir/script.py`` puts ``dir`` on sys.path, so ``import helper`` there is first-party; in a package ``import
    zstandard`` is the top-level distribution even when a ``zstandard.py`` sits next to it."""
    root = _project(tmp_path, "import zstandard\n")
    (root / "src" / "mypkg" / "zstandard.py").write_text("x = 1\n", encoding="utf-8")
    assert [f.path for f in find_unguarded_optional_imports(root)] == ["src/mypkg/mod.py"]
    scripts = root / "tools"
    scripts.mkdir()
    (scripts / "run.py").write_text("import zstandard\n", encoding="utf-8")
    (scripts / "zstandard.py").write_text("x = 1\n", encoding="utf-8")
    assert find_unguarded_optional_imports(root, packages=["tools"]) == []
