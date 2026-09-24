from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.hardcoded_token_ceilings import RULE, assert_no_hardcoded_token_ceilings, find_hardcoded_token_ceilings


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_hardcoded_token_ceilings(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("client.create(model=m, max_tokens=4096)", "f: max_tokens=4096 is a hand-picked output ceiling"),
        ("client.create(max_output_tokens=8_192)", "f: max_output_tokens=8192 is a hand-picked output ceiling"),
        ('payload = {"max_completion_tokens": 2048}', "f: max_completion_tokens=2048 is a hand-picked output ceiling"),
        ("client.create(max_tokens=cfg.limit or 4096)", "f: max_tokens falls back to 4096 when the real ceiling is unknown"),
        ("client.create(max_tokens=cfg.limit if cfg else 512)", "f: max_tokens falls back to 512 when the real ceiling is unknown"),
        ('n = req.get("max_tokens", 1024)', "f: max_tokens falls back to 1024 when the real ceiling is unknown"),
        ('n = getattr(p, "max_output_tokens", 8192)', "f: max_output_tokens falls back to 8192 when the real ceiling is unknown"),
    ],
)
def test_each_shape_is_flagged(tmp_path: Path, line: str, message: str) -> None:
    _write(tmp_path, "m.py", f"def f(client, m, cfg, req, p):\n    {line}\n")
    assert _found(tmp_path) == [("m.py", 2, message)]


def test_parameter_defaults_positional_and_keyword_only(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(a, max_tokens: int = 4096, *, max_output_tokens=2048, other=5):\n    pass\n")
    assert [m for _, _, m in _found(tmp_path)] == ["f: parameter max_tokens defaults to 4096", "f: parameter max_output_tokens defaults to 2048"]


@pytest.mark.parametrize(
    "line",
    [
        "client.create(max_tokens=0)",
        "client.create(max_tokens=1)",
        "client.create(max_tokens=limit)",
        "client.create(max_tokens=provider.max_output_tokens)",
        "client.create(max_new_tokens=50)",
        "client.create(temperature=4096)",
        'payload = {"max_tokens": limit}',
        'n = req.get("temperature", 1024)',
        "client.create(max_tokens=True)",
        "client.create(max_tokens=4096)  # token-ceiling-ok: vendor hard limit for this endpoint",
    ],
)
def test_negative_controls(tmp_path: Path, line: str) -> None:
    _write(tmp_path, "m.py", f"def f(client, limit, provider, req):\n    {line}\n")
    assert _found(tmp_path) == []


def test_custom_names_and_threshold(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(c):\n    c.gen(max_new_tokens=50)\n    c.gen(max_tokens=10)\n")
    assert _found(tmp_path) == []
    assert [line for _, line, _ in _found(tmp_path, names={"max_new_tokens", "max_tokens"}, allow_below=1)] == [2, 3]


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_x.py", "def f(c):\n    c.gen(max_tokens=100)\n")
    _write(tmp_path, "pkg/test_y.py", "def f(c):\n    c.gen(max_tokens=100)\n")
    assert _found(tmp_path) == []
    assert len(_found(tmp_path, include_tests=True)) == 2


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(c):\n    c.gen(max_tokens=4096)\n", bom=True)
    assert len(_found(tmp_path)) == 1


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_hardcoded_token_ceilings(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    assert [f.rule for f in find_hardcoded_token_ceilings(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_hardcoded_token_ceilings(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f(c, p):\n    c.gen(max_tokens=p.max_output_tokens)\n")
    assert_no_hardcoded_token_ceilings(src, use_git=False)
    _write(src, "m.py", "def f(c, p):\n    c.gen(max_tokens=4096)\n")
    with pytest.raises(pytest.fail.Exception, match="max_tokens=4096"):
        assert_no_hardcoded_token_ceilings(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_hardcoded_token_ceilings(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_hardcoded_token_ceilings(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_hardcoded_token_ceilings(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f(c, p):\n    c.gen(max_tokens=4096)\n    c.gen(max_tokens=4096)\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_hardcoded_token_ceilings(src, baseline_path=baseline, refresh=False, use_git=False)
