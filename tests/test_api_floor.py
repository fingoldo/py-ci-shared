"""Tests for py_ci_shared.api_floor: the guard filter, the vermin output parser, the loud missing-vermin finding, and
real vermin runs over seeded corpora."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from py_ci_shared import api_floor
from py_ci_shared._core import Baseline, CorpusError, EmptyScanError, UnparsedFilesError

BOM = b"\xef\xbb\xbf"
PYPROJECT = b'[project]\nname = "x"\nrequires-python = ">=3.8"\n'
# pyutilz 14dcfc5: Path.write_text(newline=) is 3.10+, and the 3.8/3.9 legs went red after the merge.
SEED = b'from pathlib import Path\n\n\ndef seed_write(p):\n    Path(p).write_text("x", newline="\\n")\n'


def _corpus(tmp_path: Path, files: "dict[str, bytes]") -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _guarded(src: str) -> "set[int]":
    return api_floor.guarded_lines(ast.parse(textwrap.dedent(src)))


class TestGuards:
    def test_version_info_if_guards_both_branches(self):
        lines = _guarded("""
            import sys
            if sys.version_info >= (3, 9):
                new()
            else:
                old()
            after()
            """)
        assert {3, 4, 5, 6} <= lines and 7 not in lines

    def test_hasattr_in_an_and_chain_guards_the_later_operands_only(self):
        lines = _guarded("""
            import ast
            x = hasattr(ast, "unparse") and ast.unparse(t)
            y = ast.unparse(t)
            """)
        assert 3 in lines and 4 not in lines

    def test_conditional_expression_and_type_checking(self):
        lines = _guarded("""
            import sys
            from typing import TYPE_CHECKING
            h = new() if sys.version_info >= (3, 9) else None
            if TYPE_CHECKING:
                import tomllib
            """)
        assert {4, 5, 6} <= lines

    def test_try_import_with_a_guarding_handler(self):
        lines = _guarded("""
            try:
                import tomllib
            except ModuleNotFoundError:
                tomllib = None
            try:
                import zoneinfo
            except ValueError:
                zoneinfo = None
            """)
        assert 3 in lines and 7 not in lines

    def test_an_unrelated_if_is_not_a_guard(self):
        assert _guarded("if debug:\n    new()\n") == set()


def test_parse_vermin_output_handles_drive_letters_and_skips_summaries_and_python2_only():
    text = "\n".join(
        [
            r"C:\repo\a.py:5::!2:3.10:'pathlib.Path.write_text(newline)'",
            "/repo/b.py:9:11:!2:3.11:'tomllib' module",
            "/repo/c.py:7::2.0:!3:'long' member",
            "/repo/e.py:3::!2:3.12:'int.is_integer' member",
            "/repo/d.py:::!2:3.11:",
            ":::!2:3.11:",
        ]
    )
    assert api_floor.parse_vermin_output(text) == [
        (r"C:\repo\a.py", 5, "3.10", "'pathlib.Path.write_text(newline)'"),
        ("/repo/b.py", 9, "3.11", "'tomllib' module"),
    ]


def test_target_from_pyproject(tmp_path):
    assert api_floor.target_from_pyproject(_corpus(tmp_path, {"pyproject.toml": PYPROJECT}) / "pyproject.toml") == "3.8"
    (tmp_path / "p2.toml").write_bytes(b'[project]\nrequires-python = "~=3.10, !=3.10.1"\n')
    assert api_floor.target_from_pyproject(tmp_path / "p2.toml") == "3.10"
    (tmp_path / "p3.toml").write_bytes(b'[project]\nname = "x"\n')
    with pytest.raises(CorpusError, match="no >= lower bound"):
        api_floor.target_from_pyproject(tmp_path / "p3.toml")


def test_without_vermin_the_gate_reports_it_and_never_passes(tmp_path, monkeypatch):
    root = _corpus(tmp_path, {"pyproject.toml": PYPROJECT, "pkg/a.py": b"x = 1\n"})
    monkeypatch.setattr(api_floor, "_vermin_available", lambda: False)
    found = api_floor.find_api_floor(root, use_git=False)
    assert [f.rule for f in found] == [api_floor.RULE_UNAVAILABLE]
    with pytest.raises(AssertionError, match="vermin is not installed"):
        api_floor.assert_api_floor(root, use_git=False)


def test_floor_and_unparsable_checks_come_before_vermin(tmp_path, monkeypatch):
    monkeypatch.setattr(api_floor, "_vermin_available", lambda: False)
    with pytest.raises(EmptyScanError):
        api_floor.find_api_floor(_corpus(tmp_path / "e", {"pyproject.toml": PYPROJECT}), use_git=False)
    broken = _corpus(tmp_path / "b", {"pyproject.toml": PYPROJECT, "broken.py": b"def (:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        api_floor.find_api_floor(broken, use_git=False)
    assert [f.rule for f in api_floor.find_api_floor(broken, use_git=False, allow_unparsed=True)] == [api_floor.RULE_UNAVAILABLE]


def test_findings_drop_guarded_lines_and_files_vermin_did_not_name(tmp_path):
    from py_ci_shared._core import scan_python

    root = _corpus(tmp_path, {"a.py": b"import sys\nif sys.version_info >= (3, 10):\n    new()\nnew()\n"})
    files = list(scan_python(root, use_git=False))
    path = str(files[0].path.resolve())
    hits = [(path, 3, "3.10", "'x'"), (path, 4, "3.10", "'x'"), (str(tmp_path / "elsewhere.py"), 1, "3.10", "'y'")]
    found = api_floor._findings(files, hits, "3.8")
    assert [(f.path, f.line) for f in found] == [("a.py", 4)]


class TestRealVermin:
    @pytest.fixture(autouse=True)
    def _need_vermin(self):
        pytest.importorskip("vermin")

    def test_reports_the_seed(self, tmp_path):
        root = _corpus(tmp_path, {"pyproject.toml": PYPROJECT, "seed.py": SEED})
        found = api_floor.find_api_floor(root, use_git=False)
        assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 5, api_floor.RULE)]
        assert "write_text(newline)" in found[0].message and "3.10" in found[0].message and "3.8" in found[0].message

    def test_negative_control_guarded_and_target_met(self, tmp_path):
        guarded = b'import sys\nfrom pathlib import Path\n\nif sys.version_info >= (3, 10):\n    Path("p").write_text("x", newline="\\n")\n'
        root = _corpus(tmp_path, {"pyproject.toml": PYPROJECT, "guarded.py": guarded})
        assert api_floor.find_api_floor(root, use_git=False) == []
        seed_root = _corpus(tmp_path / "s", {"pyproject.toml": PYPROJECT, "seed.py": SEED})
        assert api_floor.find_api_floor(seed_root, use_git=False, target="3.10") == []

    def test_a_bom_prefixed_file_is_reported_like_the_plain_one(self, tmp_path):
        plain = api_floor.find_api_floor(_corpus(tmp_path / "p", {"pyproject.toml": PYPROJECT, "seed.py": SEED}), use_git=False)
        bom = api_floor.find_api_floor(_corpus(tmp_path / "b", {"pyproject.toml": PYPROJECT, "seed.py": BOM + SEED}), use_git=False)
        assert bom == plain and len(plain) == 1

    def test_annotations_are_not_evaluated(self, tmp_path):
        src = b"from __future__ import annotations\n\n\ndef f(x: list[int]) -> dict[str, int]:\n    return {}\n"
        assert api_floor.find_api_floor(_corpus(tmp_path, {"pyproject.toml": PYPROJECT, "a.py": src}), use_git=False) == []

    def test_assert_and_baseline(self, tmp_path):
        root = _corpus(tmp_path / "r", {"pyproject.toml": PYPROJECT, "seed.py": SEED})
        with pytest.raises(AssertionError, match="newer than requires-python"):
            api_floor.assert_api_floor(root, use_git=False)
        baseline = tmp_path / "baseline.json"
        Baseline(baseline).save(Baseline.count(api_floor.find_api_floor(root, use_git=False)))
        api_floor.assert_api_floor(root, use_git=False, baseline_path=baseline)

    def test_many_files_are_batched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_floor, "_CMDLINE_BUDGET", 10)
        files = {f"m{i}.py": SEED for i in range(3)}
        found = api_floor.find_api_floor(_corpus(tmp_path, {"pyproject.toml": PYPROJECT, **files}), use_git=False)
        assert [f.path for f in found] == ["m0.py", "m1.py", "m2.py"]


def test_batches_respect_the_budget(monkeypatch):
    monkeypatch.setattr(api_floor, "_CMDLINE_BUDGET", 12)
    assert list(api_floor._batches(["aaaa", "bbbb", "cccc", "dddddddddddddddd"])) == [["aaaa", "bbbb"], ["cccc"], ["dddddddddddddddd"]]
