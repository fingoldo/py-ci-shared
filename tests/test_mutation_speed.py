"""The cost options of a mutation sweep change what it costs and never what it decides (audit 2026-11-04, 30-F1..F9).

Each test pairs the saving with the unchanged verdict: the same survivors with the option on and off.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from py_ci_shared import _mutation_speed, _mutation_sweep, mutation_teeth
from py_ci_shared._mutation_checkpoint import SweepCheckpoint
from py_ci_shared._mutation_model import copy_ignore
from py_ci_shared._mutation_speed import (
    CoverageMap,
    LazyWiderBaseline,
    NearestKiller,
    effective_timeout,
    narrowed_paths,
    statement_starts,
    warm_advice,
)

_CLAMP_TESTS = [(5, 3, 3), (2, 3, 2), (3, 3, 3)]


class _Fake:
    """Stands in for pytest and the warm worker: the sandbox's ``m.py`` is executed in-process and checked against the fixture's three asserts.

    It honours what the sweep passes (``-o addopts=``, a failing test file) so the options under test change its answer exactly as they would pytest's.
    """

    def __init__(self) -> None:
        self.cold_calls = 0
        self.warm_runs = 0

    def code(self, sandbox: Path, paths, flags) -> int:
        ini = sandbox / "pytest.ini"
        if ini.exists() and "--no-such-option" in ini.read_text(encoding="utf-8") and "addopts=" not in " ".join(flags).replace(" ", ""):
            return 4
        tests = sandbox / "tests" / "test_m.py"
        if "assert False" in tests.read_text(encoding="utf-8"):
            return 1
        namespace: dict = {}
        try:
            exec(compile((sandbox / "m.py").read_text(encoding="utf-8-sig"), "m.py", "exec"), namespace)
            return 0 if all(namespace["clamp"](n, cap) == want for n, cap, want in _CLAMP_TESTS) else 1
        except Exception:
            return 1

    def run_pytest(self, test_paths, cwd, timeout, *flags):
        self.cold_calls += 1
        rc = self.code(Path(cwd), test_paths, flags)
        out = "FAILED tests/test_m.py::test_clamp\n" if rc == 1 else ""
        return subprocess.CompletedProcess([], rc, stdout=out, stderr="")

    def warm_class(self):
        fake = self

        class Warm:
            def __init__(self, sandbox, timeout, *, target=None, module_names=()):
                self.sandbox, self.timeout, self.target = Path(sandbox), timeout, target
                self.extra_flags: list = []
                self.process = object()
                self.last_failed = self.last_timings = self.last_origin = None
                self.last_crash = self.last_timed_out = False

            def start(self):
                pass

            stop = restart = start

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def run(self, test_paths, *, check_origin=False):
                fake.warm_runs += 1
                rc = fake.code(self.sandbox, test_paths, self.extra_flags)
                self.last_failed = "tests/test_m.py" if rc == 1 else None
                self.last_timings = {"t_total": 0.01, "t_pytest": 0.01, "t_purge": 0.0}
                self.last_origin = {"sandbox": True, "elsewhere": []} if check_origin else None
                return rc

        return Warm


@pytest.fixture
def fake(monkeypatch) -> _Fake:
    """Replace every spawn of pytest and of the warm worker in the sweep with :class:`_Fake`; the sweep's own logic stays real."""
    fake = _Fake()
    for module in (mutation_teeth, _mutation_sweep, _mutation_speed):
        monkeypatch.setattr(module, "_run_pytest", fake.run_pytest)
    for module in (mutation_teeth, _mutation_sweep):
        monkeypatch.setattr(module, "_WarmRunner", fake.warm_class())

    def coverage_map(sandbox, relative, test_paths, timeout, extra_flags=()):
        source = (sandbox / relative).read_text(encoding="utf-8")
        executed = {1: None, 2: ["tests/test_m.py"], 3: ["tests/test_m.py"], 4: ["tests/test_m.py"], 7: None}
        return fake.run_pytest(test_paths, sandbox, timeout, *extra_flags), _mutation_speed.CoverageMap(executed, statement_starts(source))

    monkeypatch.setattr(mutation_teeth, "build_coverage_map", coverage_map)
    return fake


def _repo(tmp_path: Path, pytest_ini: str = "") -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    open(repo / "m.py", "w", encoding="utf-8", newline="").write(
        "def clamp(n, cap):\n    if n > cap:\n        return cap\n    return n\n\n\ndef unchecked(n):\n    return n * 2 + 1\n"
    )
    open(repo / "tests" / "test_m.py", "w", encoding="utf-8", newline="").write(
        "from m import clamp\n\n\ndef test_clamp():\n    assert clamp(5, 3) == 3\n    assert clamp(2, 3) == 2\n    assert clamp(3, 3) == 3\n"
    )
    if pytest_ini:
        open(repo / "pytest.ini", "w", encoding="utf-8", newline="").write(pytest_ini)
    return repo


def _sweep(repo: Path, **kwargs):
    kwargs.setdefault("use_warm_worker", False)
    return mutation_teeth.find_surviving_mutants("m.py", ["tests/test_m.py"], repo, limit=8, timeout=300, use_cache=False, **kwargs)


def _keys(run) -> list[str]:
    return [m.key for m in run.survivors]


def test_single_baseline_drops_the_cold_baseline_and_keeps_the_verdicts(tmp_path, fake):
    repo = _repo(tmp_path)
    old = _sweep(repo, use_warm_worker=True, single_baseline=False)
    old_calls = fake.cold_calls
    fake.cold_calls = 0
    new = _sweep(repo, use_warm_worker=True)
    assert old.survivors, "the fixture must leave a survivor"
    assert _keys(new) == _keys(old)
    assert new.killed == old.killed
    assert fake.cold_calls == old_calls - 1, "the unmutated primary tests must run once (warm), not cold and warm"


def test_a_failing_baseline_is_still_refused_with_one_baseline(tmp_path, fake):
    repo = _repo(tmp_path)
    open(repo / "tests" / "test_m.py", "a", encoding="utf-8", newline="").write("\n\ndef test_broken():\n    assert False\n")
    with pytest.raises(mutation_teeth.MutationHarnessError, match="unmutated baseline does not pass"):
        _sweep(repo, use_warm_worker=True)


def test_effective_timeout_follows_the_baseline_within_floor_and_cap():
    assert effective_timeout(300.0, None, 20.0) == 300.0
    assert effective_timeout(300.0, 3.0, None) == 300.0
    assert effective_timeout(300.0, 3.0, 20.0) == 60.0
    assert effective_timeout(300.0, 3.0, 2.0) == 30.0, "never below the floor"
    assert effective_timeout(300.0, 3.0, 400.0) == 300.0, "never above the cap"


def test_timeout_factor_is_accepted_and_changes_no_verdict(tmp_path, fake):
    repo = _repo(tmp_path)
    assert _keys(_sweep(repo, use_warm_worker=True, timeout_factor=3.0)) == _keys(_sweep(repo, use_warm_worker=True))


def test_the_killer_of_each_mutant_is_stored_and_seeds_the_next_order(tmp_path, fake):
    repo = _repo(tmp_path)
    checkpoint = tmp_path / "ck.jsonl"
    _sweep(repo, checkpoint_path=checkpoint)
    entries = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines()]
    killed = [e for e in entries if e.get("killer")]
    assert killed and all(e["killer"].replace("\\", "/").endswith("test_m.py") and isinstance(e["line"], int) for e in killed)
    loaded = SweepCheckpoint(checkpoint, entries[0]["key"])
    assert {killer.replace("\\", "/") for _, killer in loaded.killers} == {k["killer"].replace("\\", "/") for k in killed}


def test_nearest_killer_leads_with_the_neighbouring_line_and_the_most_frequent_file():
    nearest = NearestKiller()
    for line, killer in [(10, "a.py"), (11, "a.py"), (40, "b.py")]:
        nearest.add(line, killer)
    assert nearest.nearest(12) == "a.py"
    assert nearest.nearest(38) == "b.py"
    assert nearest.order(["b.py", "c.py", "a.py"]) == ["a.py", "b.py", "c.py"]
    assert NearestKiller().nearest(1) is None


def test_statement_starts_maps_continuation_lines_to_their_statement():
    starts = statement_starts("def f(a,\n      b):\n    return g(a,\n             b)\n")
    assert starts[2] == 1 and starts[4] == 3 and starts[3] == 3


def test_coverage_map_answers_unexecuted_covered_and_unknown():
    class M:
        def __init__(self, line, span="x"):
            self.line, self.original_span = line, span

    cmap = CoverageMap({3: ["tests/test_a.py"], 5: None}, {3: 3, 4: 3, 5: 5, 8: 8})
    assert cmap.files_for(M(4)) == ["tests/test_a.py"]  # a continuation line belongs to its statement
    assert cmap.files_for(M(5)) is None  # executed at import time: every file
    assert cmap.files_for(M(8)) == []  # statement no test executes
    assert cmap.files_for(M(99)) is None  # not a statement: unknown, so run everything
    assert CoverageMap.from_json(cmap.to_json()).files_for(M(8)) == []


def test_coverage_prune_skips_unexecuted_statements_and_keeps_the_verdicts(tmp_path, fake):
    repo = _repo(tmp_path)
    plain = _sweep(repo)
    plain_calls = fake.cold_calls
    fake.cold_calls = 0
    pruned = _sweep(repo, coverage_prune=True)
    assert _keys(pruned) == _keys(plain)
    assert fake.cold_calls < plain_calls, "mutants of `unchecked` are never executed, so they must not be run"


def test_coverage_map_is_stored_in_the_checkpoint_and_reused(tmp_path, monkeypatch, fake):
    repo = _repo(tmp_path)
    checkpoint = tmp_path / "ck.jsonl"
    _sweep(repo, coverage_prune=True, checkpoint_path=checkpoint)
    assert any("coverage" in json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines())
    from py_ci_shared import mutation_teeth as teeth

    def refuse(*args, **kwargs):
        raise AssertionError("the stored map must be reused")

    monkeypatch.setattr(teeth, "build_coverage_map", refuse)
    _sweep(repo, coverage_prune=True, checkpoint_path=checkpoint)


def test_confirm_in_worker_confirms_survivors_without_a_cold_run_and_keeps_the_verdicts(tmp_path, fake):
    repo = _repo(tmp_path)
    cold = _sweep(repo, use_warm_worker=True, cold_confirm_every=1)
    cold_calls = fake.cold_calls
    fake.cold_calls = 0
    warm = _sweep(repo, use_warm_worker=True, confirm_in_worker=True, cold_confirm_every=1000)
    assert _keys(warm) == _keys(cold)
    assert fake.cold_calls < cold_calls


def test_copy_ignore_adds_caller_patterns_to_the_builtin_names():
    ignore = copy_ignore(["*.parquet", "dist"])
    assert ignore("d", ["a.parquet", "dist", "m.py", ".git"]) == {"a.parquet", "dist", ".git"}
    assert copy_ignore()("d", ["a.parquet", ".git"]) == {".git"}


def test_ignore_addopts_runs_the_sweep_without_the_repos_addopts(tmp_path, fake):
    repo = _repo(tmp_path, "[pytest]\naddopts = --no-such-option-for-the-sweep\n")
    with pytest.raises(mutation_teeth.MutationHarnessError):
        _sweep(repo)
    assert _keys(_sweep(repo, ignore_addopts=True)), "the survivor of `unchecked` must be found once the addopts are out of the way"


def test_the_wider_baseline_is_lazy_and_runs_once(tmp_path, monkeypatch):
    from py_ci_shared import _mutation_speed

    runs: list[int] = []

    class Done:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(_mutation_speed, "_run_pytest", lambda *a, **k: runs.append(1) or Done())
    lazy = LazyWiderBaseline(["tests"])
    assert not runs
    assert lazy.resolve(tmp_path, 5.0) == ["tests"] and lazy.resolve(tmp_path, 5.0) == ["tests"]
    assert len(runs) == 1


def test_a_wider_net_that_fails_unmutated_is_dropped_with_a_note(tmp_path, monkeypatch):
    from py_ci_shared import _mutation_speed

    class Failed:
        returncode = 1
        stdout = "FAILED tests/test_x.py::test_y"

    monkeypatch.setattr(_mutation_speed, "_run_pytest", lambda *a, **k: Failed())
    lazy = LazyWiderBaseline(["tests"])
    assert lazy.resolve(tmp_path, 5.0) == ()
    assert "does not pass unmutated" in lazy.note


def test_warm_advice_only_when_cold_is_no_slower():
    assert warm_advice([5.0, 5.0, 5.0], [4.0]) is not None
    assert warm_advice([1.0, 1.0, 1.0], [4.0]) is None
    assert warm_advice([5.0], [4.0]) is None, "too few warm runs to say"


def test_narrowed_paths_keep_the_ranked_files_first():
    assert narrowed_paths(["b.py", "a.py", "c.py"], ["c.py", "a.py"]) == ["c.py", "a.py", "b.py"]


@pytest.mark.slow
def test_real_warm_sweep_with_one_baseline_and_a_worker_confirm_matches_the_cold_sweep(tmp_path):
    """End to end with real pytest and a real warm worker (the fast tests above use a stand-in for both)."""
    repo = _repo(tmp_path)
    cold = _sweep(repo)
    warm = _sweep(repo, use_warm_worker=True, confirm_in_worker=True, cold_confirm_every=1000)
    assert cold.survivors, "the fixture must leave a survivor"
    assert _keys(warm) == _keys(cold) and warm.killed == cold.killed


@pytest.mark.slow
def test_real_coverage_prune_matches_the_plain_sweep(tmp_path):
    """The real ``coverage`` map and the real addopts flag, once: the fast tests fake the map and pytest."""
    pytest.importorskip("coverage")
    repo = _repo(tmp_path, "[pytest]\naddopts = --no-such-option-for-the-sweep\n")
    plain = _sweep(repo, ignore_addopts=True)
    pruned = _sweep(repo, ignore_addopts=True, coverage_prune=True)
    assert plain.survivors and _keys(pruned) == _keys(plain)
