"""Tests for py_ci_shared.vendored_internal_imports."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, clear_parse_cache
from py_ci_shared.vendored_internal_imports import RULE, assert_no_vendored_internal_imports, find_vendored_internal_imports, vendored_part

BOM = b"\xef\xbb\xbf"


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    clear_parse_cache()


def _found(root: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_vendored_internal_imports(root, use_git=False, **kw)]  # type: ignore[arg-type]


def test_reports_the_seeded_violation(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "x = 1\nfrom joblib.externals import cloudpickle\n")
    assert _found(tmp_path) == [
        (
            "m.py",
            2,
            "imports `joblib.externals`, a private copy vendored inside `joblib.externals`: import the standalone distribution (cloudpickle) and declare it",
        )
    ]
    assert find_vendored_internal_imports(tmp_path, use_git=False)[0].rule == RULE


@pytest.mark.parametrize(
    ("line", "module", "standalone"),
    [
        ("import joblib.externals.loky as loky", "joblib.externals.loky", "loky"),
        ("from joblib.externals.loky.backend import context", "joblib.externals.loky.backend", "loky"),
        ("from pip._vendor import requests", "pip._vendor", "requests"),
        ("from setuptools.extern import packaging", "setuptools.extern", "packaging"),
        ("from sklearn.externals import joblib", "sklearn.externals", "joblib"),
        ("from requests.packages.urllib3.util import Retry", "requests.packages.urllib3.util", "urllib3"),
        ("import botocore.vendored.requests", "botocore.vendored.requests", "requests"),
        ("import importlib\nm = importlib.import_module('joblib.externals.loky')", "joblib.externals.loky", "loky"),
    ],
)
def test_each_vendored_shape_is_reported_with_the_standalone_name(tmp_path: Path, line: str, module: str, standalone: str) -> None:
    _write(tmp_path, "m.py", line + "\n")
    messages = [m for _, _, m in _found(tmp_path)]
    assert len(messages) == 1
    assert f"`{module}`" in messages[0] and f"({standalone})" in messages[0]


@pytest.mark.parametrize(
    "text",
    [
        "import cloudpickle\nimport loky\n",
        "from . import externals\nfrom .vendor import x\n",  # relative: the repo's own code
        "import externals.thing\n",  # a top-level package named like a vendoring directory is a real distribution
        "from mypkg._vendored import infonet\n",  # first-party vendored copy (mypkg is a package of this repo)
        "from joblib.externals import cloudpickle  # vendored-ok: joblib<1.6 only, pinned\n",
        "try:\n    import cloudpickle\nexcept ImportError:\n    from joblib.externals import cloudpickle\n",
        "try:\n    from loky import backend\nexcept (ModuleNotFoundError, OSError):\n    from joblib.externals.loky import backend\n",
        "x = 'from joblib.externals import cloudpickle'\n",
    ],
)
def test_negative_controls(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "mypkg/__init__.py", "")
    _write(tmp_path, "m.py", text)
    assert _found(tmp_path) == []


def test_a_fallback_for_a_different_name_or_a_non_import_handler_is_still_reported(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "try:\n    import dill\nexcept ImportError:\n    from joblib.externals import cloudpickle\n")
    _write(tmp_path, "b.py", "try:\n    import cloudpickle\nexcept ValueError:\n    from joblib.externals import cloudpickle\n")
    _write(tmp_path, "c.py", "import cloudpickle\nfrom joblib.externals import cloudpickle as cp\n")
    assert [(p, line) for p, line, _ in _found(tmp_path)] == [("a.py", 4), ("b.py", 4), ("c.py", 2)]


def test_first_party_under_src_and_explicit_names_are_exempt(tmp_path: Path) -> None:
    _write(tmp_path, "src/ownpkg/__init__.py", "")
    _write(tmp_path, "m.py", "from ownpkg._vendor import x\nfrom otherpkg.vendor import y\n")
    assert [line for _, line, _ in _found(tmp_path)] == [2]
    assert _found(tmp_path, first_party=["otherpkg"]) == []


def test_tests_are_scanned_by_default_and_can_be_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_x.py", "import joblib.externals\n")
    assert [p for p, _, _ in _found(tmp_path)] == ["tests/test_x.py"]
    assert _found(tmp_path, include_tests=False) == []


def test_vendored_part() -> None:
    assert vendored_part("joblib.externals.loky.backend") == ("joblib.externals", "loky")
    assert vendored_part("joblib.externals") == ("joblib.externals", "")
    assert vendored_part("requests.packages") == ("requests.packages", "")
    assert vendored_part("externals.x") is None
    assert vendored_part("numpy.linalg") is None


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path: Path) -> None:
    _write(tmp_path / "a", "m.py", "from joblib.externals import cloudpickle\n")
    _write(tmp_path / "b", "m.py", "from joblib.externals import cloudpickle\n", bom=True)
    assert _found(tmp_path / "a") == _found(tmp_path / "b") != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "import cloudpickle\n")
    _write(tmp_path, "broken.py", "def broken(:\n")
    with pytest.raises(pytest.fail.Exception, match=r"broken\.py"):
        assert_no_vendored_internal_imports(tmp_path, use_git=False)
    assert_no_vendored_internal_imports(tmp_path, allow_unparsed=True, use_git=False)
    assert [f.rule for f in find_vendored_internal_imports(tmp_path, use_git=False)] == ["unparsed-file"]


def test_an_empty_corpus_fails_the_floor(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_vendored_internal_imports(tmp_path, use_git=False)
    assert not issubclass(EmptyScanError, pytest.fail.Exception)  # the floor goes through the report, not a raw scan error


def test_assert_fails_raw_and_ratchets_against_a_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    _write(tmp_path, "m.py", "from joblib.externals import cloudpickle\n")
    with pytest.raises(pytest.fail.Exception, match="cloudpickle"):
        assert_no_vendored_internal_imports(tmp_path, use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_no_vendored_internal_imports(tmp_path, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_vendored_internal_imports(tmp_path, baseline_path=baseline, use_git=False)
    _write(tmp_path, "n.py", "import joblib.externals.loky\n")
    with pytest.raises(pytest.fail.Exception, match="loky"):
        assert_no_vendored_internal_imports(tmp_path, baseline_path=baseline, use_git=False)
