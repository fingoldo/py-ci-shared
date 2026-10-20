"""Behavioural tests for ``phantom_code_references``: each check must fire on the shape it guards and stay
quiet on the shapes that look similar but are not claims."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.phantom_code_references import (
    assert_no_count_claim_mismatches,
    assert_no_phantom_code_references,
    assert_no_stale_absolute_line_citations,
    count_claim_mismatches,
    dart_declarations,
    find_phantom_code_references,
    find_stale_absolute_line_citations,
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


_SUBST = (
    "#!/bin/sh\n# Two substitutions:\n#   API_URL - the backend origin\n#   CORE_REF - the pinned core tag,\n#     resolved at build time\n{third}\nsed -e x\n"
)


def test_count_claim_counts_a_bullet_list_in_a_shell_comment(tmp_path: Path) -> None:
    bad = _write(tmp_path, "tool/build.sh", _SUBST.format(third="#   SENTRY_DSN - the error sink"))
    assert count_claim_mismatches([bad]) == [f"{bad}:2: says 2, lists 3"]


def test_count_claim_bullet_list_matching_the_claim_is_clean(tmp_path: Path) -> None:
    good = _write(tmp_path, "tool/build.sh", _SUBST.format(third="echo done"))
    assert count_claim_mismatches([good]) == []


def test_count_claim_docstring_bullets_with_wrapped_items_are_all_counted(tmp_path: Path) -> None:
    # A docstring has no comment marker, so its continuation lines carry their indent as is; reading it after a
    # blanket lstrip counted the first bullet and stopped (autopsia's `tests/_overlay_unwire.py`: "says 3, lists 1").
    text = (
        '"""Helpers.\n\n'
        "Three entry points:\n"
        "- `simulate_unwired(monkeypatch, tmp_path, module)` - unwires a module and\n"
        "  forces a cold re-parse of the merged KB.\n"
        "- `filter_sibling_overlays(...)` - the sibling pass on its own, for a caller\n"
        "  that already has the removed ids.\n"
        "- `cheap_scores_unwired(present, absent, rows_attr)` - the fast path.\n"
        '"""\n'
    )
    good = _write(tmp_path, "tests/_helper.py", text)
    assert count_claim_mismatches([good]) == []
    bad = _write(tmp_path, "tests/_helper2.py", text.replace("Three entry points", "Two entry points"))
    assert count_claim_mismatches([bad]) == [f"{bad}:3: says 2, lists 3"]


def test_count_claim_without_a_list_under_it_is_prose(tmp_path: Path) -> None:
    prose = _write(tmp_path, "Dockerfile", "# Two substitutions:\nRUN echo a\n# API_URL - not under the claim\n")
    spaced = _write(tmp_path, "b.yaml", "# Two substitutions:\n#\n#   API_URL - after a blank comment line\nk: v\n")
    assert count_claim_mismatches([prose, spaced]) == []


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("deploy/app.yaml", "# the three keys:\n# - `host`: where\n# - port: which\nk: v\n"),
        ("lib/a.dart", "// Three modes:\n// * FAST - quick\n// * SLOW - thorough\nvoid f() {}\n"),
        (".githooks/pre-commit", "#!/bin/sh\n# three checks:\n#   LINT - ruff\n#   TYPES - mypy\nexit 0\n"),
        ("a.py", "def f():\n    # Three substitutions:\n    #   A - x\n    #   B - y\n    pass\n"),
    ],
)
def test_count_claim_bullet_shapes_across_languages(tmp_path: Path, rel: str, text: str) -> None:
    path = _write(tmp_path, rel, text)
    assert [v.rsplit(": ", 1)[1] for v in count_claim_mismatches([path])] == ["says 3, lists 2"]


def test_count_claim_numbered_list_still_wins_over_bullets(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.sh", "# the two steps:\n#   1. build\n#   2. push\n")
    assert count_claim_mismatches([path]) == []


def test_a_hash_comment_in_yaml_is_now_a_comment_line_for_phantom_references(tmp_path: Path) -> None:
    path = _write(tmp_path, "ci.yaml", "# runs `GhostRunner` first\nk: v\n")
    assert find_phantom_code_references([path], tmp_path, set()) == ["ci.yaml:1: `GhostRunner` names nothing declared in this repo"]


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


def test_a_stale_absolute_line_citation_of_the_files_own_line_count_is_reported(tmp_path: Path) -> None:
    """The proposal's shape: prose citing a line number past the end of the file it sits in."""
    bad = _write(tmp_path, "backfill.py", "# the retry budget is computed on line 482 of this file\nX = 1\n")
    assert find_stale_absolute_line_citations([bad]) == [f"{bad}:1: cites line 482, but this file has only 2 lines"]
    with pytest.raises(pytest.fail.Exception, match="line 482"):
        assert_no_stale_absolute_line_citations([bad])


def test_a_line_citation_within_the_files_length_is_not_reported(tmp_path: Path) -> None:
    """The nearest correct code: citing a line the file actually has stays clean (a name would still be better)."""
    good = _write(tmp_path, "backfill.py", "# the retry budget is computed on line 2 of this file\nX = 1\n")
    assert find_stale_absolute_line_citations([good]) == []
    assert_no_stale_absolute_line_citations([good])


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
        assert out == ["pkg/mod.py:1: `exploding_mod_c24.thing.x` does not resolve by import"]


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


# --- false-positive classes found on the autopsia consumer tree (2026-10-20). Each rule has a shape that must pass and
# --- a neighbouring real phantom that must still fire, so the rule cannot quietly swallow a genuine finding.


def test_python_protocol_dunder_names_are_not_repo_names(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", '"""Read `__file__` and `__dict__`; a worker re-imports `__main__`."""\n')
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == []
    ghost = _write(tmp_path, "pkg/ghost.py", '"""Calls `__ghost_protocol__` on the object."""\n')
    assert find_phantom_code_references([ghost], tmp_path, python_declarations([ghost])) == [
        "pkg/ghost.py:1: `__ghost_protocol__` names nothing declared in this repo"
    ]


def test_standard_vocabulary_codes_are_not_repo_names(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "pkg/mod.py",
        '"""ICD-10 `E11`, the range `A15-A19`, the concept `C0020538`, the review `CD000097.pub2`, category `K60.1`."""\n',
    )
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == []


def test_os_error_constants_are_not_repo_names(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", '"""An `OSError` with `ENOENT` counts; `ENOTAREALERRNO` does not exist."""\n')
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == ["pkg/mod.py:1: `ENOTAREALERRNO` names nothing declared in this repo"]


def test_external_data_file_names_with_their_own_extensions_are_not_repo_names(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", '"""Reads `eddict2020.dct` and `MRMAP.RRF` from the release."""\n')
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == []


def test_a_bare_attribute_assigned_on_self_is_declared(tmp_path: Path) -> None:
    impl = _write(tmp_path, "pkg/impl.py", "class Client:\n    def call(self):\n        self._last_request_at = 1.0\n")
    doc = _write(tmp_path, "pkg/doc.py", "# the `_last_request_at` stamp is reset on every retry\nx = 1\n")
    assert find_phantom_code_references([impl, doc], tmp_path, python_declarations([impl, doc])) == []
    assert find_phantom_code_references([doc], tmp_path, python_declarations([doc])) == ["pkg/doc.py:1: `_last_request_at` names nothing declared in this repo"]


def test_a_one_character_symbol_is_not_a_repo_name(tmp_path: Path) -> None:
    p = _write(tmp_path, "pkg/mod.py", "# the `Z` term is the scale the reducer factored out\n# the `Frobnicate` term is not\nx = 1\n")
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == ["pkg/mod.py:2: `Frobnicate` names nothing declared in this repo"]


def test_a_name_the_repo_spells_out_as_a_string_is_a_data_name(tmp_path: Path) -> None:
    data = _write(tmp_path, "pkg/fars.py", 'COLUMNS = ["ATESTANTE", "DTOBITO"]\n')
    doc = _write(tmp_path, "pkg/doc.py", '"""The `ATESTANTE` column counts deaths; `MentionedOnlyHere` is a claim."""\nx = 1\n')
    files = [data, doc]
    assert find_phantom_code_references(files, tmp_path, python_declarations(files)) == [
        "pkg/doc.py:1: `MentionedOnlyHere` names nothing declared in this repo"
    ]


def test_table_dot_column_the_repo_builds_in_sql_is_not_a_phantom(tmp_path: Path) -> None:
    sql = _write(tmp_path, "pkg/db.py", 'SCHEMA = "CREATE TABLE fts_progress (last_rowid INTEGER)"\n')
    doc = _write(tmp_path, "pkg/doc.py", "# the bookmark (`fts_progress.last_rowid`) survives a restart\n# and `fts_progress.lost_rowid` does not\nx = 1\n")
    files = [sql, doc]
    assert find_phantom_code_references(files, tmp_path, python_declarations(files)) == [
        "pkg/doc.py:2: `fts_progress.lost_rowid` names nothing declared in this repo"
    ]


def test_a_library_member_of_a_class_the_commented_file_imports_is_not_judged(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "pkg/reader.py",
        "from pandas.io.stata import StataReader\n# the labels come from `StataReader.value_labels()` on the real file\nx = 1\n",
    )
    assert find_phantom_code_references([p], tmp_path, python_declarations([p])) == []
    bare = _write(tmp_path, "pkg/bare.py", "# the labels come from `StataReader.value_labels()` on the real file\nx = 1\n")
    assert find_phantom_code_references([bare], tmp_path, python_declarations([bare])) == [
        "pkg/bare.py:1: `StataReader.value_labels()` names nothing declared in this repo"
    ]


def test_a_string_a_production_module_uses_in_any_role_is_a_data_name(tmp_path: Path) -> None:
    """A comparison, a call argument, a dict key, a subscript key and a table row are all uses the repo makes of a string, so a
    comment naming that string describes the repo's data, not a phantom. Only the never-spelled name is reported."""
    use = _write(tmp_path, "pkg/use.py", 'def f(x, load):\n    return x == "UseOnlyHere" or load("UseOnlyTwo")\n')
    defn = _write(
        tmp_path,
        "pkg/defn.py",
        'REGISTRY = {"DictKeyHere": 1}\nTABLE = ["TableRowHere"]\nrow = {}\nrow["SubscriptKeyHere"] = 2\n',
    )
    doc = _write(
        tmp_path,
        "pkg/doc.py",
        "# `UseOnlyHere`, `UseOnlyTwo`, `DictKeyHere`, `TableRowHere` and `SubscriptKeyHere` are named\n# `NeverSpelledAnywhere` is not\nx = 1\n",
    )
    files = [use, defn, doc]
    assert find_phantom_code_references(files, tmp_path, python_declarations(files)) == [
        "pkg/doc.py:2: `NeverSpelledAnywhere` names nothing declared in this repo"
    ]


def test_a_string_only_a_test_module_uses_is_still_a_phantom(tmp_path: Path) -> None:
    """Test modules never contribute: a fixture string under ``tests/`` is not the repo's data, so a production comment naming it fires."""
    fixture = _write(tmp_path, "tests/test_fixture.py", 'FIXTURE = {"OnlyInTestHere": 1}\n')
    prod = _write(tmp_path, "pkg/prod.py", "# the `OnlyInTestHere` value is what the fixture feeds in\nx = 1\n")
    files = [fixture, prod]
    assert find_phantom_code_references(files, tmp_path, python_declarations(files)) == ["pkg/prod.py:1: `OnlyInTestHere` names nothing declared in this repo"]


def test_known_genuine_phantoms_stay_reported_next_to_longer_spelled_out_strings(tmp_path: Path) -> None:
    """Matching is by whole string: a production literal ``LBD_pipeline`` does not make the comment name ``LBD`` a data name."""
    prod = _write(tmp_path, "pkg/prod.py", 'STAGE = "LBD_pipeline"\nlabel = "_drop_age_excluded_candidatesX"\n')
    doc = _write(
        tmp_path,
        "pkg/doc.py",
        "# `LBD` and `_drop_age_excluded_candidates` were removed; `CantMissCandidate.severity` too\nx = 1\n",
    )
    files = [prod, doc]
    assert find_phantom_code_references(files, tmp_path, python_declarations(files)) == [
        "pkg/doc.py:1: `LBD` names nothing declared in this repo",
        "pkg/doc.py:1: `_drop_age_excluded_candidates` names nothing declared in this repo",
        "pkg/doc.py:1: `CantMissCandidate.severity` names nothing declared in this repo",
    ]
