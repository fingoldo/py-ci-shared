from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.stdlib_json_ban import RULE, assert_no_stdlib_json, find_stdlib_json_imports


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_stdlib_json_imports(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("import json", "import json"),
        ("import json as j", "import json as j"),
        ("import os, json.decoder", "import json.decoder"),
        ("from json import loads, dumps", "from json import loads, dumps"),
        ("from json.decoder import JSONDecodeError", "from json.decoder import JSONDecodeError"),
        ("import importlib\nm = importlib.import_module('json')", "importlib.import_module('json')"),
        ("m = __import__('json')", "__import__('json')"),
    ],
)
def test_each_import_shape_is_flagged(tmp_path: Path, line: str, shown: str) -> None:
    _write(tmp_path, "m.py", line + "\n")
    found = _found(tmp_path)
    assert [m for _, _, m in found] == [f"imports stdlib json: `{shown}`"]


@pytest.mark.parametrize(
    "line",
    [
        "import orjson",
        "import simplejson",
        "from . import json",
        "from .json import loads",
        "import jsonschema",
        "x = 'import json'",
        "import json  # stdlib-json-ok: strict=False",
    ],
)
def test_negative_controls(tmp_path: Path, line: str) -> None:
    _write(tmp_path, "m.py", line + "\n")
    assert _found(tmp_path) == []


def test_allow_globs_exempt_and_tests_included_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/_json.py", "import json\n")
    _write(tmp_path, "pkg/a.py", "import json\n")
    _write(tmp_path, "tests/test_x.py", "import json\n")
    assert [p for p, _, _ in _found(tmp_path, allow={"pkg/_json.py": "the shim"})] == ["pkg/a.py", "tests/test_x.py"]
    assert [p for p, _, _ in _found(tmp_path, allow={"pkg/*.py": "legacy"}, include_tests=False)] == []


def test_allow_entries_need_a_reason_and_must_still_match(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/_json.py", "import json\n")
    _write(tmp_path, "pkg/clean.py", "import orjson\n")
    with pytest.raises(pytest.fail.Exception, match="has no reason"):
        assert_no_stdlib_json(tmp_path, allow={"pkg/_json.py": " "}, use_git=False)
    with pytest.raises(pytest.fail.Exception, match=r"'pkg/clean.py' no longer matches"):
        assert_no_stdlib_json(tmp_path, allow={"pkg/_json.py": "the shim", "pkg/clean.py": "old"}, use_git=False)
    assert_no_stdlib_json(tmp_path, allow={"pkg/_json.py": "the shim"}, use_git=False)


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "import json\n", bom=True)
    assert len(_found(tmp_path)) == 1


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_stdlib_json(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    assert [f.rule for f in find_stdlib_json_imports(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_stdlib_json(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "import orjson\n")
    assert_no_stdlib_json(src, use_git=False)
    _write(src, "m.py", "import json\n")
    with pytest.raises(pytest.fail.Exception, match="imports stdlib json"):
        assert_no_stdlib_json(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_stdlib_json(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_stdlib_json(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_stdlib_json(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "n.py", "import json\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_stdlib_json(src, baseline_path=baseline, refresh=False, use_git=False)
