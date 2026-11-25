"""The memory a scan holds is bounded: a budget on the parse cache, a worker count that fits the host, and the parent's parsed corpus released before a pool starts.

Measured on mlframe (CPython 3.11): a parsed corpus costs about 55 times its source bytes in every process that scans it (37 times for the text and the trees, 45 percent more for what ``node_index`` derives), a test session that had
scanned ``src`` and ``tests`` held 5.5 GB, and four workers of about 1 GB each beside it ran a 16 GB host out of memory. Reading pickled trees back from disk instead of parsing them took 19.8 s against 14.8 s and the same resident size.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache, memory, parse_file, source
from py_ci_shared._core.source import parse_cache_size
from py_ci_shared._core.node_index import nodes_of
from py_ci_shared.code_audit import registry


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Every test starts and ends with an empty parse cache."""
    clear_parse_cache()
    yield
    clear_parse_cache()


def _corpus(root: Path, files: int, lines: int) -> list:
    """``files`` modules of ``lines`` one-line functions each."""
    out = []
    for i in range(files):
        path = root / f"m{i}.py"
        path.write_text("".join(f"def f{j}(x):\n    return x + {j}\n" for j in range(lines)), encoding="utf-8")
        out.append(path)
    return out


def test_the_budget_comes_from_the_variable_or_half_the_memory_capped_at_8_gib(monkeypatch):
    """``PY_CI_SHARED_PARSE_CACHE_MB`` wins (zero or less means no limit); without it half of the physical memory up to 8 GiB; an unknown memory means no limit."""
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "64")
    assert memory.parse_cache_budget_bytes() == 64 * 2**20
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "0")
    assert memory.parse_cache_budget_bytes() is None
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "")
    monkeypatch.setattr(memory, "total_memory_bytes", lambda: 8 * 2**30)
    assert memory.parse_cache_budget_bytes() == 4 * 2**30
    monkeypatch.setattr(memory, "total_memory_bytes", lambda: 64 * 2**30)
    assert memory.parse_cache_budget_bytes() == 8 * 2**30
    monkeypatch.setattr(memory, "total_memory_bytes", lambda: None)
    assert memory.parse_cache_budget_bytes() is None


def test_workers_that_fit_divides_the_free_memory_by_the_cost_of_one_parsed_corpus(monkeypatch):
    """``(available - margin) // (source bytes x 55 + the bare worker)``; zero when not even one fits; ``None`` when the host memory is unknown."""
    per_worker = 10 * 2**20 * memory.TREE_BYTES_PER_SOURCE_BYTE + memory.WORKER_BASE_BYTES
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: 512 * 2**20 + 3 * per_worker + 1)
    assert memory.workers_that_fit(10 * 2**20) == 3
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: 512 * 2**20 + per_worker - 1)
    assert memory.workers_that_fit(10 * 2**20) == 0
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: None)
    assert memory.workers_that_fit(10 * 2**20) is None


def test_the_parse_cache_is_dropped_when_it_passes_its_budget_and_the_caller_keeps_a_valid_tree(tmp_path, monkeypatch):
    """With a 1 MiB budget one 60 KB module (3.3 MiB by the cost model) empties the cache again; the tree the caller got is intact; a large budget keeps every entry."""
    (path,) = _corpus(tmp_path, 1, 3000)
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "1")
    tree = parse_file(path)
    assert parse_cache_size() == 0
    assert len(nodes_of(tree, ast.FunctionDef)) == 3000
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "4096")
    parse_file(path)
    assert parse_cache_size() == 1


def test_a_changed_file_replaces_its_cost_instead_of_adding_to_it(tmp_path, monkeypatch):
    """The estimated size of a path is counted once: rewriting a file does not make the budget fill with stale entries."""
    monkeypatch.setenv("PY_CI_SHARED_PARSE_CACHE_MB", "4096")
    (path,) = _corpus(tmp_path, 1, 100)
    parse_file(path)
    first = source._COST_TOTAL
    path.write_text(path.read_text(encoding="utf-8") + "\ny = 1\n", encoding="utf-8")
    parse_file(path)
    assert parse_cache_size() == 1
    assert source._COST_TOTAL == pytest.approx(first, rel=0.1)
    clear_parse_cache()
    assert source._COST_TOTAL == 0 and source._COST == {}


def test_node_index_positions_are_unchanged_by_the_compact_array(tmp_path):
    """The walk positions are stored as a 4-byte array; the nodes of several types still come back in ``ast.walk`` order."""
    (path,) = _corpus(tmp_path, 1, 50)
    tree = parse_file(path)
    got = nodes_of(tree, ast.FunctionDef, ast.BinOp)
    expected = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.BinOp))]
    assert got == expected


def test_the_pool_size_follows_the_free_memory_and_the_parents_corpus_is_released_first(tmp_path, monkeypatch):
    """Workers are capped by what fits, below two the caller runs in this process, the environment variable caps further, and the parent's parse cache is empty before any worker starts."""
    paths = _corpus(tmp_path, 3, 20)
    for p in paths:
        parse_file(p)
    assert parse_cache_size() == 3
    monkeypatch.setattr(memory, "workers_that_fit", lambda corpus_bytes, margin_bytes=0: 3)
    monkeypatch.delenv("PY_CI_SHARED_AUDIT_MAX_WORKERS", raising=False)
    assert registry._workers_for_memory(8, tmp_path, frozenset()) == 3
    assert parse_cache_size() == 0, "the parent's parsed corpus is of no use to the workers and must be released first"
    monkeypatch.setattr(memory, "workers_that_fit", lambda corpus_bytes, margin_bytes=0: 1)
    assert registry._workers_for_memory(8, tmp_path, frozenset()) == 1
    monkeypatch.setattr(memory, "workers_that_fit", lambda corpus_bytes, margin_bytes=0: None)
    assert registry._workers_for_memory(8, tmp_path, frozenset()) == 8
    monkeypatch.setenv("PY_CI_SHARED_AUDIT_MAX_WORKERS", "4")
    assert registry._workers_for_memory(8, tmp_path, frozenset()) == 4
    monkeypatch.setenv("PY_CI_SHARED_AUDIT_MAX_WORKERS", "1")
    assert registry._workers_for_memory(8, tmp_path, frozenset()) == 1


def test_without_room_for_two_workers_the_scanners_run_in_this_process(tmp_path, monkeypatch):
    """No pool is created when fewer than two workers fit; the findings are those of the in-process run."""
    (tmp_path / "bad.py").write_text("def f(x=[]):\n    return x\n", encoding="utf-8")
    monkeypatch.setattr(registry, "_workers_for_memory", lambda *a, **k: 1)

    def no_pool(*args, **kwargs):
        raise AssertionError("a process pool was created although fewer than two workers fit")

    import concurrent.futures

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", no_pool)
    selected = ["mutable_default", "broad_except_swallow"][:2]
    selected = [name for name in selected if name in registry._SCANNERS] or list(registry._SCANNERS)[:2]
    findings, failed = registry._run_scanners_parallel(selected, tmp_path, frozenset())
    assert failed == {}
    expected, _ = registry.run_all_checked(tmp_path, checks=selected, parallel=False)
    assert sorted((f.check, f.file, f.line) for f in findings) == sorted((f.check, f.file, f.line) for f in expected if f.check != "unparsable")
