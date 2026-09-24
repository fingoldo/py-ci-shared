"""Tests for py_ci_shared.save_failure_markers: every emission shape, what is not an emission, and the fail-closed paths."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared._core import CorpusError
from py_ci_shared.save_failure_markers import DEFAULT_MARKER_PATTERNS, assert_markers_are_fatal, find_emitted_markers


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _is_fatal(error: str) -> bool:
    return error.startswith(("widgets_failed", "gizmos_failed", "a_failed", "b_failed", "c_failed", "d_failed", "e_failed"))


_EVERY_SHAPE = """
def save(errors, e, kind):
    errors.append(rf"a_failed: {e}")
    errors.append(F"b_failed: {e}")
    errors.extend([f"c_failed: {e}", "d_failed: x"])
    errors += [f"e_failed: {e}"]
    errors.insert(0, "widgets_failed: y")
    run(marker="gizmos_failed")
    errors.append(f"{kind}_failed: {e}")
"""


def test_every_emission_shape_is_found(tmp_path: Path):
    _write(tmp_path, "m.py", _EVERY_SHAPE)
    found = find_emitted_markers(tmp_path)
    assert sorted(found) == ["a_failed", "b_failed", "c_failed", "d_failed", "e_failed", "gizmos_failed", "widgets_failed", "{kind}_failed"]
    assert found["e_failed"] == ["m.py:6"]


def test_comments_docstrings_and_non_emissions_are_not_markers(tmp_path: Path):
    _write(
        tmp_path,
        "m.py",
        '"""errors.append(f\\"doc_failed: x\\")"""\n'
        '# errors.append(f"commented_failed: {e}")\n'
        'log.warning("logged_failed: %s", e)\n'
        'raise_it = RuntimeError("raised_failed: z")\n'
        'errors.append("real_failed: z")\n',
    )
    assert sorted(find_emitted_markers(tmp_path)) == ["real_failed"]


def test_explicit_regex_patterns_skip_comments(tmp_path: Path):
    _write(tmp_path, "m.py", '# errors.append(f"commented_failed: {e}")\nerrors.append(rf"a_failed: {e}")\n')
    assert sorted(find_emitted_markers(tmp_path, DEFAULT_MARKER_PATTERNS)) == ["a_failed"]
    custom = (re.compile(r"""emit\(["']([a-z_]+_failed)"""),)
    _write(tmp_path, "n.py", 'emit("x_failed")\n# emit("y_failed")\n')
    assert sorted(find_emitted_markers(tmp_path, custom)) == ["x_failed"]


def test_a_dynamic_marker_must_be_listed_with_a_reason(tmp_path: Path):
    _write(tmp_path, "m.py", _EVERY_SHAPE)
    with pytest.raises(AssertionError, match=r"build their NAME at runtime.*\{kind\}_failed"):
        assert_markers_are_fatal(tmp_path, _is_fatal)
    assert_markers_are_fatal(tmp_path, _is_fatal, non_fatal={"{kind}_failed": "kind is always one of the fatal names above"})


def test_a_missing_root_raises_and_too_few_markers_fail(tmp_path: Path):
    with pytest.raises(CorpusError):
        find_emitted_markers(tmp_path / "nope")
    with pytest.raises(CorpusError):
        assert_markers_are_fatal(tmp_path / "nope", _is_fatal)
    _write(tmp_path, "m.py", "x = 1\n")
    with pytest.raises(AssertionError, match="found 0 emitted marker"):
        assert_markers_are_fatal(tmp_path, _is_fatal)
    assert_markers_are_fatal(tmp_path, _is_fatal, min_markers=0)
    _write(tmp_path, "n.py", 'errors.append("widgets_failed: x")\n')
    assert_markers_are_fatal(tmp_path, _is_fatal)


def test_an_undecodable_file_is_reported_not_a_crash(tmp_path: Path):
    _write(tmp_path, "ok.py", 'errors.append("widgets_failed: x")\n')
    (tmp_path / "cp1251.py").write_bytes('errors.append("gizmos_failed: тест")\n'.encode("cp1251"))
    with pytest.raises(AssertionError, match=r"cp1251\.py"):
        find_emitted_markers(tmp_path)
    assert sorted(find_emitted_markers(tmp_path, allow_unparsed=True)) == ["widgets_failed"]
    with pytest.raises(AssertionError, match="could not be read or parsed"):
        assert_markers_are_fatal(tmp_path, _is_fatal)


def test_a_bom_file_and_skipped_directories(tmp_path: Path):
    (tmp_path / "bom.py").write_bytes(b'\xef\xbb\xbferrors.append("widgets_failed: x")\n')
    _write(tmp_path, ".venv/lib/x.py", 'errors.append("vendored_failed: x")\n')
    assert sorted(find_emitted_markers(tmp_path)) == ["widgets_failed"]


def test_the_original_contract_still_holds(tmp_path: Path):
    _write(tmp_path, "m.py", 'errors.append(f"sprockets_failed: {e}")\n')
    with pytest.raises(AssertionError, match="sprockets_failed"):
        assert_markers_are_fatal(tmp_path, _is_fatal)
    assert_markers_are_fatal(tmp_path, _is_fatal, non_fatal={"sprockets_failed": "regenerable"})
    with pytest.raises(AssertionError, match="needs a reason"):
        assert_markers_are_fatal(tmp_path, _is_fatal, non_fatal={"sprockets_failed": " "})
    with pytest.raises(AssertionError, match="stale exemption"):
        assert_markers_are_fatal(tmp_path, _is_fatal, non_fatal={"sprockets_failed": "r", "gone_failed": "r"})
