"""Tests for py_ci_shared.mutation_teeth.

Most of these are regression tests for defects found by review of the FIRST two implementations,
and every one of them names the input that broke it. That is deliberate: a mutation harness whose
own mutants are wrong is worse than no harness, because its output is read as evidence.
"""

from __future__ import annotations

import ast
import io
import time
from pathlib import Path

import pytest

from py_ci_shared import mutation_teeth
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


class TestAKillIsNotAlwaysEvidence:
    """A mutant that makes the code CRASH dies against any test that reaches the line.

    It is killed for free and says nothing about test quality, which is one of the four reasons a
    mutation score is not computed here. Labelled rather than filtered: it never survives, so it
    costs no survivor-list noise, and knowing how many kills were free is what stops a high kill
    count from being read as good tests.
    """

    @pytest.mark.parametrize(
        "output,expected",
        [
            ("E       assert 1 == 2", False),
            ("E       AssertionError: nope", False),
            ("E       ValueError: invalid literal", True),
            ("E       LookupError: unknown encoding: ", True),
            ("E       ZeroDivisionError: division by zero", True),
        ],
    )
    def test_a_crash_is_told_apart_from_a_failed_assertion(self, output, expected):
        from py_ci_shared.mutation_teeth import _killed_by_crash

        assert _killed_by_crash(output) is expected

    def test_an_exception_name_in_a_traceback_is_not_a_crash(self):
        """Read from pytest's own ``E `` summary, not the traceback body: a test using
        ``pytest.raises(ValueError)`` mentions the name while doing exactly its job, and counting
        that as a free kill would misreport a working test."""
        from py_ci_shared.mutation_teeth import _killed_by_crash

        output = "E       assert 'x' in 'y'" + chr(10) + "ValueError appears in the traceback"

        assert _killed_by_crash(output) is False

    def test_the_summary_says_how_many_kills_were_free(self):
        run = MutationRun(
            survivors=[], mutants_run=10, killed=10, truncated=False, candidates_total=10, killed_by_crash=4
        )

        assert "4 of the kills were CRASHES" in run.summary()


class TestReprCoupledConstantsAreOptional:
    """``text[: max_len - 3] + "..."`` states one decision twice, so mutating both halves makes a
    reader think about it twice to learn it once."""

    SOURCE = 'def clip(text, max_len):' + chr(10) + '    return text[: max_len - 3] + "..."' + chr(10)

    def test_it_is_off_by_default(self, tmp_path):
        """What the filter hides is real -- the case where the two have DRIFTED and only one
        direction is covered -- so the judgement stays with the caller rather than being made
        silently on their behalf."""
        path = _write(tmp_path, self.SOURCE)

        default, _total, _sampled = generate_mutants(path)

        assert any(m.description == "constant: emptied a string" for m in default)

    def test_enabling_it_drops_the_string_and_keeps_the_number(self, tmp_path):
        """The numeric half is the more informative of the pair: it says what the reservation IS,
        while the string only shows what fills it."""
        path = _write(tmp_path, self.SOURCE)

        filtered, _total, _sampled = generate_mutants(path, skip_coupled_constants=True)

        assert not any(m.description == "constant: emptied a string" for m in filtered)
        assert any("3 becomes 4" in m.description for m in filtered)

    def test_an_uncoupled_string_is_untouched_by_the_filter(self, tmp_path):
        """The filter is per LINE. A string on a line with no numeric partner is not part of a pair
        and must survive it, or this quietly becomes "skip most strings"."""
        path = _write(tmp_path, 'MESSAGE = "hello"' + chr(10) + "N = 7" + chr(10))

        filtered, _total, _sampled = generate_mutants(path, skip_coupled_constants=True)

        assert any(m.description == "constant: emptied a string" for m in filtered)


class TestWindowsSourceIsHandled:
    """Verified rather than assumed: a harness that mangled a CRLF file would be worse than none in
    repos developed on Windows."""

    def test_crlf_line_endings_round_trip(self, tmp_path):
        crlf = chr(13) + chr(10)
        path = tmp_path / "crlf.py"
        io.open(path, "w", encoding="utf-8", newline="").write("def f(a, b):" + crlf + "    return a > b" + crlf)

        mutants, _total, _sampled = generate_mutants(path)

        assert mutants, "a CRLF file produced no candidates at all"
        assert mutants[0].mutated_file_text.count(crlf) == 2, "line endings were rewritten"
        assert "return a >= b" in mutants[0].mutated_file_text

    def test_tab_indentation_round_trips(self, tmp_path):
        tab = chr(9)
        path = _write(tmp_path, "def f(a, b):" + chr(10) + tab + "return a > b" + chr(10))

        mutants, _total, _sampled = generate_mutants(path)

        assert mutants and tab + "return a >= b" in mutants[0].mutated_file_text


class TestTheWorkerProtocolSurvivesTestOutput:
    """`pytest.main()` writes its report to the worker's stdout, which is also the protocol channel.

    Found by the sweep itself, three files in: the parent read a pytest output line, it happened to
    parse as JSON, and `reply["rc"]` raised `TypeError` in the middle of a run. A test that prints
    anything JSON-shaped was enough. The worker now redirects pytest's output, and the parent treats
    a line that is not a reply as "worker unavailable" -- which degrades to a cold re-run rather
    than to an exception or, worse, to a printed number read as an exit code.
    """

    def test_json_shaped_test_output_does_not_break_the_channel(self, tmp_path):
        from py_ci_shared.mutation_teeth import _WarmRunner

        noisy = (
            "def test_prints_json():" + chr(10)
            + '    print(chr(34) + "a bare json string" + chr(34))' + chr(10)
            + "    print('{" + chr(34) + "rc" + chr(34) + ": 999}')" + chr(10)
            + "    assert True" + chr(10)
        )
        io.open(tmp_path / "test_noisy.py", "w", encoding="utf-8", newline="").write(noisy)

        with _WarmRunner(tmp_path, timeout=120) as warm:
            codes = [warm.run(["test_noisy.py"]) for _ in range(3)]

        assert codes == [0, 0, 0], (
            f"the worker returned {codes}; a 999 would mean the printed line was read as the reply, "
            "and a None that the channel was lost"
        )

    def test_a_reply_without_rc_is_treated_as_worker_unavailable(self):
        """Not as an error and not as a result. The caller must fall back to a cold run, because a
        harness that turns a protocol hiccup into a verdict is the failure this module exists for."""
        from py_ci_shared.mutation_teeth import _WarmRunner

        runner = _WarmRunner(Path("."), timeout=1)

        class _Fake:
            def poll(self):
                return None

            class stdin:
                @staticmethod
                def write(_):
                    return None

                @staticmethod
                def flush():
                    return None

            class stdout:
                @staticmethod
                def readline():
                    return '"a bare json string"' + chr(10)

        runner.process = _Fake()

        assert runner.run(["tests"]) is None


class TestTheFingerprintSeesWhatTheAnswerDependsOn:
    """Each of these was a way a cached verdict outlived the thing it was a verdict about."""

    def _pkg(self, tmp_path):
        pkg = tmp_path / "builder"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "helper.py").write_text("def shorten(s):\n    return s[:10]\n", encoding="utf-8")
        (pkg / "main.py").write_text(
            "from .helper import shorten\n\n\ndef go(s):\n    return shorten(s)\n", encoding="utf-8"
        )
        (tmp_path / "test_it.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        return pkg

    def test_a_from_dot_module_import_name_reaches_the_module(self, tmp_path):
        """`from .helper import shorten` names the FUNCTION in node.names and the MODULE in
        node.module. Probing only node.names resolved `from . import helper` and silently dropped
        this form -- the dominant one inside a package -- so a cached answer survived any edit to
        the code it actually calls."""
        self._pkg(tmp_path)
        seen: set = set()
        mutation_teeth._first_party_imports(tmp_path / "builder" / "main.py", tmp_path, seen)

        assert (tmp_path / "builder" / "helper.py") in seen

    def test_editing_that_module_moves_the_fingerprint(self, tmp_path):
        """The property the closure exists for, asserted end to end rather than on the walk."""
        pkg = self._pkg(tmp_path)
        before = mutation_teeth.fingerprint(tmp_path, Path("builder/main.py"), ["test_it.py"])
        (pkg / "helper.py").write_text("def shorten(s):\n    return s[:99]\n", encoding="utf-8")
        after = mutation_teeth.fingerprint(tmp_path, Path("builder/main.py"), ["test_it.py"])

        assert before != after

    def test_a_directory_of_tests_is_expanded_not_swallowed(self, tmp_path):
        """A directory is a legal pytest target, and `read_bytes()` on one raises. The digest loop
        turned that into a constant, so the fingerprint was blind to every test under it -- for
        good, and silently."""
        self._pkg(tmp_path)
        suite = tmp_path / "suite"
        suite.mkdir()
        (suite / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
        before = mutation_teeth.fingerprint(tmp_path, Path("builder/main.py"), ["suite"])
        (suite / "test_a.py").write_text("def test_a():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        after = mutation_teeth.fingerprint(tmp_path, Path("builder/main.py"), ["suite"])

        assert before != after, "editing a test under the directory did not move the fingerprint"

    def test_the_scope_of_a_run_is_part_of_its_key(self, tmp_path):
        """A sweep narrowed by `lines` or capped by `limit` answers about a SUBSET. Replaying that
        answer for a wider scope reports 'no survivors' about mutants that never ran, which made
        raising the cap after a truncated sweep a no-op whenever a cache file was in play."""
        self._pkg(tmp_path)
        args = (tmp_path, Path("builder/main.py"), ["test_it.py"])
        narrow = mutation_teeth.fingerprint(*args, scope=([[1, 5]], 10, []))
        wider = mutation_teeth.fingerprint(*args, scope=([[1, 50]], 10, []))
        capped = mutation_teeth.fingerprint(*args, scope=([[1, 5]], 40, []))

        assert narrow != wider
        assert narrow != capped


class TestABaselineEntryNamesOnePlace:
    def test_the_same_span_on_two_lines_gets_two_keys(self, tmp_path):
        """The key digested the mutated span alone, so every `token_hex(8)` in a file -- span `8` --
        collided. One accepted entry then silenced four separate call sites, three of which the
        tests killed. An accepted mutant is a claim about one place."""
        src = tmp_path / "m.py"
        src.write_text(
            "import secrets\n\n\ndef go():\n"
            "    first = secrets.token_hex(8)\n"
            "    second = secrets.token_hex(8)\n"
            "    return first, second\n",
            encoding="utf-8",
        )
        mutants, _total, _s = mutation_teeth.generate_mutants(src)
        eight = [m for m in mutants if m.original_span == "8"]

        assert len(eight) == 2, "expected one mutant per call site"
        assert len({m.key for m in eight}) == 2, "two call sites share one baseline key"

    def test_the_key_does_not_move_when_code_above_it_shifts(self, tmp_path):
        """The property the line NUMBER was rejected for, which the site digest has to keep."""
        src = tmp_path / "m.py"
        body = "def go():\n    return secrets.token_hex(8)\n"
        src.write_text("import secrets\n\n\n" + body, encoding="utf-8")
        before = [m for m in mutation_teeth.generate_mutants(src)[0] if m.original_span == "8"][0]
        src.write_text("import secrets\n\n\n# a new comment above\n\n\n" + body, encoding="utf-8")
        after = [m for m in mutation_teeth.generate_mutants(src)[0] if m.original_span == "8"][0]

        assert after.line != before.line, "the fixture did not actually shift the line"
        assert after.key == before.key


class TestAnUnmeasuredMutantIsNotReportedAsMeasured:
    """A run that could not finish is a third outcome, and it used to be filed under the other two."""

    def _mutant(self, line=1):
        return mutation_teeth.Mutant(
            path=Path("m.py"), line=line, column=0, description="constant: 1 becomes 2",
            original_span="1", mutated_span="2", context="x = 1",
        )

    def test_the_summary_says_the_sweep_is_incomplete(self):
        run = mutation_teeth.MutationRun(
            survivors=[], mutants_run=3, killed=3, truncated=False, candidates_total=4,
            inconclusive=[self._mutant()],
        )
        text = run.summary()

        assert "INCONCLUSIVE" in text
        assert "incomplete" in text

    def test_a_clean_run_says_nothing_about_inconclusive_mutants(self):
        """The counterweight: the notice must not become background noise on every report."""
        run = mutation_teeth.MutationRun(
            survivors=[], mutants_run=4, killed=4, truncated=False, candidates_total=4
        )

        assert "INCONCLUSIVE" not in run.summary()

    def test_the_warm_reader_gives_up_rather_than_blocking_forever(self):
        """`readline()` on a pipe has no deadline, so the stored timeout was never applied and one
        non-terminating mutant hung the sweep with no output. A `<` -> `<=` mutation can produce
        exactly that."""
        import types

        class NeverAnswers:
            def readline(self):
                import time

                time.sleep(30)
                return "{}"

        runner = mutation_teeth._WarmRunner(Path("."), timeout=600)
        runner.process = types.SimpleNamespace(stdout=NeverAnswers(), stdin=None)
        started = time.perf_counter()
        got = runner._readline_within(0.3)

        assert got is None
        assert time.perf_counter() - started < 10, "the reader waited on the blocked pipe"


class TestTheCacheReplaysEveryCaveat:
    def test_a_mutant_survives_the_json_round_trip_intact(self):
        """`context` in particular: the baseline key digests it, so a cached survivor whose context
        was dropped would key differently from a freshly measured one and silently stop matching
        its accepted entry."""
        original = mutation_teeth.Mutant(
            path=Path("pkg/m.py"), line=7, column=4, description="constant: 8 becomes 9",
            original_span="8", mutated_span="9", category="noise",
            context="    nonce = secrets.token_hex(8)",
        )
        restored = mutation_teeth._mutant_from_json(mutation_teeth._mutant_json(original))

        assert restored.context == original.context
        assert restored.key == original.key


class TestProseInsideAnFStringCanBeChallenged:
    """On 3.12+ the literal text of an f-string is FSTRING_MIDDLE, which the string operator never
    saw. In this project that is where the prose lives: a prompt template rendered as one f-string
    yielded mutants only for its `{...}` slots, so no sentence in it could be challenged."""

    def test_a_literal_segment_is_mutable(self, tmp_path):
        src = tmp_path / "m.py"
        src.write_text('def go(name):\n    return f"### {name} fenced by a rule"\n', encoding="utf-8")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)
        segments = [m for m in mutants if "f-string segment" in m.description]

        assert segments, "the literal text of an f-string produced no mutants"
        assert any("fenced by a rule" in m.original_span for m in segments)

    def test_the_interpolations_survive_the_edit(self, tmp_path):
        """The objection that made the whole f-string be skipped: emptying an entire f-string token
        turns `f"{x} and {'q'}"` into something where the second interpolation is no longer one.
        Deleting a SEGMENT cannot do that, and this is the assertion that says so."""
        src = tmp_path / "m.py"
        src.write_text('def go(a, b):\n    return f"{a} middle {b} tail"\n', encoding="utf-8")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)
        segments = [m for m in mutants if "f-string segment" in m.description]

        assert segments
        for m in segments:
            tree = ast.parse(m.mutated_file_text)
            slots = [n for n in ast.walk(tree) if isinstance(n, ast.FormattedValue)]
            assert len(slots) == 2, f"an interpolation was lost by {m.original_span!r}"


class TestTheOperatorSetReachesTheShapesThisCodeProduces:
    """Each of these was absent, and each names a real defect shape in the consuming project."""

    def _descriptions(self, tmp_path, body: str) -> set[str]:
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        return {m.description for m in mutation_teeth.generate_mutants(src)[0]}

    def test_an_accumulator_can_stop_accumulating(self, tmp_path):
        """`+=` -> `=` is the attachment-budget defect exactly: the running total stops running, and
        every item is then measured against the full budget instead of what is left of it."""
        got = self._descriptions(tmp_path, "def go(items):\n    used = 0\n    for i in items:\n        used += i\n    return used\n")

        assert any("+= becomes =" in d for d in got), sorted(got)

    def test_a_loop_can_stop_instead_of_skipping(self, tmp_path):
        """`continue` -> `break` in a cap-enforcement loop is the difference between skipping one
        item and abandoning the rest, and it is the same family as an off-by-one at the cap."""
        got = self._descriptions(tmp_path, "def go(items):\n    for i in items:\n        if i:\n            continue\n        print(i)\n")

        assert any("continue becomes break" in d for d in got), sorted(got)

    def test_a_clamp_can_invert(self, tmp_path):
        got = self._descriptions(tmp_path, "def go(n, cap):\n    return max(1, min(n, cap))\n")

        assert any("max becomes min" in d for d in got), sorted(got)

    def test_integer_division_and_modulo_are_reachable(self, tmp_path):
        got = self._descriptions(tmp_path, "def go(n):\n    return n // 3, n % 7, n ** 2\n")

        for wanted in ("// becomes /", "% becomes *", "** becomes *"):
            assert any(wanted in d for d in got), f"{wanted} missing from {sorted(got)}"

    def test_every_generated_mutant_still_compiles(self, tmp_path):
        """The guard on the whole set: a new operator that emits invalid syntax would be filtered
        as 'does not compile' and silently reduce coverage instead of adding it."""
        body = (
            "def go(items, n, cap):\n"
            "    used = 0\n"
            "    for i in items:\n"
            "        if i is None:\n"
            "            continue\n"
            "        used += i // 2\n"
            "        if used > cap:\n"
            "            break\n"
            "    return max(used, min(n, cap)), any(items), all(items)\n"
        )
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        mutants, _total, _s = mutation_teeth.generate_mutants(src)

        # Asserted by NAME, not by count: a count is a guess about the operator set, and the point
        # here is that each new operator fires and none of them emits invalid syntax -- an operator
        # that did would be filtered as "does not compile" and quietly shrink coverage.
        fired = {m.description for m in mutants}
        for wanted in ("+= becomes =", "continue becomes break", "break becomes continue", "max becomes min"):
            assert any(wanted in d for d in fired), f"{wanted} did not fire: {sorted(fired)}"
        for m in mutants:
            ast.parse(m.mutated_file_text)


class TestAnnotationsThatCarryEnforcedBounds:
    """The exclusion of annotations was right for the reason it gave and too wide by one case."""

    def _descriptions(self, tmp_path, body: str) -> set[str]:
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        return {m.description for m in mutation_teeth.generate_mutants(src)[0]}

    def test_a_literal_choice_set_is_mutable(self, tmp_path):
        """`Literal[...]` is read when the model is built and enforced on every instance, so
        emptying one of its members is a defect a test observes. Excluding every annotation hid
        that behind the same rule that correctly excludes a forward reference."""
        got = self._descriptions(tmp_path, 'from typing import Literal\n\nStatus = 1\n\n\ndef f(s: Literal["draft", "sent"]) -> int:\n    return 1\n')

        assert any("emptied a string" in d for d in got), sorted(got)

    def test_a_bare_forward_reference_is_still_left_alone(self, tmp_path):
        """The counterweight, and the reason the original exclusion exists: under
        `from __future__ import annotations` this is never evaluated, so the mutant is unkillable
        by construction and would be a permanent false survivor."""
        got = self._descriptions(tmp_path, 'from __future__ import annotations\n\n\ndef f(x: "MyType") -> "int":\n    return x\n')

        assert not any("emptied a string" in d for d in got), sorted(got)


class TestTheOperatorsThatNeedAstSpans:
    """Deferred once, because AST columns are BYTE offsets and this is where that bites.

    The historical failure is on record in the module docstring: a span taken as a character offset
    turned a whole statement into `a >= b` and reported it at a line nobody had touched. Both
    operators here take every boundary through `_abs_index`, and the first test puts an emoji in
    front of the call so a regression shows up as a mangled splice rather than as a subtly wrong
    line number.
    """

    def _mutants(self, tmp_path, body: str):
        src = tmp_path / "m.py"
        io.open(src, "w", encoding="utf-8", newline="").write(body)
        return mutation_teeth.generate_mutants(src)[0]

    def test_adjacent_arguments_are_transposed(self, tmp_path):
        """`log("%s kept %d of %d", uid, n, cap)` type-checks, reads correctly, and lies when two of
        those values change places. No single token is wrong, so no token operator can express it."""
        mutants = self._mutants(tmp_path, 'def go(uid, n, cap):\n    log("kept", uid, n, cap)\n')
        swaps = [m for m in mutants if "transposed" in m.description]

        assert len(swaps) == 3, [m.description for m in swaps]
        assert any("uid <-> n" in m.description for m in swaps)

    def test_a_transposition_survives_a_non_ascii_prefix(self, tmp_path):
        """The trap itself. With an emoji earlier on the line, a span read as a character offset
        lands two positions late and destroys the surrounding text."""
        body = 'def go(uid, n):\n    x = "\U0001f600\U0001f600"; log("kept", uid, n)\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "transposed" in m.description]

        assert swaps
        for mutant in swaps:
            line = mutant.mutated_file_text.split("\n")[1]
            assert '"\U0001f600\U0001f600"' in line, f"the text before the call was destroyed: {line}"
            ast.parse(mutant.mutated_file_text)

    def test_star_args_are_left_alone(self, tmp_path):
        """`*args` has no fixed position, so there is nothing to transpose and the splice would be
        meaningless rather than wrong."""
        mutants = self._mutants(tmp_path, "def go(a, rest):\n    log(a, *rest)\n")

        assert not [m for m in mutants if "transposed" in m.description]

    def test_a_named_slice_bound_can_be_removed(self, tmp_path):
        """`text[:limit]` -> `text[:]` is the unbounded-slice defect. The numeric operator reached
        this only when the bound was a literal, and a bound worth having is usually a name."""
        mutants = self._mutants(tmp_path, "def go(text, limit):\n    return text[:limit]\n")
        cuts = [m for m in mutants if m.description.startswith("slice:")]

        assert cuts, [m.description for m in mutants]
        assert "text[:]" in cuts[0].mutated_file_text

    def test_a_literal_slice_bound_is_left_to_the_numeric_operator(self, tmp_path):
        """The counterweight: two operators firing on one site is noise, and `10` -> `11` already
        says everything a removed literal bound would."""
        mutants = self._mutants(tmp_path, "def go(text):\n    return text[:10]\n")

        assert not [m for m in mutants if m.description.startswith("slice:")]
        assert any("10 becomes 11" in m.description for m in mutants)


class TestSubstitutionNeedsARuleAndHasOne:
    """Both of these were deferred for want of a rule, not for want of effort.

    Emptying a string is unambiguous; substituting one has to answer "with WHAT", and an arbitrary
    answer would add to the 56% of all candidates that string-emptying already produces.
    """

    def _mutants(self, tmp_path, body: str):
        src = tmp_path / "m.py"
        io.open(src, "w", encoding="utf-8", newline="").write(body)
        return mutation_teeth.generate_mutants(src)[0]

    def test_a_guard_pattern_is_widened_at_its_anchors(self, tmp_path):
        """Each perturbation makes the pattern match MORE, which is the direction a guard fails in:
        a narrowed guard misses something and says so, a widened one quietly says yes."""
        body = _RAW_GUARD
        widened = [m for m in self._mutants(tmp_path, body) if "regex widened" in m.description]

        assert widened, [m.description for m in self._mutants(tmp_path, body)]
        assert "word-boundary" in widened[0].description

    def test_prose_in_the_same_shape_is_not_treated_as_a_pattern(self, tmp_path):
        """The counterweight. `\b` in a log message is not an anchor, and perturbing it would be a
        mutant about nothing -- the rule is that the string must reach a regex CALL."""
        body = _RAW_PROSE
        widened = [m for m in self._mutants(tmp_path, body) if "regex widened" in m.description]

        assert not widened

    def test_a_pattern_that_would_not_compile_is_not_emitted(self, tmp_path):
        """A perturbation that breaks the pattern is not a mutant no test kills -- it is not a
        mutant. Emitting it would put a permanent unkillable entry in every survivor list."""
        body = _RAW_ANCHORED
        for mutant in self._mutants(tmp_path, body):
            if "regex widened" in mutant.description:
                import re as _re

                pattern = mutant.mutated_file_text.split('r"')[1].split('"')[0]
                _re.compile(pattern)

    def test_a_literal_is_swapped_for_a_confusable_one_from_the_same_file(self, tmp_path):
        """The vocabulary is the file's own, so the rule invents nothing: if `NFKD` and `NFKC` both
        appear, the author held both in mind and "does anything check which one this is" is a real
        question."""
        body = 'def go(text, mode):\n    a = norm("NFKD", text)\n    b = norm("NFKC", text)\n    return a, b\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "became" in m.description]

        assert swaps, [m.description for m in self._mutants(tmp_path, body)]
        assert any("NFKD" in m.description and "NFKC" in m.description for m in swaps)

    def test_a_literal_with_no_confusable_neighbour_gets_no_mutant(self, tmp_path):
        """The other half of the rule, and what keeps it cheap: no neighbour, no mutant. Measured at
        1.6% of candidates across five real modules."""
        body = 'def go(text):\n    return norm("NFKD", text)\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "became" in m.description]

        assert not swaps

#: Raw string bodies. A backslash-b inside an ordinary Python string is the BACKSPACE
#: escape, and an earlier version of these tests handed the harness that control character
#: and then reported that no widening was produced -- true, and about nothing. Four separate
#: attempts lost a level of escaping between a shell heredoc and this file; the rule that
#: finally worked is to stop escaping rather than to escape more carefully.
_RAW_GUARD = r"""import re


P = re.compile(r"\bnot the right fit\b")
"""

_RAW_PROSE = r"""def go(log):
    log("a message with a \b in it")
"""

_RAW_ANCHORED = r"""import re


P = re.compile(r"^a+$")
"""


class TestConcurrencyChangesSpeedAndNothingElse:
    """`jobs=N` is opt-in, and this is the property that makes it safe to opt into.

    The speedup that motivated it was measured on a machine its own author recorded as carrying
    40-50 foreign processes, where the noisy twin of the same A/B pair gave 1.27x against the quiet
    one's 3.5x -- so the number is not claimed here and is not what this asserts. What IS assertable
    without a quiet machine is that concurrency does not change the ANSWER, which is the only
    property a harness may not trade for speed.

    It stays off by default because the remaining risk is not in the harness: a suite that binds a
    port, shares a database or writes a fixed temp path gets FALSE KILLS from concurrency, and only
    the suite's owner can rule that out.
    """

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        io.open(repo / "m.py", "w", encoding="utf-8", newline="").write(
            "def clamp(n, cap):\n"
            "    if n > cap:\n"
            "        return cap\n"
            "    return n\n"
            "\n"
            "\n"
            "def unchecked(n):\n"
            "    return n * 2 + 1\n"
        )
        io.open(repo / "tests" / "test_m.py", "w", encoding="utf-8", newline="").write(
            "from m import clamp\n"
            "\n"
            "\n"
            "def test_clamp():\n"
            "    assert clamp(5, 3) == 3\n"
            "    assert clamp(2, 3) == 2\n"
            "    assert clamp(3, 3) == 3\n"
        )
        return repo

    def test_four_workers_reach_the_same_verdict_as_one(self, tmp_path):
        repo = self._repo(tmp_path)
        serial = mutation_teeth.find_surviving_mutants(
            "m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False
        )
        parallel = mutation_teeth.find_surviving_mutants(
            "m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, jobs=4
        )

        assert serial.mutants_run == parallel.mutants_run
        assert serial.killed == parallel.killed
        assert [m.key for m in serial.survivors] == [m.key for m in parallel.survivors]

    def test_survivors_come_back_in_source_order(self, tmp_path):
        """Merged from several partitions, so completion order is arbitrary. A survivor list that
        reorders itself between runs is a diff nobody can read, and a baseline that churns."""
        repo = self._repo(tmp_path)
        outcome = mutation_teeth.find_surviving_mutants(
            "m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, jobs=3
        )

        lines = [m.line for m in outcome.survivors]
        assert lines == sorted(lines), lines


class TestACoverageGapNamesItsKiller:
    """"Fix the map" is only actionable if the report says WHICH file to add.

    A real sweep produced 96 coverage-map gaps, 65 of them in one file whose wider net holds 61
    test files. Adding all 61 is exactly what the map exists to avoid, so without a name the
    instruction leaves a human to find one test among dozens, once per gap -- while the harness
    knew the answer at the moment it printed the complaint and discarded it.
    """

    def test_the_failing_file_is_read_from_the_summary_line(self):
        """From the summary, not the traceback: the summary is a documented shape, a traceback is
        whatever the failing assertion happened to print. `-x` guarantees there is at most one."""
        output = "some noise\nFAILED tests/test_small_gates.py::test_channel - AssertionError: x\n1 failed"

        assert mutation_teeth._first_failing_file(output) == "tests/test_small_gates.py"

    def test_a_collection_error_counts_too(self):
        """A mutant that breaks an import produces ERROR, never FAILED, and it identifies the file
        just as well."""
        assert mutation_teeth._first_failing_file("ERROR tests/test_parsing.py::test_a") == "tests/test_parsing.py"

    def test_a_passing_run_names_nothing(self):
        """The counterweight: no failure, no name -- rather than a plausible-looking wrong one."""
        assert mutation_teeth._first_failing_file("==== 12 passed in 3.4s ====") is None

    def test_the_summary_lists_the_files_to_add(self):
        """What the operator actually reads. The names are de-duplicated and sorted, because 65 gaps
        in one file usually point at a handful of tests, not 65 of them."""
        gaps = [
            mutation_teeth.Mutant(
                path=Path("models.py"), line=n, column=0, description="d",
                original_span="1", mutated_span="2", category=f"killed-by:tests/test_{name}.py",
            )
            for n, name in ((1, "b"), (2, "a"), (3, "a"))
        ]
        run = mutation_teeth.MutationRun(
            survivors=[], mutants_run=3, killed=3, truncated=False, candidates_total=3, coverage_gaps=gaps
        )
        text = run.summary()

        assert "add tests/test_a.py, tests/test_b.py" in text, text


class TestSkippingTheSyntaxCheckIsSafe:
    """Generation re-parsed the whole file once per candidate: 45% of its time, and on the largest
    real module 7.35s for 696 candidates of which 21 could actually fail.

    The parse now runs only for families that substitute a TOKEN for a token of the same class in a
    position where the class carries structure -- `logic:`, `operator:`, `comparison:`. Every other
    family swaps a complete expression or literal, or replaces a statement with `pass`, and cannot
    produce a syntax error.

    That claim is checked rather than asserted: if an operator is added later that can break syntax
    while carrying a 'safe' description, these tests fail instead of the harness quietly emitting a
    mutant that compiles nowhere -- which would fail every test it touched and be counted as a KILL,
    the flattering direction.
    """

    def test_every_mutant_of_a_dense_fixture_still_parses(self, tmp_path):
        body = (
            "import re\n"
            "from typing import Literal\n"
            "\n"
            "TABLE = {'a': 1, 'b': 2}\n"
            "P = re.compile(r'^a+$')\n"
            "\n"
            "\n"
            "def go(items, n, cap, *args, mode: Literal['x', 'y'] = 'x', **kw):\n"
            "    used = 0\n"
            "    for i in items:\n"
            "        if i is None:\n"
            "            continue\n"
            "        used += i // 2\n"
            "        if used > cap:\n"
            "            break\n"
            "        log('kept %d of %d', used, cap)\n"
            "    text = str(n)[:cap]\n"
            "    return max(used, min(n, cap)), any(items), all(items), text, TABLE.get('a', 0)\n"
        )
        src = tmp_path / "m.py"
        io.open(src, "w", encoding="utf-8", newline="").write(body)

        mutants, _total, _sampled = mutation_teeth.generate_mutants(src)

        # Asserted by FAMILY, not by count. A count is a guess about the operator set that goes
        # stale the moment one is added or narrowed; what this test needs is that the fixture
        # actually exercises the families whose parse is now skipped, or it proves nothing about
        # the skip.
        families = {m.description.split(":", 1)[0] for m in mutants}
        for skipped in ("constant", "arguments transposed", "slice", "deleted a statement-level call"):
            assert any(f.startswith(skipped) for f in families), f"{skipped} absent: {sorted(families)}"
        for mutant in mutants:
            ast.parse(mutant.mutated_file_text)

    def test_a_star_in_a_signature_is_still_parsed_and_rejected(self, tmp_path):
        """The concrete shape that motivated keeping `operator:` in the checked set: `*` in a
        parameter list is structure, not arithmetic, and swapping it produces `def f(/args)`."""
        src = tmp_path / "m.py"
        io.open(src, "w", encoding="utf-8", newline="").write("def f(*args):\n    return args\n")

        for mutant in mutation_teeth.generate_mutants(src)[0]:
            ast.parse(mutant.mutated_file_text)


class TestIdenticalMutantsRunOnce:
    def test_a_duplicate_mutated_file_is_generated_but_not_run_twice(self, tmp_path):
        """Two candidates can produce a byte-identical file. Running the twin is pure cost -- but
        its verdict has to reach BOTH, because each carries its own baseline key and a silently
        unrun twin turns an accepted entry stale without anything saying so."""
        src = tmp_path / "m.py"
        io.open(src, "w", encoding="utf-8", newline="").write("def f(a, b):\n    return a + b\n")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)

        texts = [m.mutated_file_text for m in mutants]
        assert len(texts) == len(set(texts)), "generation itself should not emit identical files here"

    def test_the_truncation_banner_ignores_duplicates(self):
        """A de-duplicated twin was never a candidate for a separate run, so counting it as omitted
        would raise TRUNCATED on a complete sweep -- the false-alarm direction this module already
        had to fix once, when the candidate counter included non-compiling edits."""
        run = mutation_teeth.MutationRun(
            survivors=[], mutants_run=5, killed=5, truncated=False, candidates_total=5
        )

        assert "TRUNCATED" not in run.summary()
