"""Behavioural tests for ``phantom_code_references``: each check must fire on the shape it guards and stay
quiet on the shapes that look similar but are not claims."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.phantom_code_references import (
    assert_no_count_claim_mismatches,
    assert_no_phantom_code_references,
    count_claim_mismatches,
    dart_declarations,
    find_phantom_code_references,
    python_declarations,
)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_python_declarations_come_from_the_ast(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "pkg/mod.py",
        "import os\nfrom x import Thing as Alias\nLIMIT = 3\n\nclass Foo:\n    rate = 1\n    def bar(self, count):\n        pass\n\ndef baz():\n    pass\n",
    )
    names = python_declarations([p])
    assert {"mod", "Foo", "Foo.bar", "bar", "baz", "LIMIT", "Foo.rate", "count", "os", "Alias"} <= names


def test_dart_declarations_see_classes_members_and_fields(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "lib/w.dart",
        "class KbStatsBadge extends StatelessWidget {\n  const KbStatsBadge({required this.stats});\n  final int stats;\n  String label() => 'x';\n}\nString localizedModality(int x) => '';\n",
    )
    names = dart_declarations([p])
    assert {"w", "KbStatsBadge", "stats", "label", "localizedModality"} <= names


def test_a_backticked_test_file_that_does_not_exist_is_a_violation(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", '"""Pinned by `test_nothing_here.py` and `real_test.dart`."""\n')
    _write(tmp_path, "test/real_test.dart", "void main() {}\n")
    out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
    assert out == ["pkg/mod.py:1: `test_nothing_here.py` names a test file that does not exist"]


def test_a_backticked_name_that_is_declared_passes_and_an_undeclared_one_fails(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "pkg/mod.py",
        "# uses `Foo.bar()` and `Ghost` and `Foo.anything` and `some prose here`\nclass Foo:\n    def bar(self):\n        pass\n",
    )
    out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
    # `Foo.anything`: declared head, unknown member - a library-member shape, not a claim this module can
    # resolve, so it passes; `some prose here` is not an identifier and is skipped.
    assert out == ["pkg/mod.py:1: `Ghost` names nothing declared in this repo"]


def test_dart_comments_are_read_and_language_words_are_never_references(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "lib/a.dart",
        "/// Wraps the line in `SingleChildScrollView`, unlike `Text` or `null`.\nclass A {}\n",
    )
    out = find_phantom_code_references([p], tmp_path, dart_declarations([p]), extra_known={"SingleChildScrollView"})
    assert out == []
    out = find_phantom_code_references([p], tmp_path, dart_declarations([p]))
    assert out == ["lib/a.dart:1: `SingleChildScrollView` names nothing declared in this repo"]


def test_count_claim_fires_only_when_the_numbered_list_disagrees(tmp_path: Path) -> None:
    bad = _write(
        tmp_path,
        "a.py",
        "# minus the TWO known exceptions, both recorded here:\n#   1. the chip\n#   2. the footer\n#   3. the cloud\n#   4. the menu\n",
    )
    good = _write(tmp_path, "b.py", "# the three cases:\n#   1. a\n#   2. b\n#   3. c\n")
    prose = _write(tmp_path, "c.py", "# there were two cases last week, see the log.\nx = 1\n")
    assert count_claim_mismatches([bad]) == [f"{bad}:1: says 2, lists 4"]
    assert count_claim_mismatches([good]) == []
    assert count_claim_mismatches([prose]) == []


def test_assert_variant_uses_and_prunes_the_baseline(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", "# see `Ghost`\n")
    baseline = tmp_path / "_baseline.json"
    with pytest.raises(pytest.fail.Exception, match="do not extend the baseline"):
        assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
    baseline.write_text(json.dumps({"phantom_references": ["pkg/mod.py:1: `Ghost` names nothing declared in this repo"]}))
    assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
    p.write_text("# fixed\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="no longer reproduced"):
        assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)


def test_assert_count_claims_fails_with_the_location(tmp_path: Path) -> None:
    bad = _write(tmp_path, "a.py", "# the two forms:\n#   1. a\n")
    with pytest.raises(pytest.fail.Exception, match="says 2, lists 1"):
        assert_no_count_claim_mismatches([bad])
