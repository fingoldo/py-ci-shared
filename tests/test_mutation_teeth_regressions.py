"""Regression tests for the mutation harness's defects, one class per defect, each with its failing input.

Most sweeps here replace pytest with a fake (patched through the ``mutation_teeth`` namespace, which is
also what keeps the split-out modules patchable) so the harness logic is exercised in milliseconds.
The few tests that need the real warm worker say so.
"""

from __future__ import annotations

import ast
import codecs
import gc
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path
from typing import Any, ClassVar, Optional

import pytest

from py_ci_shared import _mutation_worker, mutation_teeth
from py_ci_shared._mutation_model import SourceText, read_target, write_target
from py_ci_shared._mutation_operators import _Intervals, _normalise_lines
from py_ci_shared.mutation_teeth import Mutant, MutationHarnessError, MutationRun, fingerprint, generate_mutants

# ── fakes ───────────────────────────────────────────────────────────────────────────────────────


class FakeCold:
    """Stands in for ``_run_pytest``: the exit code is a function of the target file's text in *cwd*."""

    def __init__(self, relative: str, verdict, *, on_call=None) -> None:
        self.relative = relative
        self.verdict = verdict
        self.on_call = on_call
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, test_paths, cwd, timeout):
        text = (Path(cwd) / self.relative).read_text(encoding="utf-8")
        self.calls.append((Path(cwd), text))
        if self.on_call is not None:
            self.on_call(len(self.calls), Path(cwd))
        rc = self.verdict(text, list(test_paths))
        if rc is None:
            return None
        return subprocess.CompletedProcess([], rc, "", "")


class FakeWarm:
    """Stands in for ``_WarmRunner``. *script* maps the mutated text to a list of replies, consumed in order.

    A reply is an int exit code, ``"timeout"``, ``"dead"``, or ``(rc, {"crash": True, ...})``.
    """

    instances: ClassVar[list["FakeWarm"]] = []

    def __init__(self, sandbox, timeout, *, target=None, module_names=()) -> None:
        self.sandbox = Path(sandbox)
        self.target = target
        self.process: Any = self
        self.last_failed: Optional[str] = None
        self.last_timings: Optional[dict[str, float]] = None
        self.last_crash = False
        self.last_timed_out = False
        self.last_origin: Optional[dict[str, Any]] = None
        self.restarts = 0
        self.runs: list[str] = []
        self.original = Path(target).read_text(encoding="utf-8") if target is not None else None
        FakeWarm.instances.append(self)

    script: ClassVar[dict[str, list[Any]]] = {}
    default: Any = 0
    origin: Optional[dict[str, Any]] = None

    def poll(self):
        return None

    def run(self, test_paths, *, check_origin=False):
        self.last_failed = None
        self.last_crash = False
        self.last_timed_out = False
        self.last_origin = FakeWarm.origin if check_origin else None
        text = Path(self.target).read_text(encoding="utf-8") if self.target else ""
        self.runs.append(text)
        queue = FakeWarm.script.get(text)
        # The unmutated file passes unless a script says otherwise, as a real baseline does.
        reply = queue.pop(0) if queue else (0 if text == self.original else FakeWarm.default)
        if reply == "timeout":
            self.last_timed_out = True
            self.process = None
            return None
        if reply == "dead":
            self.process = None
            return None
        if isinstance(reply, tuple):
            rc, extra = reply
            self.last_crash = bool(extra.get("crash"))
            self.last_failed = extra.get("failed")
            return rc
        return reply

    def restart(self):
        self.restarts += 1
        self.process = self

    def stop(self):
        self.process = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


@pytest.fixture
def fake_warm(monkeypatch):
    FakeWarm.instances = []
    FakeWarm.script = {}
    FakeWarm.default = 0
    FakeWarm.origin = None
    monkeypatch.setattr(mutation_teeth, "_WarmRunner", FakeWarm)
    return FakeWarm


def _repo(tmp_path: Path, text: str, name: str = "m.py") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / name).write_bytes(text.encode("utf-8"))
    (repo / "test_m.py").write_bytes(b"def test_ok():\n    assert True\n")
    return repo


def _sweep(repo: Path, **kwargs) -> MutationRun:
    kwargs.setdefault("use_cache", False)
    kwargs.setdefault("timeout", 30)
    return mutation_teeth.find_surviving_mutants(kwargs.pop("path", "m.py"), ["test_m.py"], repo, **kwargs)


# ── zero mutants, unparsable targets, BOM, encodings ─────────────────────────────────────────────


class TestZeroMutantsIsNotAPass:
    def test_an_unparsable_target_raises(self, tmp_path):
        target = tmp_path / "m.py"
        target.write_text("def g(:\n    return 1 + 2\n", encoding="utf-8")
        with pytest.raises(MutationHarnessError, match="does not parse"):
            generate_mutants(target)

    def test_an_empty_scope_raises_unless_allowed(self, tmp_path, fake_warm, monkeypatch):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        cold = FakeCold("m.py", lambda text, _p: 0)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        with pytest.raises(MutationHarnessError, match="no mutant"):
            _sweep(repo, lines=[])
        assert cold.calls == [], "the refusal comes before any pytest run is paid for"

        allowed = _sweep(repo, lines=[], allow_empty=True)
        assert allowed.mutants_run == 0 and not allowed.survivors

    def test_a_scope_with_mutants_runs_normally(self, tmp_path, fake_warm, monkeypatch):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.default = 1
        outcome = _sweep(repo)
        assert outcome.mutants_run >= 1 and outcome.killed == outcome.mutants_run


class TestBomAndEncodings:
    SOURCE = "def f(a, b):\n    if a > b:\n        return a\n    return max(a, b)\n"

    def test_a_bom_file_gets_the_same_mutants(self, tmp_path):
        plain = tmp_path / "plain.py"
        bom = tmp_path / "bom.py"
        plain.write_bytes(self.SOURCE.encode("utf-8"))
        bom.write_bytes(codecs.BOM_UTF8 + self.SOURCE.encode("utf-8"))

        without, _t1, _s1 = generate_mutants(plain)
        with_bom, _t2, _s2 = generate_mutants(bom)

        assert len(without) >= 4
        assert [m.description for m in with_bom] == [m.description for m in without]
        assert any(m.description == "deleted a statement-level call" or m.description.startswith("logic:") for m in with_bom)

    def test_a_mutant_is_written_back_with_its_bom(self, tmp_path):
        bom = tmp_path / "bom.py"
        bom.write_bytes(codecs.BOM_UTF8 + self.SOURCE.encode("utf-8"))
        source = read_target(bom)
        assert source.bom and not source.text.startswith("\ufeff")
        write_target(bom, source, source.text.replace(">", ">="))
        assert bom.read_bytes().startswith(codecs.BOM_UTF8)
        assert b">=" in bom.read_bytes()

    def test_a_file_without_a_bom_is_written_without_one(self, tmp_path):
        plain = tmp_path / "plain.py"
        plain.write_bytes(self.SOURCE.encode("utf-8"))
        source = read_target(plain)
        write_target(plain, source, source.text)
        assert plain.read_bytes() == self.SOURCE.encode("utf-8")

    def test_undecodable_bytes_are_a_harness_error(self, tmp_path):
        latin = tmp_path / "latin.py"
        latin.write_bytes("x = 'caf\u00e9'\n".encode("latin-1"))
        with pytest.raises(MutationHarnessError, match="cannot read"):
            generate_mutants(latin)

    def test_a_declared_encoding_is_honoured_and_round_trips(self, tmp_path):
        latin = tmp_path / "latin.py"
        raw = "# -*- coding: latin-1 -*-\nx = 'caf\u00e9'\nif x == 'y':\n    x = 1\n".encode("latin-1")
        latin.write_bytes(raw)
        mutants, _t, _s = generate_mutants(latin)
        assert mutants
        source = read_target(latin)
        write_target(latin, source, source.text)
        assert latin.read_bytes() == raw

    def test_no_file_handle_is_leaked(self, tmp_path):
        target = tmp_path / "m.py"
        target.write_text(self.SOURCE, encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            generate_mutants(target)
            write_target(target, read_target(target), self.SOURCE)
            gc.collect()
        assert not [w for w in caught if issubclass(w.category, ResourceWarning)]


# ── the line scope ──────────────────────────────────────────────────────────────────────────────


class TestTheLineScope:
    SOURCE = "def f(a, b):\n    x = a + b\n    y = a - b\n    return x * y\n"

    def test_a_generator_is_read_once_and_scopes_the_sweep(self, tmp_path, fake_warm, monkeypatch):
        repo = _repo(tmp_path, self.SOURCE)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.default = 1
        whole = _sweep(repo)
        narrow = _sweep(repo, lines=(r for r in [range(2, 3)]))
        listed = _sweep(repo, lines=[range(2, 3)])

        assert narrow.mutants_run == listed.mutants_run
        assert 0 < narrow.mutants_run < whole.mutants_run

    def test_a_generator_run_is_cached_under_its_own_scope(self, tmp_path, fake_warm, monkeypatch):
        repo = _repo(tmp_path, self.SOURCE)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.default = 1
        cache = tmp_path / "cache.json"
        first = _sweep(repo, lines=(r for r in [range(2, 3)]), use_cache=True, cache_path=cache)
        replay = _sweep(repo, lines=[range(2, 3)], use_cache=True, cache_path=cache)
        whole = _sweep(repo, use_cache=True, cache_path=cache)

        assert replay.from_cache and replay.mutants_run == first.mutants_run
        assert not whole.from_cache and whole.mutants_run > first.mutants_run

    def test_an_empty_scope_and_the_whole_file_key_differently(self, tmp_path):
        (tmp_path / "m.py").write_text(self.SOURCE, encoding="utf-8")
        (tmp_path / "test_m.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
        whole = fingerprint(tmp_path, "m.py", ["test_m.py"], scope=mutation_teeth._scope_key(None, None, (), False))
        empty = fingerprint(tmp_path, "m.py", ["test_m.py"], scope=mutation_teeth._scope_key([], None, (), False))
        assert whole != empty

    def test_an_empty_scope_generates_nothing(self, tmp_path):
        target = tmp_path / "m.py"
        target.write_text(self.SOURCE, encoding="utf-8")
        assert generate_mutants(target, lines=[])[0] == []
        assert generate_mutants(target, lines=None)[0]

    def test_a_non_range_entry_is_rejected_up_front(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path, self.SOURCE)
        cold = FakeCold("m.py", lambda text, _p: 0)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        with pytest.raises(TypeError, match="range"):
            _sweep(repo, lines=[(1, 5)])
        with pytest.raises(TypeError, match="range"):
            generate_mutants(repo / "m.py", lines=[range(1, 2), (1, 5)])
        assert cold.calls == []
        assert _normalise_lines([range(1, 5)]) == [range(1, 5)]


# ── twins, crash counting, timeouts ─────────────────────────────────────────────────────────────


def _twin_pair(text: str) -> list[Mutant]:
    return [
        Mutant(Path("m.py"), 2, 4, "operator: + becomes -", "+", "-", context="    return a + b\n", mutated_file_text=text),
        Mutant(Path("m.py"), 2, 4, "constant: 'x' became 'y'", "x", "y", context="    return a + b\n", mutated_file_text=text),
    ]


class TestTwinsShareOneVerdict:
    def _run(self, tmp_path, monkeypatch, fake_warm, rc):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        twin_text = "def f(a, b):\n    return a - b\n"
        monkeypatch.setattr(mutation_teeth, "generate_mutants", lambda target, lines=None, limit=None: (_twin_pair(twin_text), 2, {}))
        original = (repo / "m.py").read_text(encoding="utf-8")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0 if text == original else rc))
        fake_warm.default = rc
        outcome = _sweep(repo)
        runs_of_twin = sum(1 for inst in fake_warm.instances for text in inst.runs if text == twin_text)
        return outcome, runs_of_twin

    def test_a_surviving_twin_reports_both_keys(self, tmp_path, monkeypatch, fake_warm):
        outcome, runs = self._run(tmp_path, monkeypatch, fake_warm, 0)
        assert runs == 1, "the twin is run once"
        assert len({m.key for m in outcome.survivors}) == 2
        assert outcome.mutants_run == 2 and outcome.killed == 0 and not outcome.truncated

    def test_a_killed_twin_kills_both(self, tmp_path, monkeypatch, fake_warm):
        outcome, runs = self._run(tmp_path, monkeypatch, fake_warm, 1)
        assert runs == 1
        assert outcome.survivors == [] and outcome.mutants_run == 2 and outcome.killed == 2


class TestWarmCrashesAreCounted:
    def test_a_type_error_kill_in_the_warm_worker_is_a_crash(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.default = (1, {"crash": True})
        outcome = _sweep(repo)
        assert outcome.killed_by_crash == outcome.mutants_run > 0

    def test_an_assertion_kill_is_not(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.default = (1, {"crash": False})
        outcome = _sweep(repo)
        assert outcome.killed_by_crash == 0 and outcome.killed == outcome.mutants_run > 0

    def test_the_crash_list_has_no_dead_entry(self):
        assert "AssertionError" not in mutation_teeth._CRASH_EXCEPTIONS
        assert _mutation_worker.is_crash_message("AssertionError: boom") is False
        assert _mutation_worker.is_crash_message("TypeError: boom") is True
        assert _mutation_worker.is_crash_message("something else") is None


class TestTimeoutsAreInconclusive:
    SOURCE = "def f(a, b):\n    return a + b\n"

    def test_a_warm_timeout_is_not_re_run_cold_and_the_worker_is_replaced(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, self.SOURCE)
        cold = FakeCold("m.py", lambda text, _p: 0)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        mutants, _t, _s = generate_mutants(repo / "m.py")
        hanging = mutants[0].mutated_file_text
        fake_warm.script = {hanging: ["timeout"]}
        fake_warm.default = 1
        outcome = _sweep(repo)

        assert [m.mutated_file_text for m in outcome.inconclusive] == [hanging]
        assert not [c for c in cold.calls if c[1] == hanging], "the timed-out mutant was re-run cold"
        assert fake_warm.instances[0].restarts == 1
        assert outcome.mutants_run == len(mutants) - 1 == outcome.killed

    def test_a_wider_net_timeout_is_inconclusive_not_a_survivor(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, self.SOURCE)
        (repo / "test_wide.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        mutants, _t, _s = generate_mutants(repo / "m.py")
        first = mutants[0].mutated_file_text
        fake_warm.script = {first: [0, "timeout"]}
        fake_warm.default = 1
        outcome = _sweep(repo, fallback_test_paths=["test_wide.py"])

        assert [m.mutated_file_text for m in outcome.inconclusive] == [first]
        assert outcome.survivors == []

    def test_a_cold_wider_net_timeout_is_inconclusive_too(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, self.SOURCE)
        (repo / "test_wide.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
        calls = {"wide": 0}

        def verdict(text, paths):
            if paths == ["test_wide.py"]:
                calls["wide"] += 1
                return 0 if calls["wide"] == 1 else None  # the unmutated net passes; every mutant hangs it
            return 0

        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", verdict))
        outcome = _sweep(repo, fallback_test_paths=["test_wide.py"], use_warm_worker=False)
        assert outcome.survivors == [] and len(outcome.inconclusive) == outcome.candidates_total > 0

    def test_a_dead_worker_falls_back_cold_and_is_restarted(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, self.SOURCE)
        cold = FakeCold("m.py", lambda text, _p: 1 if text != self.SOURCE else 0)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        mutants, _t, _s = generate_mutants(repo / "m.py")
        fake_warm.script = {mutants[0].mutated_file_text: ["dead"]}
        fake_warm.default = 1
        outcome = _sweep(repo)
        assert outcome.inconclusive == [] and outcome.killed == len(mutants)
        assert [c for c in cold.calls if c[1] == mutants[0].mutated_file_text]
        assert fake_warm.instances[0].restarts == 1


# ── sandboxes ───────────────────────────────────────────────────────────────────────────────────


class TestExtraSandboxes:
    def test_sweep_files_with_jobs_cleans_up_and_handles_a_second_file(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b * 2\n")
        (repo / "n.py").write_text("def g(a, b):\n    return a - b * 3\n", encoding="utf-8")
        scratch = tmp_path / "tmp"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))

        def cold(test_paths, cwd, timeout):
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        fake_warm.default = 1
        results = mutation_teeth.sweep_files([("m.py", ["test_m.py"]), ("n.py", ["test_m.py"])], repo, jobs=2, use_cache=False, timeout=30)

        assert set(results) == {"m.py", "n.py"}
        assert all(r.mutants_run > 0 for r in results.values())
        assert list(scratch.iterdir()) == [], "a sandbox was left behind"

    def test_extras_are_copied_from_the_verified_sandbox_and_verified(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b * 2\n")
        original = (repo / "m.py").read_text(encoding="utf-8")

        def edit_repo(n, cwd):
            if n == 1:  # the live repo changes right after the baseline
                (repo / "m.py").write_text("def f(a, b):\n    return 0\n", encoding="utf-8")

        cold = FakeCold("m.py", lambda text, _p: 0, on_call=edit_repo)
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        fake_warm.default = 1
        _sweep(repo, jobs=2)

        extra_baselines = [c for c in cold.calls[1:] if c[1] == original]
        assert extra_baselines, "the extra sandbox had no unmutated baseline of its own"
        assert all(c[1] != "def f(a, b):\n    return 0\n" for c in cold.calls)

    def test_an_extra_sandbox_whose_baseline_fails_is_refused(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b * 2\n")
        cold = FakeCold("m.py", lambda text, _p: 0, on_call=None)
        cold.verdict = lambda text, _p: 0 if len(cold.calls) == 1 else 1
        monkeypatch.setattr(mutation_teeth, "_run_pytest", cold)
        with pytest.raises(MutationHarnessError, match="extra sandbox"):
            _sweep(repo, jobs=2)


class TestTheImportMustComeFromTheSandbox:
    def test_a_foreign_origin_is_refused(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.origin = {"sandbox": False, "elsewhere": ["m from C:/site-packages/m.py"]}
        with pytest.raises(MutationHarnessError, match="outside the mutation sandbox"):
            _sweep(repo)

    def test_the_sandbox_origin_is_accepted(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.origin = {"sandbox": True, "elsewhere": ["m from C:/site-packages/m.py"]}
        fake_warm.default = 1
        assert _sweep(repo).mutants_run > 0

    def test_a_cold_only_run_checks_too(self, tmp_path, monkeypatch, fake_warm):
        repo = _repo(tmp_path, "def f(a, b):\n    return a + b\n")
        monkeypatch.setattr(mutation_teeth, "_run_pytest", FakeCold("m.py", lambda text, _p: 0))
        fake_warm.origin = {"sandbox": False, "elsewhere": ["m from elsewhere"]}
        with pytest.raises(MutationHarnessError, match="outside the mutation sandbox"):
            _sweep(repo, use_warm_worker=False)

    def test_module_names_never_include_a_bare_name_inside_a_package(self):
        assert mutation_teeth._module_names(Path("src/pkg/mod.py")) == ["src.pkg.mod", "pkg.mod"]
        assert mutation_teeth._module_names(Path("pkg/__init__.py")) == ["pkg"]
        assert mutation_teeth._module_names(Path("m.py")) == ["m"]

    def test_end_to_end_a_shadowing_copy_is_detected(self, tmp_path):
        """The real worker: the tests import `pkg.mod` from a directory outside the sandbox."""
        repo = tmp_path / "repo"
        shadow = tmp_path / "shadow"
        for root in (repo, shadow):
            (root / "pkg").mkdir(parents=True)
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "mod.py").write_text("def f(a, b):\n    return a + b\n", encoding="utf-8")
        # The conftest imports the shadow copy before any test module does, as an editable install would.
        (repo / "conftest.py").write_text(f"import sys\nsys.path.insert(0, {str(shadow)!r})\nimport pkg.mod  # noqa: E402,F401\n", encoding="utf-8")
        (repo / "test_mod.py").write_text("from pkg.mod import f\n\n\ndef test_f():\n    assert f(1, 2) == 3\n", encoding="utf-8")
        with pytest.raises(MutationHarnessError, match="outside the mutation sandbox"):
            mutation_teeth.find_surviving_mutants("pkg/mod.py", ["test_mod.py"], repo, use_cache=False, timeout=120)


# ── the cache ───────────────────────────────────────────────────────────────────────────────────


class TestTheCache:
    def _run(self, note: str) -> MutationRun:
        return MutationRun(survivors=[], mutants_run=2, killed=2, truncated=False, candidates_total=2, wider_net_note=note)

    def test_the_wider_net_note_is_replayed(self, tmp_path):
        cache = tmp_path / "c.json"
        mutation_teeth._store_cached_run(cache, Path("m.py"), "k", self._run("the wider net does not pass unmutated"))
        replay = mutation_teeth._load_cached_run(cache, Path("m.py"), "k")
        assert replay is not None and replay.wider_net_note == "the wider net does not pass unmutated"
        assert "WIDER NET UNUSED" in replay.summary()

    def test_a_run_without_a_note_replays_without_one(self, tmp_path):
        cache = tmp_path / "c.json"
        mutation_teeth._store_cached_run(cache, Path("m.py"), "k", self._run(""))
        replay = mutation_teeth._load_cached_run(cache, Path("m.py"), "k")
        assert replay is not None and replay.wider_net_note == "" and "WIDER NET" not in replay.summary()

    def test_concurrent_writers_keep_every_entry(self, tmp_path):
        cache = tmp_path / "c.json"
        errors: list[BaseException] = []

        def writer(prefix: str) -> None:
            try:
                for i in range(15):
                    mutation_teeth._store_cached_run(cache, Path(f"{prefix}{i}.py"), "k", self._run(""))
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(json.loads(cache.read_text(encoding="utf-8"))) == 45
        assert not (tmp_path / "c.json.tmp").exists()

    def test_the_lock_excludes_a_second_holder(self, tmp_path):
        cache = tmp_path / "c.json"
        got: list[str] = []

        def contender() -> None:
            try:
                with mutation_teeth._cache_lock(cache, timeout=0.3):
                    got.append("acquired")
            except TimeoutError:
                got.append("timed out")

        with mutation_teeth._cache_lock(cache):
            t = threading.Thread(target=contender)
            t.start()
            t.join()
        assert got == ["timed out"]
        contender()
        assert got == ["timed out", "acquired"], "the lock was not released"


# ── fingerprint ─────────────────────────────────────────────────────────────────────────────────


class TestTheFingerprintCoversWhatPytestRuns:
    def _base(self, tmp_path: Path) -> Path:
        (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
        return tmp_path

    def test_a_node_id_hashes_its_file(self, tmp_path):
        root = self._base(tmp_path)
        before = fingerprint(root, "m.py", ["tests/test_x.py::test_f"])
        (root / "tests" / "test_x.py").write_text("def test_f():\n    assert False\n", encoding="utf-8")
        assert fingerprint(root, "m.py", ["tests/test_x.py::test_f"]) != before

    def test_an_unrelated_file_does_not_move_a_node_id_key(self, tmp_path):
        root = self._base(tmp_path)
        before = fingerprint(root, "m.py", ["tests/test_x.py::test_f"])
        (root / "notes.txt").write_text("hello", encoding="utf-8")
        assert fingerprint(root, "m.py", ["tests/test_x.py::test_f"]) == before

    def test_the_default_python_files_include_suffix_style(self, tmp_path):
        root = self._base(tmp_path)
        (root / "tests" / "foo_test.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
        before = fingerprint(root, "m.py", ["tests"])
        (root / "tests" / "foo_test.py").write_text("def test_f():\n    assert 0\n", encoding="utf-8")
        assert fingerprint(root, "m.py", ["tests"]) != before

    def test_a_configured_python_files_is_honoured(self, tmp_path):
        root = self._base(tmp_path)
        (root / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
        (root / "tests" / "check_a.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
        (root / "tests" / "helper.py").write_text("X = 1\n", encoding="utf-8")
        before = fingerprint(root, "m.py", ["tests"])
        (root / "tests" / "check_a.py").write_text("def test_f():\n    assert 0\n", encoding="utf-8")
        after_check = fingerprint(root, "m.py", ["tests"])
        (root / "tests" / "helper.py").write_text("X = 2\n", encoding="utf-8")
        assert after_check != before
        assert fingerprint(root, "m.py", ["tests"]) == after_check, "a file pytest would not collect moved the key"

    def test_conftests_outside_the_repo_are_never_walked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._base(repo)
        for name in ("aaa", "zzz"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "test_o.py").write_text("def test_o():\n    pass\n", encoding="utf-8")
            (tmp_path / name / "conftest.py").write_text("X = 1\n", encoding="utf-8")
        paths = ["../aaa/test_o.py", "../zzz/test_o.py"]
        before = fingerprint(repo, "m.py", paths)
        (tmp_path / "zzz" / "conftest.py").write_text("X = 2\n", encoding="utf-8")
        (tmp_path / "aaa" / "conftest.py").write_text("X = 2\n", encoding="utf-8")
        assert fingerprint(repo, "m.py", paths) == before
        (repo / "conftest.py").write_text("Y = 1\n", encoding="utf-8")
        assert fingerprint(repo, "m.py", ["tests/test_x.py"]) != fingerprint(repo, "m.py", ["tests/test_x.py"], scope="x")

    def test_a_package_init_is_in_the_closure(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "pkg" / "other.py").write_text("Y = 1\n", encoding="utf-8")
        (tmp_path / "test_m.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
        before = fingerprint(tmp_path, "pkg/mod.py", ["test_m.py"])
        (tmp_path / "pkg" / "other.py").write_text("Y = 2\n", encoding="utf-8")
        assert fingerprint(tmp_path, "pkg/mod.py", ["test_m.py"]) == before
        (tmp_path / "pkg" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
        assert fingerprint(tmp_path, "pkg/mod.py", ["test_m.py"]) != before


# ── operators ───────────────────────────────────────────────────────────────────────────────────


def _mutants_of(tmp_path: Path, text: str):
    target = tmp_path / "subject.py"
    target.write_bytes(text.encode("utf-8"))
    return generate_mutants(target)


class TestOperatorsProduceTheMutantTheyName:
    def test_dropping_a_not_before_a_paren_keeps_the_paren(self, tmp_path):
        mutants, _t, _s = _mutants_of(tmp_path, "def f(x):\n    return not(x)\n")
        dropped = [m for m in mutants if m.description == "dropped a `not`"]
        assert len(dropped) == 1
        assert "return (x)" in dropped[0].mutated_file_text
        ast.parse(dropped[0].mutated_file_text)

    def test_dropping_a_spaced_not_removes_the_space(self, tmp_path):
        mutants, _t, _s = _mutants_of(tmp_path, "def f(x):\n    return not x\n")
        dropped = [m for m in mutants if m.description == "dropped a `not`"]
        assert [m.mutated_file_text for m in dropped] == ["def f(x):\n    return x\n"]

    def test_emptying_bytes_keeps_them_bytes(self, tmp_path):
        mutants, _t, _s = _mutants_of(tmp_path, "def f():\n    return b'ab'\n")
        emptied = [m for m in mutants if m.description == "constant: emptied a string"]
        assert [m.mutated_span for m in emptied] == ['b""']

    def test_an_empty_literal_is_not_emptied(self, tmp_path):
        mutants, _t, _s = _mutants_of(tmp_path, "def f():\n    return r'' + u'' + 'ab'\n")
        emptied = [m for m in mutants if m.description == "constant: emptied a string"]
        assert [m.original_span for m in emptied] == ["'ab'"]

    def test_min_and_max_describe_their_own_direction(self, tmp_path):
        mutants, _t, _s = _mutants_of(tmp_path, "def f(a, b):\n    return min(a, b) + max(a, b)\n")
        described = {m.original_span: m.description for m in mutants if m.original_span in ("min", "max")}
        assert described == {"min": "logic: min becomes max", "max": "logic: max becomes min"}


class TestSamplingCountsRowsAndCoversEveryOperator:
    def test_a_six_entry_dict_reports_six_rows(self, tmp_path):
        text = "T = {" + ", ".join(f"'k{i}': {i}" for i in range(6)) + "}\n"
        _m, _t, sampled = _mutants_of(tmp_path, text)
        assert sampled == {"T": (3, 6)}

    def test_a_small_table_is_not_sampled(self, tmp_path):
        _m, _t, sampled = _mutants_of(tmp_path, "T = {'a': 1, 'b': 2, 'c': 3}\n")
        assert sampled == {}

    def test_ast_operators_are_sampled_too(self, tmp_path):
        text = "def f(x, n1, n2, n3, n4, n5):\n    T = [x[:n1], x[:n2], x[:n3], x[:n4], x[:n5]]\n    return T\n"
        mutants, _t, sampled = _mutants_of(tmp_path, text)
        slices = [m for m in mutants if m.description.startswith("slice:")]
        assert sampled == {"T": (3, 5)}
        assert len(slices) == 3

    def test_a_key_and_its_value_are_kept_or_dropped_together(self, tmp_path):
        text = "T = {" + ", ".join(f"{i}: {i + 100}" for i in range(6)) + "}\n"
        mutants, _t, _s = _mutants_of(tmp_path, text)
        keys = {m.original_span for m in mutants if m.original_span.isdigit() and int(m.original_span) < 100}
        values = {str(int(m.original_span) - 100) for m in mutants if m.original_span.isdigit() and int(m.original_span) >= 100}
        assert keys == values and len(keys) == 3


class TestParsedOnce:
    def test_the_target_is_parsed_once(self, tmp_path, monkeypatch):
        text = "import re\n\n\ndef f(a, b):\n    if a > b:\n        return re.sub('x+', 'y', a[:b])\n    return g(a, b)\n"
        target = tmp_path / "m.py"
        target.write_bytes(text.encode("utf-8"))
        real_parse = ast.parse
        seen: list[str] = []

        def counting(source, *args, **kwargs):
            seen.append(source if isinstance(source, str) else "")
            return real_parse(source, *args, **kwargs)

        monkeypatch.setattr(ast, "parse", counting)
        mutants, _t, _s = generate_mutants(target)
        assert mutants
        assert seen.count(text) == 1

    def test_interval_containment_matches_a_scan(self):
        ranges = [(0, 5), (3, 9), (20, 30), (22, 25), (40, 41)]
        index = _Intervals(ranges)
        for a in range(0, 45):
            for b in range(a, 46):
                merged_hit = any(lo <= a and b <= hi for lo, hi in [(0, 9), (20, 30), (40, 41)])
                assert index.contains(a, b) == merged_hit, (a, b)


# ── the ratchet ─────────────────────────────────────────────────────────────────────────────────


class _RatchetBaseline:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.regenerated: Optional[dict[str, str]] = None

    def regenerate(self, found):
        self.regenerated = dict(found)
        self.path.write_text(json.dumps(found), encoding="utf-8")

    def enforce(self, found, *, label, guidance):
        return 1 if found else 0


def _survivor(line: int = 1) -> Mutant:
    return Mutant(Path("m.py"), line, 0, "constant: 1 becomes 2", "1", "2", context=f"x = {line}")


class TestTheRatchet:
    def _patch(self, monkeypatch, **fields):
        run = MutationRun(survivors=[_survivor()], mutants_run=5, killed=4, truncated=False, candidates_total=5)
        for k, v in fields.items():
            setattr(run, k, v)
        monkeypatch.setattr(mutation_teeth, "find_surviving_mutants", lambda *a, **k: run)

    @pytest.mark.parametrize(
        "fields",
        [{"truncated": True, "candidates_total": 9}, {"inconclusive": [_survivor(2)]}, {"coverage_gaps": [_survivor(3)]}],
    )
    def test_a_partial_run_never_rewrites_the_baseline(self, tmp_path, monkeypatch, fields):
        self._patch(monkeypatch, **fields)
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", mutation_teeth.REFRESH_FLAG)
        baseline = _RatchetBaseline(tmp_path / "b.json")
        with pytest.raises(AssertionError):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)
        assert baseline.regenerated is None and not baseline.path.exists()

    def test_a_complete_run_refreshes_with_the_marker(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", mutation_teeth.REFRESH_FLAG)
        baseline = _RatchetBaseline(tmp_path / "b.json")
        with pytest.raises(AssertionError, match="WITHOUT justifications"):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)
        assert baseline.regenerated and all(v.startswith("NEEDS-JUSTIFICATION") for v in baseline.regenerated.values())

    def test_truncation_fails_only_when_asked(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PY_CI_SHARED_REFRESH", raising=False)
        self._patch(monkeypatch, truncated=True, survivors=[], candidates_total=9)
        baseline = _RatchetBaseline(tmp_path / "b.json")
        with pytest.warns(UserWarning, match="TRUNCATED"):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)
        with pytest.raises(AssertionError, match="fail_on_truncation"):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline, fail_on_truncation=True)

    def test_a_core_baseline_is_accepted_and_needs_justification(self, tmp_path, monkeypatch):
        from py_ci_shared._core import Baseline

        self._patch(monkeypatch)
        path = tmp_path / "core.json"
        baseline = Baseline(path, gate="mutation teeth", refresh_command="--refresh-mutation-survivors-baseline")
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", mutation_teeth.REFRESH_FLAG)
        with pytest.raises(AssertionError, match="WITHOUT justifications"):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)
        monkeypatch.delenv("PY_CI_SHARED_REFRESH")
        with pytest.raises(AssertionError, match="NEEDS-JUSTIFICATION"):
            mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)
        entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
        for entry in entries.values():
            entry["note"] = "equivalent: the constant is only logged"
        path.write_text(json.dumps({"schema": 1, "gate": "mutation teeth", "entries": entries}), encoding="utf-8")
        mutation_teeth.assert_no_new_surviving_mutant("m.py", ["t.py"], tmp_path, baseline)


# ── process trees and sandbox removal ───────────────────────────────────────────────────────────


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, timeout=30).stdout
        return str(pid).encode("ascii") in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TestTimeoutsKillTheTree:
    def test_a_grandchild_dies_with_its_parent(self, tmp_path):
        from py_ci_shared._mutation_runner import run_with_deadline

        pid_file = tmp_path / "grandchild.pid"
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(120)\n"
        )
        started = time.monotonic()
        result = run_with_deadline([sys.executable, "-c", script], cwd=tmp_path, env=None, timeout=5)
        assert result is None and time.monotonic() - started < 60
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(pid), "the grandchild outlived the timeout"

    def test_a_finished_run_returns_its_output(self, tmp_path):
        from py_ci_shared._mutation_runner import run_with_deadline

        result = run_with_deadline([sys.executable, "-c", "print('hi'); raise SystemExit(3)"], cwd=tmp_path, env=None, timeout=60)
        assert result is not None and result.returncode == 3 and result.stdout.strip() == "hi"

    @pytest.mark.skipif(os.name != "nt", reason="an open handle only blocks removal on Windows")
    def test_a_sandbox_that_cannot_be_removed_is_reported(self, tmp_path):
        from py_ci_shared._mutation_runner import _remove_tree

        doomed = tmp_path / "sandbox"
        doomed.mkdir()
        handle = open(doomed / "held.txt", "w")
        try:
            with pytest.warns(UserWarning, match="could not remove its sandbox"):
                _remove_tree(doomed)
        finally:
            handle.close()
        _remove_tree(doomed)
        assert not doomed.exists()

    def test_a_read_only_file_does_not_stop_removal(self, tmp_path):
        from py_ci_shared._mutation_runner import _remove_tree

        doomed = tmp_path / "sandbox"
        doomed.mkdir()
        (doomed / "ro.txt").write_text("x", encoding="utf-8")
        os.chmod(doomed / "ro.txt", 0o444)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _remove_tree(doomed)
        assert not doomed.exists()


class TestWarmRunnerState:
    def test_a_dead_worker_resets_the_last_run(self):
        runner = mutation_teeth._WarmRunner(Path("."), timeout=1)
        runner.last_failed = "tests/test_old.py"
        runner.last_timings = {"t_total": 1.0}
        runner.last_crash = True

        class Dead:
            stdin = stdout = None

            def poll(self):
                return 1

        runner.process = Dead()
        assert runner.run(["tests"]) is None
        assert runner.last_failed is None and runner.last_timings is None and runner.last_crash is False

    def test_a_reply_to_another_request_is_skipped(self):
        runner = mutation_teeth._WarmRunner(Path("."), timeout=5)

        class Fake:
            def poll(self):
                return None

            class stdin:  # noqa: N801 - stands in for Popen.stdin
                @staticmethod
                def write(_):
                    return None

                @staticmethod
                def flush():
                    return None

            class stdout:  # noqa: N801 - stands in for Popen.stdout
                lines: ClassVar[list[str]] = ['{"id": 0, "rc": 999}\n', "garbage\n", '{"id": 1, "rc": 1, "failed": "t.py", "crash": true}\n']

                @classmethod
                def readline(cls):
                    return cls.lines.pop(0) if cls.lines else ""

        runner.process = Fake()
        assert runner.run(["t.py"]) == 1
        assert runner.last_failed == "t.py" and runner.last_crash is True


def test_source_text_default_is_plain_utf8():
    assert SourceText("x").encode("é") == b"\xc3\xa9"
