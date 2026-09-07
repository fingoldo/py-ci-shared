"""`deletion_gates` must decide on the parse tree, and must fail loudly when its target is gone.

Both properties are the point rather than implementation detail. A gate that greps reports the
comment documenting a removal as the removal's violation -- that happened three times in one audit
round -- and a gate whose target was renamed must not pass by finding nothing.
"""

from __future__ import annotations

import pytest

from py_ci_shared.deletion_gates import exception_handlers, function_parameters, imported_top_level


def _write(tmp_path, text: str):
    p = tmp_path / "subject.py"
    p.write_text(text, encoding="utf-8")
    return p


class TestFunctionParameters:
    def test_it_finds_every_kind_of_parameter(self, tmp_path):
        p = _write(tmp_path, "def f(a, /, b, *args, c=1, **kw):\n    pass\n")

        assert function_parameters(p, "f") == {"a", "b", "args", "c", "kw"}

    def test_a_deleted_parameter_is_absent(self, tmp_path):
        p = _write(tmp_path, "def bootstrap(name, log_file=None):\n    pass\n")

        assert "sql_file" not in function_parameters(p, "bootstrap")

    def test_a_comment_naming_the_deleted_parameter_does_not_count(self, tmp_path):
        """THE TRAP. The removal is documented where it happened, so a substring search finds the
        explanation and calls it the violation."""
        p = _write(tmp_path, "# `sql_file=` was deleted here: it had no callers.\ndef bootstrap(name):\n    pass\n")

        assert "sql_file" not in function_parameters(p, "bootstrap")

    def test_a_renamed_function_fails_loudly(self, tmp_path):
        """A gate that silently passes once its target is renamed is worse than no gate: it reads as
        a clean bill of health for something nobody is checking any more."""
        p = _write(tmp_path, "def renamed(name):\n    pass\n")

        with pytest.raises(AssertionError, match="needs re-pointing"):
            function_parameters(p, "bootstrap")


class TestImportedTopLevel:
    def test_module_scope_imports_are_reported(self, tmp_path):
        p = _write(tmp_path, "import json\nfrom pathlib import Path\nfrom a.b import c\n")

        assert imported_top_level(p) == {"json", "pathlib", "a"}

    def test_a_function_local_import_is_not(self, tmp_path):
        """The distinction a whole class of findings turns on: an import inside a function does not
        run when the module is imported, so it cannot make importing the module fail."""
        p = _write(tmp_path, "def f():\n    import live_config\n    return live_config\n")

        assert "live_config" not in imported_top_level(p)


class TestExceptionHandlers:
    def test_a_handler_is_counted(self, tmp_path):
        p = _write(tmp_path, "try:\n    x = 1\nexcept ImportError:\n    pass\n")

        assert exception_handlers(p, "ImportError") == 1

    def test_a_tuple_of_exceptions_counts(self, tmp_path):
        p = _write(tmp_path, "try:\n    x = 1\nexcept (OSError, ImportError):\n    pass\n")

        assert exception_handlers(p, "ImportError") == 1

    def test_a_comment_about_the_removed_handler_does_not(self, tmp_path):
        """Same trap as above, and this is the exact spelling that produced a false failure: the
        comment recording why an `except ImportError` was deleted contains the words."""
        p = _write(tmp_path, "# the `except ImportError: pass` here was unreachable, so it is gone\nx = 1\n")

        assert exception_handlers(p, "ImportError") == 0
