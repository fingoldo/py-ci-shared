from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.lf_file_writes import RULE, assert_no_crlf_writes, find_crlf_writes


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _write(root: Path, rel: str, body: str, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = textwrap.dedent(body).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree
    return path


def _found(root: Path, **kw: object) -> list[tuple[str, int]]:
    return [(f.path, f.line) for f in find_crlf_writes(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "line",
    [
        'Path("deploy.sh").write_text(text)',
        'open("ci.yml", "w").write(text)',
        'open("ci.yaml", mode="a").write(text)',
        'io.open("x.sh", "wt").write(text)',
        'Path("hook.yaml").open("w").write(text)',
        "BASELINE_PATH.write_text(text)",
    ],
)
def test_each_write_shape_to_an_lf_file_is_flagged(tmp_path: Path, line: str) -> None:
    _write(tmp_path, "pkg/m.py", f"import io\nfrom pathlib import Path\nBASELINE_PATH = Path('b.json')\ndef f(text):\n    {line}\n")
    assert _found(tmp_path) == [("pkg/m.py", 5)]


@pytest.mark.parametrize(
    "line",
    [
        'Path("deploy.sh").write_bytes(text.encode())',
        'Path("deploy.sh").write_text(text, newline="\\n")',
        'Path("deploy.sh").write_text(text, "utf-8", None, "\\n")',
        'open("ci.yml", "wb").write(text)',
        'open("ci.yml", "w", newline="\\n").write(text)',
        'open("ci.yml").read()',
        'open("ci.yml", "r").read()',
        'Path("notes.txt").write_text(text)',
        'gzip.open("ci.yml", "wt").write(text)',
        'Path("deploy.sh").write_text(text)  # lf-ok: consumed by PowerShell only',
        'Path(tempfile.mkdtemp()).joinpath("x.sh").write_text(text)',
        'open(path, "w", **opts).write(text)',
    ],
)
def test_negative_controls_are_not_flagged(tmp_path: Path, line: str) -> None:
    _write(tmp_path, "pkg/m.py", f"import gzip, tempfile\nfrom pathlib import Path\npath = 'a.sh'\nopts = {{}}\ndef f(text):\n    {line}\n")
    assert _found(tmp_path) == []


def test_target_resolved_through_assigned_names(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        from pathlib import Path
        ROOT = Path(".")
        def f(text):
            workflows = ROOT / ".github"
            target = workflows / "ci.yml"
            target.write_text(text)
        """,
    )
    assert _found(tmp_path) == [("m.py", 7)]


def test_temp_directory_targets_are_exempt_through_names(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        import tempfile
        from pathlib import Path
        def f(text):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "repo"
                (root / "ci.yml").write_text(text)
        """,
    )
    assert _found(tmp_path) == []


def test_baseline_matches_identifiers_not_literals(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "from pathlib import Path\ndef f(t, out_dir):\n    out = out_dir / 'bench_baseline.json'\n    out.write_text(t)\n")
    assert _found(tmp_path) == []
    _write(tmp_path, "m.py", "def f(t, baseline_path):\n    baseline_path.write_text(t)\n")
    assert _found(tmp_path) == [("m.py", 2)]


def test_tests_are_skipped_unless_included(tmp_path: Path) -> None:
    _write(tmp_path, "tests/helper.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n")
    _write(tmp_path, "pkg/test_m.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n")
    assert _found(tmp_path) == []
    assert sorted(_found(tmp_path, include_tests=True)) == [("pkg/test_m.py", 2), ("tests/helper.py", 2)]


def test_custom_suffixes(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(p, t):\n    (p / 'x.json').write_text(t)\n")
    assert _found(tmp_path) == []
    assert _found(tmp_path, lf_suffixes=(".json",)) == [("m.py", 2)]


def test_bom_file_is_parsed_and_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n", bom=True)
    assert _found(tmp_path) == [("m.py", 2)]


def test_unparsable_file_is_reported_and_fails(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    rules = [f.rule for f in find_crlf_writes(tmp_path, use_git=False)]
    assert rules == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_crlf_writes(tmp_path, use_git=False)


def test_empty_corpus_fails_the_floor(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_crlf_writes(tmp_path, use_git=False)


def test_assert_fails_raw_and_passes_clean(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(p, t):\n    (p / 'x.sh').write_bytes(t)\n")
    assert_no_crlf_writes(tmp_path, use_git=False)
    _write(tmp_path, "m.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n")
    with pytest.raises(pytest.fail.Exception, match=r"m.py:2: \[crlf-write\]"):
        assert_no_crlf_writes(tmp_path, use_git=False)


def test_baseline_missing_refresh_and_ratchet(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n")
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_crlf_writes(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_crlf_writes(src, baseline_path=baseline, refresh=True, use_git=False)
    assert baseline.read_bytes().count(b"\r\n") == 0
    assert_no_crlf_writes(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f(p, t):\n    (p / 'x.sh').write_text(t)\n    (p / 'y.sh').write_text(t)\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_crlf_writes(src, baseline_path=baseline, refresh=False, use_git=False)
