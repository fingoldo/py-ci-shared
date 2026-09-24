"""`_core.scan`: unparsed files are reported, and the floor counts PARSED files."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import UNPARSED_RULE, EmptyScanError, Finding, UnparsedFilesError, scan_python


def _files(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


def test_parsed_and_unparsed_are_separated(tmp_path):
    _files(tmp_path, {"ok.py": "x = 1\n", "bom.py": b"\xef\xbb\xbfy = 2\n", "bad.py": "def (:\n", "latin.py": b"s = '\xff'\n"})
    result = scan_python(tmp_path, use_git=False)
    assert [f.rel for f in result.files] == ["bom.py", "ok.py"]
    assert [(p.rel, p.line, p.kind) for p in result.unparsed] == [("bad.py", 1, "unparsable"), ("latin.py", 1, "unreadable")]
    path, tree, source = result.files[0]
    assert path == tmp_path / "bom.py" and source == "y = 2\n" and tree.body


def test_unparsed_become_findings(tmp_path):
    _files(tmp_path, {"bad.py": "x = 1\ndef (:\n"})
    (finding,) = scan_python(tmp_path, use_git=False).unparsed_findings()
    assert isinstance(finding, Finding)
    assert (finding.path, finding.line, finding.rule) == ("bad.py", 2, UNPARSED_RULE)
    assert finding.message.startswith("unparsable:")


class TestFloor:
    def test_the_floor_counts_parsed_files_not_inputs(self, tmp_path):
        """Two inputs, zero parsed: the old input-count floor passed this with no check run."""
        _files(tmp_path, {"a.py": "def (:\n", "b.py": "class :\n"})
        result = scan_python(tmp_path, min_files=1, use_git=False)
        with pytest.raises(EmptyScanError, match=r"only 0 file\(s\) parsed.*2 more could not be parsed"):
            result.check_floor()
        _files(tmp_path, {"c.py": "x = 1\n"})
        scan_python(tmp_path, min_files=1, use_git=False).check_floor()  # control: one parsed file meets it

    def test_an_empty_root_fails_the_floor(self, tmp_path):
        with pytest.raises(EmptyScanError):
            scan_python(tmp_path, use_git=False).assert_ok()
        scan_python(tmp_path, min_files=0, use_git=False).assert_ok()  # control: an explicit zero floor

    def test_assert_ok_fails_on_unparsed_unless_allowed(self, tmp_path):
        _files(tmp_path, {"ok.py": "x = 1\n", "bad.py": "def (:\n"})
        result = scan_python(tmp_path, use_git=False)
        with pytest.raises(UnparsedFilesError, match=r"bad\.py:1: unparsable"):
            result.assert_ok()
        result.assert_ok(allow_unparsed=True)

    def test_floor_errors_are_assertion_errors(self):
        assert issubclass(EmptyScanError, AssertionError) and issubclass(UnparsedFilesError, AssertionError)


def test_explicit_file_list_with_a_file_outside_root(tmp_path):
    _files(tmp_path, {"in/a.py": "x = 1\n", "out/b.py": "y = 1\n"})
    result = scan_python([tmp_path / "out" / "b.py", tmp_path / "in" / "a.py"], root=tmp_path / "in")
    assert [f.rel for f in result.files] == ["a.py", (tmp_path / "out" / "b.py").as_posix()]


def test_excluded_dirs_do_not_count_toward_the_floor(tmp_path):
    _files(tmp_path, {"build/a.py": "x = 1\n"})
    with pytest.raises(EmptyScanError):
        scan_python(tmp_path, use_git=False).check_floor()
    scan_python(tmp_path, exclude=(), use_git=False).check_floor()


def _write_one(d: Path, name: str, body: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return d / name


class TestMixedCorpus:
    def test_directories_in_a_list_are_expanded_under_the_same_exclusions(self, tmp_path):
        _write_one(tmp_path / "a", "x.py", "x = 1\n")
        _write_one(tmp_path / "a" / "__pycache__", "junk.py", "x = 1\n")
        _write_one(tmp_path / "b", "y.py", "y = 1\n")
        loose = _write_one(tmp_path, "z.py", "z = 1\n")
        result = scan_python([tmp_path / "a", str(tmp_path / "b"), loose], use_git=False)
        assert sorted(f.rel for f in result.files) == sorted(["x.py", "y.py", loose.as_posix()])

    def test_a_file_named_twice_is_parsed_once(self, tmp_path):
        f = _write_one(tmp_path / "a", "x.py", "x = 1\n")
        assert len(scan_python([tmp_path / "a", f], use_git=False).files) == 1

    def test_a_missing_entry_raises(self, tmp_path):
        from py_ci_shared._core import CorpusError

        with pytest.raises(CorpusError, match="does not exist"):
            scan_python([tmp_path / "missing.py"])
