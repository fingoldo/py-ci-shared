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
    # `Foo.anything`: Foo is a class of this repo whose members are all known, so an undeclared member is a
    # phantom; `some prose here` is not an identifier and is skipped.
    assert out == [
        "pkg/mod.py:1: `Ghost` names nothing declared in this repo",
        "pkg/mod.py:1: `Foo.anything` names a member `Foo` does not declare",
    ]


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


class TestAuditRegressions:
    def test_a_hash_inside_a_string_is_not_a_comment(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "pkg/mod.py", 'URL = "http://a#`Ghost()`"\n# but this `Phantom()` is a comment\n')
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == ["pkg/mod.py:2: `Phantom()` names nothing declared in this repo"]

    def test_an_assigned_triple_quoted_literal_does_not_flip_docstring_parity(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "pkg/mod.py",
            'SQL = """\nSELECT `Ghost()` FROM t\n"""\n\n\ndef f():\n    """Calls `Missing()`."""\n    x = 1  # and `AlsoMissing()`\n',
        )
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == [
            "pkg/mod.py:7: `Missing()` names nothing declared in this repo",
            "pkg/mod.py:8: `AlsoMissing()` names nothing declared in this repo",
        ]

    def test_members_of_repo_classes_are_checked_and_open_classes_are_not(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "pkg/mod.py",
            "from pydantic import BaseModel\n"
            "# `Foo.renamed_method()` `Foo.method()` `Foo.attr` `Child.method()` `Model.model_dump()` `Foo.method.extra`\n"
            "class Foo:\n    def __init__(self):\n        self.attr = 1\n    def method(self):\n        pass\n"
            "class Child(Foo):\n    pass\n"
            "class Model(BaseModel):\n    x: int = 0\n",
        )
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == ["pkg/mod.py:2: `Foo.renamed_method()` names a member `Foo` does not declare"]

    def test_a_three_part_dotted_name_is_judged_on_its_first_member(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "pkg/mod.py", "# `Foo.gone.x` and `Ghost.a.b`\nclass Foo:\n    pass\n")
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == [
            "pkg/mod.py:1: `Foo.gone.x` names a member `Foo` does not declare",
            "pkg/mod.py:1: `Ghost.a.b` names nothing declared in this repo",
        ]

    def test_test_files_are_matched_by_path_and_excluded_dirs_do_not_count(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "pkg/mod.py", "# `tests/unit/test_foo.py` `tests/test_foo.py` `test_vendored.py`\n")
        _write(tmp_path, "tests/test_foo.py", "")
        _write(tmp_path, ".venv/lib/test_vendored.py", "")
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == [
            "pkg/mod.py:1: `tests/unit/test_foo.py` names a test file that does not exist",
            "pkg/mod.py:1: `test_vendored.py` names a test file that does not exist",
        ]

    def test_bom_and_unparsable_files(self, tmp_path: Path) -> None:
        bom = tmp_path / "bom.py"
        bom.write_bytes(b"\xef\xbb\xbf# `Ghost()`\nclass Real:\n    pass\n")
        bad = _write(tmp_path, "bad.py", "# `Real`\ndef (:\n")
        out = find_phantom_code_references([bom, bad], tmp_path, python_declarations([bom, bad]))
        assert out[0] == "bom.py:1: `Ghost()` names nothing declared in this repo"
        assert out[1].startswith("bad.py:2: unparsable:")
        assert "Real" in python_declarations([bom])


class TestExternalDottedNames:
    """Dotted names whose head the repo does not declare resolve by import, attribute by attribute (CANARY-24)."""

    def test_installed_module_paths_resolve_and_misspellings_do_not(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "pkg/mod.py",
            "# `os.path.join` `email.mime.text.MIMEText` `json.dumps()` `ValueError.args`\n" "# `os.path.joinn` `nosuchpkg.x.y` `email.mime.text.MIMEGhost`\n",
        )
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == [
            "pkg/mod.py:2: `os.path.joinn` does not resolve by import",
            "pkg/mod.py:2: `nosuchpkg.x.y` names nothing declared in this repo",
            "pkg/mod.py:2: `email.mime.text.MIMEGhost` does not resolve by import",
        ]

    def test_a_repo_declared_head_is_still_judged_by_the_repo(self, tmp_path: Path) -> None:
        """A repo class named like an importable module keeps the repo check: its renamed member is still flagged."""
        p = _write(tmp_path, "pkg/mod.py", "# `json.renamed_method()` `json.method()`\nclass json:\n    def method(self):\n        pass\n")
        out = find_phantom_code_references([p], tmp_path, python_declarations([p]))
        assert out == ["pkg/mod.py:1: `json.renamed_method()` names a member `json` does not declare"]

    def test_an_import_that_raises_anything_counts_as_unresolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pkg = tmp_path / "site"
        _write(pkg, "exploding_mod_c24/__init__.py", "raise SystemExit('no')\n")
        monkeypatch.syspath_prepend(str(pkg))
        p = _write(tmp_path, "pkg/mod.py", "# `exploding_mod_c24.thing.x`\n")
        out = find_phantom_code_references([p], tmp_path, set())
        assert out == ["pkg/mod.py:1: `exploding_mod_c24.thing.x` names nothing declared in this repo"]


def test_baseline_entries_match_on_file_and_name_not_line_or_wording(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", "# see `Ghost()`\n")
    baseline = tmp_path / "_baseline.json"
    baseline.write_text(json.dumps({"phantom_references": ["pkg/mod.py:7: `Ghost()` an older wording of the message"]}))
    assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
    baseline.write_text(json.dumps({"phantom_references": ["pkg/mod.py::Ghost()"]}))
    assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
    # Negative controls: another file, or another name in the same file, is not covered by the entry.
    baseline.write_text(json.dumps({"phantom_references": ["pkg/other.py::Ghost()"]}))
    with pytest.raises(pytest.fail.Exception, match="do not extend the baseline"):
        assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
    baseline.write_text(json.dumps({"phantom_references": ["pkg/mod.py::Phantom()"]}))
    with pytest.raises(pytest.fail.Exception, match="no longer reproduced"):
        assert_no_phantom_code_references([p], tmp_path, set(), baseline_path=baseline)
