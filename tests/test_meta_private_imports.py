"""Unit tests for meta_private_imports: recursion, unparsable files, BOM, dynamic imports."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared._core import CorpusError
from py_ci_shared.meta_private_imports import assert_no_private_meta_imports, imported_names, private_meta_imports


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestAuditRegressions:
    def test_a_meta_test_in_a_subdirectory_is_scanned(self, tmp_path):
        _write(tmp_path / "test_top.py", "import pkg.api\n")
        _write(tmp_path / "sub" / "test_x.py", "from pkg.mod import _helper\n")

        with pytest.raises(pytest.fail.Exception, match=re.escape("test_x::pkg.mod._helper")):
            assert_no_private_meta_imports(tmp_path, ("pkg",))
        assert_no_private_meta_imports(tmp_path, ("pkg",), recursive=False)  # the old top-level-only view misses it
        assert_no_private_meta_imports(tmp_path, ("pkg",), permitted={"test_x::pkg.mod._helper"})

    def test_an_unparsable_meta_test_fails_instead_of_being_skipped(self, tmp_path):
        _write(tmp_path / "test_ok.py", "import pkg.api\n")
        _write(tmp_path / "test_broken.py", "from pkg.mod import _helper\ndef (:\n")

        parsed, found = private_meta_imports(sorted(tmp_path.glob("test_*.py")), ("pkg",))

        assert parsed == 1 and found == {"test_broken::<unparsed>"}
        with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
            assert_no_private_meta_imports(tmp_path, ("pkg",))

    def test_a_bom_file_is_parsed(self, tmp_path):
        (tmp_path / "test_bom.py").write_bytes(b"\xef\xbb\xbffrom pkg.mod import _helper\n")
        assert private_meta_imports([tmp_path / "test_bom.py"], ("pkg",)) == (1, {"test_bom::pkg.mod._helper"})

    def test_import_module_with_a_literal_private_name_is_caught(self, tmp_path):
        import ast

        tree = ast.parse(
            "import importlib\nimport importlib as il\nfrom importlib import import_module\n"
            "importlib.import_module('pkg._x')\nil.import_module('pkg._y')\nimport_module('pkg._z')\n"
            "__import__('pkg._w')\nimportlib.import_module('pkg.public')\nimportlib.import_module('.rel', 'pkg')\nother.import_module('pkg._no')\n"
        )
        names = imported_names(tree)
        assert {"pkg._x", "pkg._y", "pkg._z", "pkg._w", "pkg.public"} <= set(names)
        assert "pkg._no" not in names and ".rel" not in names

    def test_a_missing_meta_dir_raises_and_an_empty_one_fails_the_floor(self, tmp_path):
        with pytest.raises(CorpusError):
            assert_no_private_meta_imports(tmp_path / "nope", ("pkg",))
        (tmp_path / "empty").mkdir()
        with pytest.raises(pytest.fail.Exception, match="only 0 meta-test file"):
            assert_no_private_meta_imports(tmp_path / "empty", ("pkg",))
