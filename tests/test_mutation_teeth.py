"""Tests for py_ci_shared.mutation_teeth.

Most of these are regression tests for defects found by review of the FIRST two implementations,
and every one of them names the input that broke it. That is deliberate: a mutation harness whose
own mutants are wrong is worse than no harness, because its output is read as evidence.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from py_ci_shared.mutation_teeth import (
    HARNESS_VERSION,
    Mutant,
    MutationRun,
    fingerprint,
    generate_mutants,
)


def _write(tmp_path: Path, text: str, name: str = "subject.py") -> Path:
    path = tmp_path / name
    io.open(path, "w", encoding="utf-8", newline="").write(text)
    return path


def _descriptions(path: Path) -> list[str]:
    mutants, _total, _sampled = generate_mutants(path)
    return [m.description for m in mutants]


class TestTheSpliceLandsWhereItShould:
    """`col_offset` is a UTF-8 BYTE offset and the source is a `str`.

    Slicing the string by that number put the edit in the wrong place whenever a non-ASCII character
    appeared earlier on the same line. On the verified input below the previous implementation
    replaced the entire statement with `a >= b` and reported it at a line it had not touched -- a
    false accusation, not noise. Non-ASCII is not exotic here: em dashes and Cyrillic appear
    throughout the repos this package serves.
    """

    @pytest.mark.parametrize(
        "prefix",
        ['x = "\U0001F600\U0001F600"; ', 'x = "——"; ', "т = 1; "],
        ids=["emoji", "em-dashes", "cyrillic-identifier"],
    )
    def test_non_ascii_earlier_on_the_line_does_not_move_the_edit(self, tmp_path, prefix):
        path = _write(tmp_path, f"def f(a, b):\n    {prefix}return a > b\n")

        mutants, _total, _sampled = generate_mutants(path)
        comparisons = [m for m in mutants if m.description.startswith("operator: >")]

        assert comparisons, "the comparison was not found at all"
        line = comparisons[0].mutated_file_text.split("\n")[1]
        assert line.strip().endswith("return a >= b"), line
        assert prefix.strip() in line, "the text before the mutation was destroyed"

    def test_comments_inside_a_multi_line_expression_survive(self):
        """The module's headline claim. It was FALSE for every multi-line node while mutants were
        produced by re-rendering an AST node, which discards comments and reformats."""
        source = "def f(a, b):\n    return (\n        a  # keep me\n        > b  # and me\n    )\n"
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), source)
            mutants, _total, _sampled = generate_mutants(path)
            assert mutants
            text = mutants[0].mutated_file_text
            assert "# keep me" in text and "# and me" in text
            changed = [
                (a, b)
                for a, b in zip(source.split("\n"), text.split("\n"))
                if a != b
            ]
            assert len(changed) == 1, f"a single-token mutation changed {len(changed)} lines"


class TestLineNumberingMatchesThePythonTokenizer:
    def test_a_form_feed_in_a_literal_does_not_erase_every_candidate(self, tmp_path):
        """`str.splitlines()` breaks on \\x0c, \\x85 and \\u2028; the tokenizer does not. A file with
        any of them desynchronised line numbers and produced ZERO candidates -- which reads exactly
        like "there is nothing here to check"."""
        path = _write(tmp_path, 'S = "a\x0cb"\ndef f(a, b):\n    return a > b\n')

        mutants, total, _sampled = generate_mutants(path)

        assert total > 0 and mutants, "a control character in a literal silenced the whole file"


class TestOperatorCoverage:
    def test_an_awaited_call_is_deletable(self, tmp_path):
        """The flagship operator -- "the guard exists and nothing invokes it" -- matched only
        `Expr(Call)` and was therefore blind on every async call site."""
        path = _write(tmp_path, "async def f(g):\n    await g()\n    return 1\n")

        assert "deleted a statement-level call" in _descriptions(path)

    def test_floats_are_mutated(self, tmp_path):
        """A threshold is exactly what a test should pin, and one of the four cases that motivated
        this module was a threshold test. Integers alone left them uncovered."""
        path = _write(tmp_path, "THRESH = 0.5\ndef f(x):\n    return x > 0.5\n")

        assert any(d.startswith("constant: 0.5 becomes") for d in _descriptions(path))

    def test_arithmetic_operators_are_mutated(self, tmp_path):
        path = _write(tmp_path, "def f(a, b):\n    return a + b\n")

        assert "operator: + becomes -" in _descriptions(path)

    def test_is_not_collapses_to_is_as_one_change(self, tmp_path):
        path = _write(tmp_path, "def f(a):\n    return a is not None\n")
        mutants, _t, _s = generate_mutants(path)

        collapsed = [m for m in mutants if m.description == "comparison: is not becomes is"]
        assert collapsed and "a is None" in collapsed[0].mutated_file_text

    def test_a_boolean_chain_flips_one_operator_at_a_time(self, tmp_path):
        """`a and b and c` used to become `a or b or c` in a single mutant, which is not a
        single-change mutation and does not localise the failure."""
        path = _write(tmp_path, "def f(a, b, c):\n    return a and b and c\n")
        mutants, _t, _s = generate_mutants(path)

        flips = [m for m in mutants if m.description == "logic: and becomes or"]
        assert len(flips) == 2
        for mutant in flips:
            assert mutant.mutated_file_text.count(" or ") == 1


class TestUnobservableConstructsAreNotMutated:
    """Each of these is provably equivalent, so it would be permanent noise in a survivor list."""

    def test_docstrings_are_left_alone(self, tmp_path):
        path = _write(tmp_path, '"""Module doc."""\n\n\ndef f():\n    """Function doc."""\n    return 1\n')

        assert "constant: emptied a string" not in _descriptions(path)

    def test_a_bare_string_statement_is_left_alone(self, tmp_path):
        """Not only the FIRST string in a body: a section banner or a PEP-258 attribute docstring is
        as unobservable as a real docstring."""
        path = _write(tmp_path, "def f():\n    x = 1\n    'a bare string used as a comment'\n    return x\n")

        assert "constant: emptied a string" not in _descriptions(path)

    def test_dunder_all_entries_are_left_alone(self, tmp_path):
        """18 of 293 candidates on the measured sample, eight of them filling the first slots of a
        truncated run. The cost is the ability to catch a test asserting the public surface, which
        is inventory and already covered by `phantom_code_references`."""
        path = _write(tmp_path, '__all__ = ["a", "b"]\n\n\ndef f():\n    return 1\n')

        assert "constant: emptied a string" not in _descriptions(path)

    def test_annotations_are_left_alone(self, tmp_path):
        """With `from __future__ import annotations` they are never evaluated, so a mutated
        forward-reference string is provably unkillable."""
        path = _write(tmp_path, 'def f(x: "MyType") -> "int":\n    return x\n')

        assert "constant: emptied a string" not in _descriptions(path)

    def test_f_strings_are_left_alone(self, tmp_path):
        """Emptying one literal part of an f-string turns the rest into concatenated literals where
        an interpolation stops being one -- a mutant labelled "emptied a string" that is not."""
        path = _write(tmp_path, 'x = 1\nS = f"{x} and more"\n')

        assert "constant: emptied a string" not in _descriptions(path)


class TestRegexFragmentsAreMutatedOnPurpose:
    def test_a_regex_fragment_is_a_candidate(self, tmp_path):
        """Measured counter-finding: emptying a regex fragment is the STRONGEST string mutation, not
        noise. `re.compile("").search("abc")` matches at position 0, so a selective guard becomes an
        always-fires guard -- a behaviour change any adequate test must catch."""
        path = _write(tmp_path, 'import re\nPAT = re.compile(r"\\bword\\b")\n')

        assert "constant: emptied a string" in _descriptions(path)


class TestLimitAndScope:
    def test_truncation_is_reported_rather_than_silent(self, tmp_path):
        """"No survivors" from a truncated run used to be indistinguishable from "no survivors" from
        a complete one."""
        path = _write(tmp_path, "def f(a, b):\n" + "".join(f"    x{i} = a > b\n" for i in range(10)))

        mutants, total, _sampled = generate_mutants(path, limit=3)
        run = MutationRun(survivors=[], mutants_run=3, killed=3, truncated=total > len(mutants), candidates_total=total)

        assert len(mutants) == 3 and total > 3
        assert run.truncated and "TRUNCATED" in run.summary()

    def test_candidates_are_returned_in_source_order(self, tmp_path):
        """`ast.walk` is breadth-first, so truncating its output covered one line near the end of a
        132-candidate file and never reached whole functions."""
        path = _write(tmp_path, "def f(a, b):\n" + "".join(f"    x{i} = a > b\n" for i in range(6)))

        mutants, _t, _s = generate_mutants(path)
        lines = [m.line for m in mutants]

        assert lines == sorted(lines)

    def test_a_node_spanning_into_the_range_is_in_scope(self, tmp_path):
        """`lines` used to test the START line only, so an expression beginning above the changed
        hunk and extending into it -- the expression the commit edited -- was skipped."""
        path = _write(tmp_path, "def f(a, b):\n    return (\n        a\n        > b\n    )\n")

        mutants, _t, _s = generate_mutants(path, lines=range(4, 5))

        assert any(m.description.startswith("operator: >") for m in mutants)


class TestFingerprintCoversTheImportClosure:
    """A cache keyed on the file and its tests alone misses the case that matters most: the function
    under test is unchanged while a function it CALLS is not."""

    def test_changing_an_imported_module_changes_the_fingerprint(self, tmp_path):
        (tmp_path / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (tmp_path / "subject.py").write_text("from helper import helper\n\n\ndef f():\n    return helper()\n", encoding="utf-8")
        (tmp_path / "test_subject.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        before = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        (tmp_path / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
        after = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])

        assert before != after, "a change to a CALLED function left the fingerprint unchanged"

    def test_an_unrelated_file_does_not_change_the_fingerprint(self, tmp_path):
        """The counterweight: a fingerprint that changed on everything would never hit, and the
        cache would cost time instead of saving it."""
        (tmp_path / "subject.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "test_subject.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (tmp_path / "unrelated.py").write_text("x = 1\n", encoding="utf-8")

        before = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        (tmp_path / "unrelated.py").write_text("x = 2\n", encoding="utf-8")

        assert before == fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])

    def test_a_data_file_only_counts_when_named(self, tmp_path):
        """The honest limit. A prompt in a `.txt` changes behaviour without touching any `.py`, and
        no static analysis finds it -- which is why `extra_fingerprint_paths` exists and why the
        docstring says a hit means "nothing statically reachable changed"."""
        (tmp_path / "subject.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "test_subject.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (tmp_path / "prompt.txt").write_text("v1", encoding="utf-8")

        unnamed_before = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        named_before = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"], ["prompt.txt"])
        (tmp_path / "prompt.txt").write_text("v2", encoding="utf-8")

        assert unnamed_before == fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        assert named_before != fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"], ["prompt.txt"])

    def test_the_harness_version_is_part_of_the_key(self, tmp_path):
        """Adding an operator must invalidate every cached result, or the tool keeps reporting the
        survivor list of a harness that no longer exists."""
        (tmp_path / "subject.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "test_subject.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        import py_ci_shared.mutation_teeth as module

        before = fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        module.HARNESS_VERSION = HARNESS_VERSION + "-next"
        try:
            assert before != fingerprint(tmp_path, Path("subject.py"), ["test_subject.py"])
        finally:
            module.HARNESS_VERSION = HARNESS_VERSION


class TestMutantIdentity:
    def test_the_key_survives_an_edit_elsewhere_in_the_file(self):
        """Keyed on the SPAN's digest, not the line number. `code_audit_meta` records that a
        line-number key produces a false all-clear the moment anything above it shifts."""
        first = Mutant(Path("m.py"), 10, 4, "operator: > becomes >=", "a > b", "a >= b")
        moved = Mutant(Path("m.py"), 42, 4, "operator: > becomes >=", "a > b", "a >= b")

        assert first.key == moved.key

    def test_two_mutations_on_one_line_are_distinguishable(self):
        """`a < b < c` produced two mutants whose printed form was byte-identical, so a reader could
        not tell which one survived."""
        left = Mutant(Path("m.py"), 3, 11, "operator: < becomes <=", "<", "<=")
        right = Mutant(Path("m.py"), 3, 17, "operator: < becomes <=", "<", "<=")

        assert str(left) != str(right)

    def test_the_path_is_repo_relative(self):
        """An absolute path in a baseline key matches on one machine and silently accepts everything
        everywhere else."""
        mutant = Mutant(Path("pkg/m.py"), 1, 0, "operator: > becomes >=", "a > b", "a >= b")

        assert not Path(mutant.key.split("::")[0]).is_absolute()
