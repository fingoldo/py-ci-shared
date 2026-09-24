"""`_core.source`: decoding like the interpreter, typed errors, and a parse cache that notices edits."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared._core import SourceParseError, SourceReadError, clear_parse_cache, parse_file, parse_source, read_source
from py_ci_shared._core.source import parse_cache_size


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_parse_cache()
    yield
    clear_parse_cache()


def _write(tmp_path: Path, data: bytes, name: str = "m.py") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


class TestReadSource:
    def test_a_bom_is_stripped(self, tmp_path):
        p = _write(tmp_path, b"\xef\xbb\xbfx = 1\n")
        assert read_source(p) == "x = 1\n"
        assert read_source(_write(tmp_path, b"x = 1\n", "plain.py")) == "x = 1\n"  # control

    def test_a_bom_is_stripped_for_non_python_text_too(self, tmp_path):
        assert read_source(_write(tmp_path, b"\xef\xbb\xbf{}\n", "b.json")) == "{}\n"
        assert read_source(_write(tmp_path, b"{}\n", "c.json")) == "{}\n"

    def test_undecodable_bytes_raise_a_typed_error_with_the_line(self, tmp_path):
        p = _write(tmp_path, b"a = 1\nb = '\xff'\n")
        with pytest.raises(SourceReadError) as info:
            read_source(p)
        assert info.value.path == p and info.value.line == 2 and info.value.kind == "unreadable"
        assert read_source(_write(tmp_path, "b = '\u00ff'\n".encode(), "ok.py")) == "b = '\u00ff'\n"  # control: valid UTF-8

    def test_a_pep263_cookie_is_honoured(self, tmp_path):
        p = _write(tmp_path, b"# -*- coding: latin-1 -*-\ns = '\xe9'\n")
        assert "\u00e9" in read_source(p)
        # control: the same bytes without the cookie are not valid UTF-8
        with pytest.raises(SourceReadError):
            read_source(_write(tmp_path, b"s = '\xe9'\n", "nocookie.py"))

    def test_a_missing_file_is_a_read_error_not_an_oserror(self, tmp_path):
        with pytest.raises(SourceReadError, match="cannot read"):
            read_source(tmp_path / "missing.py")

    def test_the_empty_file(self, tmp_path):
        p = _write(tmp_path, b"")
        assert read_source(p) == ""
        assert isinstance(parse_file(p), ast.Module) and parse_file(p).body == []


class TestParseFile:
    def test_crlf_parses_with_correct_line_numbers(self, tmp_path):
        p = _write(tmp_path, b"x = 1\r\n\r\ny = 2\r\n")
        tree = parse_file(p)
        assert [n.lineno for n in tree.body] == [1, 3]
        source, _ = parse_source(p)
        assert source.splitlines()[2] == "y = 2"

    def test_bom_plus_crlf_parses(self, tmp_path):
        tree = parse_file(_write(tmp_path, b"\xef\xbb\xbfimport os\r\nx = os.sep\r\n"))
        assert isinstance(tree.body[0], ast.Import)

    def test_a_syntax_error_is_typed_with_path_line_and_message(self, tmp_path):
        p = _write(tmp_path, b"x = 1\ndef (:\n")
        with pytest.raises(SourceParseError) as info:
            parse_file(p)
        err = info.value
        assert (err.path, err.line, err.kind) == (p, 2, "unparsable") and err.message
        assert f"{p}:2" in str(err)

    def test_a_nul_byte_is_a_parse_error(self, tmp_path):
        with pytest.raises(SourceParseError):
            parse_file(_write(tmp_path, b"x = 1\x00\n"))

    def test_the_cache_returns_the_same_tree_until_the_file_changes(self, tmp_path):
        p = _write(tmp_path, b"x = 1\n")
        first = parse_file(p)
        assert parse_file(p) is first
        assert parse_cache_size() == 1
        p.write_bytes(b"x = 1\ny = 2\n")  # different size => new key even within one mtime tick
        second = parse_file(p)
        assert second is not first and len(second.body) == 2
        assert parse_cache_size() == 1, "a changed file replaces its entry, it does not accumulate"

    def test_same_size_edit_is_seen_through_mtime(self, tmp_path):
        p = _write(tmp_path, b"x = 1\n")
        first = parse_file(p)
        p.write_bytes(b"y = 1\n")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        tree = parse_file(p)
        assert tree is not first
        assert isinstance(tree.body[0], ast.Assign) and tree.body[0].targets[0].id == "y"  # type: ignore[attr-defined]

    def test_a_parse_error_is_not_cached_as_success(self, tmp_path):
        p = _write(tmp_path, b"def (:\n")
        with pytest.raises(SourceParseError):
            parse_file(p)
        assert parse_cache_size() == 0
        p.write_bytes(b"def f():\n    pass\n")
        assert len(parse_file(p).body) == 1
