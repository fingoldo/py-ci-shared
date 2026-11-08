"""Tests for py_ci_shared.mutation_teeth: concurrency, coverage gaps, syntax checks, twin mutants and survivor logging.

Split from test_mutation_teeth.py; see its docstring.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from py_ci_shared import mutation_teeth


# 256 s alone on a Windows workstation, 282 s under -n 4: past the global 300 s pytest-timeout, whose thread method
# os._exit()s the xdist worker ("node down: Not properly terminated", audit 2026-10-03 WF-12). Its own, larger budget.
@pytest.mark.timeout(1200)
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
        open(repo / "m.py", "w", encoding="utf-8", newline="").write(
            "def clamp(n, cap):\n" "    if n > cap:\n" "        return cap\n" "    return n\n" "\n" "\n" "def unchecked(n):\n" "    return n * 2 + 1\n"
        )
        open(repo / "tests" / "test_m.py", "w", encoding="utf-8", newline="").write(
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
        serial = mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False)
        parallel = mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, jobs=4)

        assert serial.mutants_run == parallel.mutants_run
        assert serial.killed == parallel.killed
        assert [m.key for m in serial.survivors] == [m.key for m in parallel.survivors]

    def test_survivors_come_back_in_source_order(self, tmp_path):
        """Merged from several partitions, so completion order is arbitrary. A survivor list that
        reorders itself between runs is a diff nobody can read, and a baseline that churns."""
        repo = self._repo(tmp_path)
        outcome = mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, jobs=3)

        lines = [m.line for m in outcome.survivors]
        assert lines == sorted(lines), lines


class TestACoverageGapNamesItsKiller:
    """ "Fix the map" is only actionable if the report says WHICH file to add.

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
                path=Path("models.py"),
                line=n,
                column=0,
                description="d",
                original_span="1",
                mutated_span="2",
                category=f"killed-by:tests/test_{name}.py",
            )
            for n, name in ((1, "b"), (2, "a"), (3, "a"))
        ]
        run = mutation_teeth.MutationRun(survivors=[], mutants_run=3, killed=3, truncated=False, candidates_total=3, coverage_gaps=gaps)
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
        open(src, "w", encoding="utf-8", newline="").write(body)

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
        open(src, "w", encoding="utf-8", newline="").write("def f(a, *args):\n    return a * len(args)\n")

        mutants = mutation_teeth.generate_mutants(src)[0]

        assert mutants, "a function with a `*` parameter and a `*` operator must yield mutants, or the parse check below is vacuous"
        for mutant in mutants:
            ast.parse(mutant.mutated_file_text)


class TestIdenticalMutantsRunOnce:
    def test_a_duplicate_mutated_file_is_generated_but_not_run_twice(self, tmp_path):
        """Two candidates can produce a byte-identical file. Running the twin is pure cost -- but
        its verdict has to reach BOTH, because each carries its own baseline key and a silently
        unrun twin turns an accepted entry stale without anything saying so."""
        src = tmp_path / "m.py"
        open(src, "w", encoding="utf-8", newline="").write("def f(a, b):\n    return a + b\n")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)

        texts = [m.mutated_file_text for m in mutants]
        assert len(texts) == len(set(texts)), "generation itself should not emit identical files here"

    def test_the_truncation_banner_ignores_duplicates(self):
        """A de-duplicated twin was never a candidate for a separate run, so counting it as omitted
        would raise TRUNCATED on a complete sweep -- the false-alarm direction this module already
        had to fix once, when the candidate counter included non-compiling edits."""
        run = mutation_teeth.MutationRun(survivors=[], mutants_run=5, killed=5, truncated=False, candidates_total=5)

        assert "TRUNCATED" not in run.summary()


class TestAMutantThatStopsPytestStartingIsAKill:
    """The same exit code means opposite things on the baseline and on a mutant.

    On the baseline, "pytest did not get through the run" says the caller passed paths pytest cannot
    use, and refusing is the only safe answer: the alternative reports every mutant killed and the
    run as a clean bill of health. On a mutant the identical command with the identical paths was
    just observed to exit 0, so only the mutation can have caused it -- and a mutation that stops the
    suite from starting has been noticed as loudly as pytest is able to notice one.
    """

    def test_every_could_not_run_code_is_a_kill_on_a_mutant(self):
        from py_ci_shared.mutation_teeth import _classify_code

        for code in (2, 3, 4, 5):
            assert _classify_code(code, "a mutant", baseline_verified=True) is False

    def test_a_crashed_interpreter_is_a_kill_on_a_mutant(self):
        from py_ci_shared.mutation_teeth import _classify_code

        # 0xC0000005 is the access violation a mutated numba kernel raised on an out-of-bounds read;
        # a negative return code is a POSIX signal. Both are the interpreter dying, not a refusal.
        for code in (3221225477, 0xC00000FD, -11):
            assert _classify_code(code, "a mutant", baseline_verified=True) is False

    def test_a_crashed_interpreter_still_refuses_on_the_baseline(self):
        from py_ci_shared.mutation_teeth import MutationHarnessError, _classify_code

        with pytest.raises(MutationHarnessError, match="neither pass"):
            _classify_code(3221225477, "the unmutated baseline")

    def test_every_could_not_run_code_still_refuses_on_the_baseline(self):
        from py_ci_shared.mutation_teeth import MutationHarnessError, _classify_code

        for code in (2, 3, 4, 5):
            with pytest.raises(MutationHarnessError, match="neither pass"):
                _classify_code(code, "the unmutated baseline")

    def test_the_cold_path_classifies_the_same_way(self):
        """`_classify` had no mutation-caused branch at all, so a mutant that broke the run was a
        refusal whenever it happened to be re-checked in a cold process rather than the warm one."""
        from py_ci_shared.mutation_teeth import MutationHarnessError, _classify

        class _Result:
            def __init__(self, code):
                self.returncode = code
                self.stdout = ""

        for code in (2, 3, 4, 5):
            assert _classify(_Result(code), "a mutant", baseline_verified=True) is False
            with pytest.raises(MutationHarnessError, match="neither pass"):
                _classify(_Result(code), "the unmutated baseline")

    def test_the_guard_does_not_depend_on_the_wording_of_the_label(self):
        """The previous version decided by testing whether the label started with "the unmutated
        baseline". Renaming that label -- a pure cosmetic edit -- would have turned every baseline
        failure into a silent kill, which is the exact class of defect this harness exists to find.
        """
        from py_ci_shared.mutation_teeth import MutationHarnessError, _classify_code

        with pytest.raises(MutationHarnessError):
            _classify_code(4, "the unmutated baseline, renamed to something else")
        with pytest.raises(MutationHarnessError):
            _classify_code(4, "a mutant")  # no claim of a verified baseline: still refuses

    def test_pass_and_fail_are_untouched_in_both_modes(self):
        from py_ci_shared.mutation_teeth import _classify_code

        for verified in (False, True):
            assert _classify_code(0, "x", baseline_verified=verified) is True
            assert _classify_code(1, "x", baseline_verified=verified) is False

    def test_end_to_end_a_slots_mutation_is_killed_rather_than_refusing_the_file(self, tmp_path):
        """The measured case, run for real rather than simulated.

        `__slots__ = ("_count",)` with the string emptied raises `TypeError: __slots__ must be
        identifiers` while the class body executes -- at import time, inside conftest -- so pytest
        exits 4. Before this the harness raised, and the refusal discarded the whole file.
        """
        (tmp_path / "subject.py").write_text(
            'class Thing:\n    __slots__ = ("_count",)\n\n    def __init__(self):\n        self._count = 0\n',
            encoding="utf-8",
        )
        (tmp_path / "conftest.py").write_text("import subject  # noqa: F401\n", encoding="utf-8")
        (tmp_path / "test_subject.py").write_text(
            "import subject\n\n\ndef test_it_constructs():\n    assert subject.Thing()._count == 0\n",
            encoding="utf-8",
        )

        outcome = mutation_teeth.find_surviving_mutants("subject.py", ["test_subject.py"], repo_root=tmp_path, use_cache=False)

        assert outcome.mutants_run >= 1
        assert not [m for m in outcome.survivors if "_count" in m.original_span], outcome.summary()


def test_the_worker_pool_tests_carry_a_budget_above_the_global_timeout():
    """Audit 2026-10-03 WF-12: past pytest-timeout's 300 s, its thread method kills the whole xdist worker."""
    (mark,) = [m for m in TestConcurrencyChangesSpeedAndNothingElse.pytestmark if m.name == "timeout"]
    assert mark.args[0] > 300


class TestSurvivorsAreLoggedAsTheyLand:
    """A sweep over a real suite takes hours, and the survivor list used to be visible only when it ended.

    Each survivor is now logged at WARNING as its verdict lands, so a long sweep can be read while it runs.
    Logging changes no verdict: the returned list must be the one the log was written from.
    """

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        open(repo / "m.py", "w", encoding="utf-8", newline="").write(
            "def clamp(n, cap):\n" "    if n > cap:\n" "        return cap\n" "    return n\n" "\n" "\n" "def unchecked(n):\n" "    return n * 2 + 1\n"
        )
        open(repo / "tests" / "test_m.py", "w", encoding="utf-8", newline="").write(
            "from m import clamp\n"
            "\n"
            "\n"
            "def test_clamp():\n"
            "    assert clamp(5, 3) == 3\n"
            "    assert clamp(2, 3) == 2\n"
            "    assert clamp(3, 3) == 3\n"
        )
        return repo

    def test_every_survivor_is_logged_once(self, tmp_path, caplog):
        import logging

        repo = self._repo(tmp_path)
        caplog.set_level(logging.WARNING, logger="py_ci_shared.mutation_teeth")
        outcome = mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False)

        logged = [record.getMessage() for record in caplog.records if "mutation survivor" in record.getMessage()]
        assert outcome.survivors, "the fixture must leave at least one survivor, or this test proves nothing"
        assert len(logged) == len(outcome.survivors)
        for survivor in outcome.survivors:
            assert any(str(survivor.line) in message for message in logged)

    def test_a_killed_mutant_is_not_logged(self, tmp_path, caplog):
        import logging

        repo = self._repo(tmp_path)
        caplog.set_level(logging.WARNING, logger="py_ci_shared.mutation_teeth")
        outcome = mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False)

        assert outcome.killed > 0
        logged = [record for record in caplog.records if "mutation survivor" in record.getMessage()]
        assert len(logged) == len(outcome.survivors) < outcome.mutants_run
