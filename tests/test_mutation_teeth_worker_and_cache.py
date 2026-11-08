"""Tests for py_ci_shared.mutation_teeth: the worker protocol, fingerprints, baseline entries and the cache.

Split from test_mutation_teeth.py; see its docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from py_ci_shared import mutation_teeth


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
            "def test_prints_json():"
            + chr(10)
            + '    print(chr(34) + "a bare json string" + chr(34))'
            + chr(10)
            + "    print('{"
            + chr(34)
            + "rc"
            + chr(34)
            + ": 999}')"
            + chr(10)
            + "    assert True"
            + chr(10)
        )
        open(tmp_path / "test_noisy.py", "w", encoding="utf-8", newline="").write(noisy)

        with _WarmRunner(tmp_path, timeout=120) as warm:
            codes = [warm.run(["test_noisy.py"]) for _ in range(3)]

        assert codes == [0, 0, 0], (
            f"the worker returned {codes}; a 999 would mean the printed line was read as the reply, " "and a None that the channel was lost"
        )

    def test_a_reply_without_rc_is_treated_as_worker_unavailable(self):
        """Not as an error and not as a result. The caller must fall back to a cold run, because a
        harness that turns a protocol hiccup into a verdict is the failure this module exists for."""
        from py_ci_shared.mutation_teeth import _WarmRunner

        runner = _WarmRunner(Path("."), timeout=1)

        class _Fake:
            def poll(self):
                return None

            def wait(self, _timeout=None):
                return 0

            class stdin:  # noqa: N801 - stands in for Popen.stdin, so it must carry that name
                @staticmethod
                def write(_):
                    return None

                @staticmethod
                def flush():
                    return None

                @staticmethod
                def close():
                    return None

            class stdout:  # noqa: N801 - stands in for Popen.stdout, so it must carry that name
                lines: ClassVar[list[str]] = ['"a bare json string"' + chr(10), '{"rc": 999}' + chr(10)]

                @classmethod
                def readline(cls, _size=-1):
                    return cls.lines.pop(0) if cls.lines else ""

        runner.process = _Fake()

        assert runner.run(["tests"]) is None
        assert runner.last_timed_out is False, "an EOF is a dead worker, not a timeout"


class TestTheFingerprintSeesWhatTheAnswerDependsOn:
    """Each of these was a way a cached verdict outlived the thing it was a verdict about."""

    def _pkg(self, tmp_path):
        pkg = tmp_path / "builder"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "helper.py").write_text("def shorten(s):\n    return s[:10]\n", encoding="utf-8")
        (pkg / "main.py").write_text("from .helper import shorten\n\n\ndef go(s):\n    return shorten(s)\n", encoding="utf-8")
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
            "import secrets\n\n\ndef go():\n" "    first = secrets.token_hex(8)\n" "    second = secrets.token_hex(8)\n" "    return first, second\n",
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
        before = next(m for m in mutation_teeth.generate_mutants(src)[0] if m.original_span == "8")
        src.write_text("import secrets\n\n\n# a new comment above\n\n\n" + body, encoding="utf-8")
        after = next(m for m in mutation_teeth.generate_mutants(src)[0] if m.original_span == "8")

        assert after.line != before.line, "the fixture did not actually shift the line"
        assert after.key == before.key


class TestAnUnmeasuredMutantIsNotReportedAsMeasured:
    """A run that could not finish is a third outcome, and it used to be filed under the other two."""

    def _mutant(self, line=1):
        return mutation_teeth.Mutant(
            path=Path("m.py"),
            line=line,
            column=0,
            description="constant: 1 becomes 2",
            original_span="1",
            mutated_span="2",
            context="x = 1",
        )

    def test_the_summary_says_the_sweep_is_incomplete(self):
        run = mutation_teeth.MutationRun(
            survivors=[],
            mutants_run=3,
            killed=3,
            truncated=False,
            candidates_total=4,
            inconclusive=[self._mutant()],
        )
        text = run.summary()

        assert "INCONCLUSIVE" in text
        assert "incomplete" in text

    def test_a_clean_run_says_nothing_about_inconclusive_mutants(self):
        """The counterweight: the notice must not become background noise on every report."""
        run = mutation_teeth.MutationRun(survivors=[], mutants_run=4, killed=4, truncated=False, candidates_total=4)

        assert "INCONCLUSIVE" not in run.summary()

    def test_the_warm_reader_gives_up_rather_than_blocking_forever(self):
        """`readline()` on a pipe has no deadline, so the stored timeout was never applied and one
        non-terminating mutant hung the sweep with no output. A `<` -> `<=` mutation can produce
        exactly that."""
        import threading
        import types

        release = threading.Event()

        class NeverAnswers:
            def readline(self, *args):
                release.wait(30)
                return ""

        runner = mutation_teeth._WarmRunner(Path("."), timeout=600)
        runner.process = types.SimpleNamespace(stdout=NeverAnswers(), stdin=None)
        try:
            got = runner._readline_within(0.3)
        finally:
            # The blocked reader is released rather than left sleeping past the test.
            release.set()

        # Returning at all is the property: a reader stuck on the blocked pipe hangs the test, and pytest-timeout bounds that.
        assert got is None
        readers = [t for t in threading.enumerate() if t.name == "mutation-worker-reader"]
        for reader in readers:
            reader.join(5)
        assert not any(t.is_alive() for t in readers), "the pipe reader outlived the test"


class TestTheCacheReplaysEveryCaveat:
    def test_a_mutant_survives_the_json_round_trip_intact(self):
        """`context` in particular: the baseline key digests it, so a cached survivor whose context
        was dropped would key differently from a freshly measured one and silently stop matching
        its accepted entry."""
        original = mutation_teeth.Mutant(
            path=Path("pkg/m.py"),
            line=7,
            column=4,
            description="constant: 8 becomes 9",
            original_span="8",
            mutated_span="9",
            category="noise",
            context="    nonce = secrets.token_hex(8)",
        )
        restored = mutation_teeth._mutant_from_json(mutation_teeth._mutant_json(original))

        assert restored.context == original.context
        assert restored.key == original.key
