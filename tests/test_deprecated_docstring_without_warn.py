"""deprecated_docstring_without_warn: a public function documented as deprecated must warn or carry a deprecated decorator."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.deprecated_docstring_without_warn import (
    RULE,
    assert_deprecated_docstrings_warn,
    find_deprecated_docstrings_without_warn,
)

BOM = b"\xef\xbb\xbf"
NL = chr(10)


def _write(root: Path, rel: str, lines: list, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + (NL.join(lines) + NL).encode("utf-8"))
    return path


def _find(tmp_path: Path, lines: list) -> list:
    _write(tmp_path, "mod.py", lines)
    return find_deprecated_docstrings_without_warn(tmp_path, use_git=False)


def test_the_docstring_only_alias_is_reported_by_name_and_line(tmp_path):
    """The incident: a 'Deprecated alias' that never warns, invisible to -W error::DeprecationWarning sweeps."""
    found = _find(
        tmp_path,
        [
            "def old_name(x):",  # 1
            '    """Deprecated alias of :func:`new_name`."""',  # 2
            "    return new_name(x)",  # 3
        ],
    )
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 1, RULE)]
    assert "old_name" in found[0].message


def test_a_warnings_warn_call_in_the_body_is_clean(tmp_path):
    found = _find(
        tmp_path,
        [
            "import warnings",
            "",
            "def old_name(x):",
            '    """Deprecated alias of :func:`new_name`."""',
            '    warnings.warn("old_name is deprecated; use new_name", DeprecationWarning, stacklevel=2)',
            "    return new_name(x)",
        ],
    )
    assert found == []


def test_a_directly_imported_warn_is_resolved(tmp_path):
    found = _find(
        tmp_path,
        [
            "from warnings import warn",
            "def old_name(x):",
            '    """DEPRECATED: use new_name."""',
            '    warn("old", DeprecationWarning)',
            "    return x",
        ],
    )
    assert found == []


def test_warnings_re_exported_by_a_package_is_still_the_warnings_module(tmp_path):
    """pyutilz binds ``from ._common import warnings`` and then calls ``warnings.warn``: the call warns, so it is clean."""
    found = _find(
        tmp_path,
        [
            "from ._common import warnings",
            "def EnsurePgTableExists(x):",
            '    """Deprecated alias for :func:`ensure_pg_table_exists`."""',
            '    warnings.warn("EnsurePgTableExists is deprecated", DeprecationWarning, stacklevel=2)',
            "    return x",
        ],
    )
    assert found == []


def test_a_deprecated_decorator_is_clean(tmp_path):
    found = _find(
        tmp_path,
        [
            "from typing_extensions import deprecated",
            "@deprecated('use new_name')",
            "def old_name(x):",
            '    """Deprecated alias of new_name."""',
            "    return x",
        ],
    )
    assert found == []


def test_a_private_function_is_not_public_and_not_reported(tmp_path):
    assert _find(tmp_path, ["def _legacy(x):", '    """Deprecated; kept for old pickles."""', "    return x"]) == []


def test_a_method_of_a_private_class_is_not_reported(tmp_path):
    assert _find(tmp_path, ["class _Hidden:", "    def old(self):", '        """Deprecated."""', "        return 1"]) == []


def test_a_public_method_is_reported(tmp_path):
    found = _find(tmp_path, ["class Model:", "    @property", "    def cv_score(self):", '        """Deprecated alias of selection_score."""', "        return 1"])
    assert [f.line for f in found] == [3]


def test_deprecated_in_prose_inside_the_first_paragraph_is_not_a_notice(tmp_path):
    """A dotted identifier such as ``span.deprecated`` is not the word, so the docstring does not claim deprecation."""
    assert _find(tmp_path, ["def classify(span):", '    """Return True when span.deprecated is set."""', "    return span"]) == []


def test_deprecated_only_in_a_later_paragraph_is_not_the_first_paragraph(tmp_path):
    assert _find(tmp_path, ["def f(x):", '    """Build the table.', "", '    The old name was deprecated in 2.0."""', "    return x"]) == []


def test_a_suppression_with_a_reason_is_accepted(tmp_path):
    found = _find(
        tmp_path,
        [
            "# deprecated-ok: kept for the 1.x data loader; removal scheduled with the next major",
            "def old_name(x):",
            '    """Deprecated alias of new_name."""',
            "    return x",
        ],
    )
    assert found == []


def test_a_suppression_without_a_reason_is_itself_a_finding(tmp_path):
    found = _find(tmp_path, ["def old_name(x):  # deprecated-ok:", '    """Deprecated alias of new_name."""', "    return x"])
    assert [f.line for f in found] == [1]
    assert "reason" in found[0].message


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    lines = ["def old_name(x):", '    """Deprecated alias of new_name."""', "    return x"]
    plain = find_deprecated_docstrings_without_warn(_write(tmp_path / "plain", "mod.py", lines).parent, use_git=False)
    bom = find_deprecated_docstrings_without_warn(_write(tmp_path / "bom", "mod.py", lines, bom=True).parent, use_git=False)
    assert [(f.path, f.line, f.message) for f in bom] == [(f.path, f.line, f.message) for f in plain]
    assert len(bom) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, "broken.py", ["def broken(:"])
    _write(tmp_path, "ok.py", ["x = 1"])  # one parsed file clears the floor, so only the unparsed check is under test
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_deprecated_docstrings_without_warn(tmp_path, use_git=False)
    assert find_deprecated_docstrings_without_warn(tmp_path, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_deprecated_docstrings_without_warn(tmp_path, use_git=False)


def test_the_assert_fails_the_build_on_a_finding(tmp_path):
    _write(tmp_path, "mod.py", ["def old_name(x):", '    """Deprecated alias."""', "    return x"])
    with pytest.raises(AssertionError, match="deprecated-docstring-without-warn"):
        assert_deprecated_docstrings_warn(tmp_path, use_git=False)
