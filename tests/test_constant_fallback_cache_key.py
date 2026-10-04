"""Tests for py_ci_shared.constant_fallback_cache_key."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.constant_fallback_cache_key import RULE, assert_constant_fallback_cache_key, find_constant_fallback_cache_key

BOM = b"\xef\xbb\xbf"
VIOLATION = (
    "import hashlib\nimport json\n\n\ndef config_digest(config):\n    try:\n        return hashlib.sha256(json.dumps(config).encode()).hexdigest()\n"
    '    except TypeError:\n        return "uncached"\n'
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, source: str) -> list[str]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.message for f in find_constant_fallback_cache_key(root, use_git=False)]


def _handler(name: str, handler_body: str) -> str:
    return f"def {name}(cfg):\n    try:\n        return repr(cfg)\n    except Exception:\n{handler_body}"


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_constant_fallback_cache_key(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 9, RULE)
    assert "config_digest" in finding.message and "'uncached'" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:9"):
        assert_constant_fallback_cache_key(root, use_git=False)


@pytest.mark.parametrize(
    "returned",
    [
        pytest.param('"uncached"', id="string"),
        pytest.param("None", id="none"),
        pytest.param("0", id="int"),
        pytest.param('("fallback", 0)', id="tuple-of-literals"),
        pytest.param('f"no-key"', id="fstring-without-substitution"),
        pytest.param("UNCACHED", id="uppercase-module-constant"),
    ],
)
def test_a_handler_returning_a_literal_constant_is_reported(tmp_path, returned):
    src = 'UNCACHED = "uncached"\n\n\n' + _handler("cache_key", f"        return {returned}\n")
    assert len(_found(tmp_path, src)) == 1


def test_a_bare_return_in_the_handler_is_the_constant_none(tmp_path):
    assert len(_found(tmp_path, _handler("cache_key", "        return\n"))) == 1


def test_a_handler_assigning_a_constant_to_the_name_the_function_returns_is_reported(tmp_path):
    src = 'def make_cache_key(cfg):\n    try:\n        key = repr(cfg)\n    except Exception:\n        key = "uncached"\n    return key\n'
    assert len(_found(tmp_path, src)) == 1


@pytest.mark.parametrize(
    "handler_body",
    [
        pytest.param("        raise\n", id="re-raise"),
        pytest.param("        raise ValueError('cannot key')\n", id="raise-other"),
        pytest.param('        return "fallback:" + repr(cfg)\n', id="distinct-fallback-from-input"),
        pytest.param("        return hash(id(cfg))\n", id="computed-from-input"),
        pytest.param("        return f'fb:{cfg!r}'\n", id="fstring-with-substitution"),
    ],
)
def test_negative_control_a_handler_that_raises_or_builds_a_distinct_key_is_clean(tmp_path, handler_body):
    assert _found(tmp_path, _handler("config_digest", handler_body)) == []


@pytest.mark.parametrize(
    "header, docstring",
    [
        pytest.param("def cache_key(cfg) -> Optional[str]:", "", id="optional-annotation"),
        pytest.param("def cache_key(cfg) -> str | None:", "", id="union-none-annotation"),
        pytest.param("def cache_key(cfg):", '    """Key of cfg, or None when it cannot be hashed."""\n', id="docstring-says-none"),
    ],
)
def test_none_is_an_accepted_sentinel_only_when_the_function_says_so(tmp_path, header, docstring):
    src = f"{header}\n{docstring}    try:\n        return repr(cfg)\n    except Exception:\n        return None\n"
    assert _found(tmp_path, src) == []
    assert len(_found(tmp_path / "const", src.replace("return None", 'return "uncached"'))) == 1


def test_a_handler_returning_a_bool_is_a_predicate_not_a_key_builder(tmp_path):
    assert _found(tmp_path, _handler("install_fingerprint_guard", "        return False\n")) == []


def test_assigning_a_constant_the_function_does_not_return_is_clean(tmp_path):
    src = 'def make_cache_key(cfg):\n    try:\n        key = repr(cfg)\n    except Exception:\n        warned = "x"\n        raise\n    return key\n'
    assert _found(tmp_path, src) == []


@pytest.mark.parametrize(
    "name",
    ["config_digest", "model_fingerprint", "get_cache_key", "make_cache_id", "hash_of_config", "FrameSignature", "build_model_key", "KeyBuilder_digest"],
)
def test_the_name_patterns_select_key_builders(tmp_path, name):
    assert len(_found(tmp_path, _handler(name, '        return "uncached"\n'))) == 1


@pytest.mark.parametrize("name", ["load_config", "parse", "sort_key", "get_api_key"])
def test_other_functions_may_return_a_constant_from_a_handler(tmp_path, name):
    assert _found(tmp_path, _handler(name, '        return "default"\n')) == []


def test_a_key_builder_decorator_selects_a_function_whatever_its_name(tmp_path):
    src = "from cachelib import key_builder\n\n\n@key_builder\ndef frame_id(df):\n    try:\n        return repr(df)\n    except Exception:\n        return ''\n"
    assert len(_found(tmp_path, src)) == 1


def test_a_nested_function_is_judged_by_its_own_name(tmp_path):
    src = "def config_digest(cfg):\n    def helper():\n        try:\n            return 1\n        except Exception:\n            return 'x'\n    return helper()\n"
    assert _found(tmp_path, src) == []


def test_the_suppression_comment_on_the_return_the_except_or_the_def_silences_it(tmp_path):
    on_return = VIOLATION.replace('return "uncached"', 'return "uncached"  # key-fallback-ok: None means do not cache')
    on_except = VIOLATION.replace("except TypeError:", "except TypeError:  # key-fallback-ok: callers test for the sentinel")
    on_def = VIOLATION.replace("def config_digest(config):", "def config_digest(config):  # key-fallback-ok: sentinel is checked by the caller")
    for index, source in enumerate((on_return, on_except, on_def)):
        assert _found(tmp_path / str(index), source) == []
    unrelated = VIOLATION.replace("import json", "import json  # key-fallback-ok: an import line is not the site")
    assert len(_found(tmp_path / "unrelated", unrelated)) == 1


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_constant_fallback_cache_key(bom, use_git=False) == find_constant_fallback_cache_key(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_constant_fallback_cache_key(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_constant_fallback_cache_key(root, use_git=False)
    assert find_constant_fallback_cache_key(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_constant_fallback_cache_key(tmp_path, use_git=False)
    root = _corpus(tmp_path / "one", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_constant_fallback_cache_key(root, min_files=2, use_git=False)


def test_exclude_skips_paths_by_fragment(tmp_path):
    root = _corpus(tmp_path, {"gen/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    assert [f.path for f in find_constant_fallback_cache_key(root, use_git=False, exclude=("gen/",))] == ["lib/m.py"]
