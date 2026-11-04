"""Tests for py_ci_shared.optional_collaborator_wired."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.optional_collaborator_wired import assert_optional_collaborator_wired, find_unwired_optional_collaborators

BOM = b"\xef\xbb\xbf"
UNWIRED = "def split_buyer_info(text, client_memo=None):\n    return client_memo or text\n\ndef run():\n    return split_buyer_info('x')\n"


def _write(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _find(tmp_path: Path, files, **kw):
    _write(tmp_path, files if isinstance(files, dict) else {"m.py": files})
    return find_unwired_optional_collaborators(tmp_path, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    found = _find(tmp_path, UNWIRED)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 1, "optional-collaborator-unwired")]
    assert found[0].message.startswith("split_buyer_info: parameter client_memo=None is never passed a value by any of its 1 non-test call(s)")


def test_keyword_and_positional_wiring_is_clean(tmp_path):
    assert _find(tmp_path, UNWIRED.replace("split_buyer_info('x')", "split_buyer_info('x', client_memo={})")) == []
    assert _find(tmp_path / "p", UNWIRED.replace("split_buyer_info('x')", "split_buyer_info('x', MEMO)")) == [] if (tmp_path / "p").mkdir() is None else None


def test_literal_none_does_not_count_as_wiring(tmp_path):
    assert len(_find(tmp_path, UNWIRED.replace("split_buyer_info('x')", "split_buyer_info('x', None)"))) == 1
    (tmp_path / "m.py").unlink()
    assert len(_find(tmp_path, UNWIRED.replace("split_buyer_info('x')", "split_buyer_info('x', client_memo=None)"))) == 1


def test_one_wired_site_among_unwired_ones_is_clean(tmp_path):
    src = UNWIRED + "\ndef other():\n    return split_buyer_info('y', client_memo=CACHE)\n"
    assert _find(tmp_path, src) == []


def test_methods_skip_self_for_positional_binding(tmp_path):
    src = "class C:\n    def go(self, a, token_fn=None):\n        return token_fn\n\ndef run(c):\n    return c.go(1, make_token)\n"
    assert _find(tmp_path, src) == []
    assert len(_find(tmp_path / "u", src.replace("c.go(1, make_token)", "c.go(1)"))) == 1 if (tmp_path / "u").mkdir() is None else None


def test_star_args_unknown_names_uncalled_and_ambiguous_defs_are_clean(tmp_path):
    assert _find(tmp_path, UNWIRED.replace("split_buyer_info('x')", "split_buyer_info(*ARGS)")) == []
    (tmp_path / "m.py").unlink()
    assert _find(tmp_path, "def f(a, x_cache=None):\n    return x_cache\n") == []
    (tmp_path / "m.py").unlink()
    assert _find(tmp_path, UNWIRED + "\ndef split_buyer_info(a, client_memo=None):\n    return a\n") == []
    (tmp_path / "m.py").unlink()
    assert _find(tmp_path, "def f(a, other=None):\n    return other\n\nf(1)\n") == []


def test_test_calls_do_not_count_and_marker_exempts(tmp_path):
    files = {"m.py": UNWIRED, "tests/test_m.py": "from m import split_buyer_info\ndef test_it():\n    split_buyer_info('x', client_memo={})\n"}
    assert len(_find(tmp_path, files)) == 1
    assert len(find_unwired_optional_collaborators(tmp_path, use_git=False, include_tests=True)) == 0
    (tmp_path / "m.py").write_text("# unwired-ok: injected by the plugin loader\n" + UNWIRED, encoding="utf-8")
    assert find_unwired_optional_collaborators(tmp_path, use_git=False) == []


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    assert [f.line for f in _find(tmp_path / "b", BOM + UNWIRED.encode())] == [1]
    assert [f.line for f in _find(tmp_path / "c", UNWIRED.replace("\n", "\r\n").encode())] == [1]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, {"ok.py": UNWIRED, "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unwired_optional_collaborators(tmp_path, use_git=False)
    assert len(find_unwired_optional_collaborators(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_unwired_optional_collaborators(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    _write(tmp_path, {"m.py": UNWIRED})
    with pytest.raises(AssertionError, match="client_memo"):
        assert_optional_collaborator_wired(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_unwired_optional_collaborators(tmp_path, use_git=False)
    base.write_text(
        json.dumps({"schema": 1, "gate": "optional_collaborator_wired", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8"
    )
    assert_optional_collaborator_wired(tmp_path, use_git=False, baseline_path=base)
    _write(tmp_path, {"n.py": UNWIRED.replace("split_buyer_info", "split_other")})
    with pytest.raises(pytest.fail.Exception, match=r"split_other"):
        assert_optional_collaborator_wired(tmp_path, use_git=False, baseline_path=base)
