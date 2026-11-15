"""A GPU-versus-host parity test runs on two or more independent seeds or datasets, not on the one that happened to pass.

CUDA.md section 10 item 6 of the mlframe repository: "GPU-vs-host equality must pass on 2 or more independent seeds/datasets, not one fixture". The rule was written after an incident in which a kernel
(``_fe_accuracy_gate_kernel.py``, 2026-11-11) converged on the first seed tried and diverged on 4 of 100 combinations of a second one. A parity test with one seed proves nothing about that class.

* ``cuda-parity-single-seed`` (advisory, then a ratchet): a ``test*`` function that (a) calls or compares a device-named callable (``gpu``, ``cuda``, ``cupy``, ``device``) AND a host-named one
  (``cpu``, ``njit``, ``host``, ``reference``), or passes ``backend="cuda"`` / ``"njit"`` style keyword strings, (b) compares them (``allclose``, ``array_equal``, ``assert_*equal``, ``approx``,
  ``isclose``), (c) draws random data (``default_rng``, ``RandomState``, ``np.random.*``, generator methods) and (d) has fewer than two seed sources. A seed source is a function parameter (a ``parametrize`` argument or a fixture) named like ``seed`` / ``rng`` / ``dataset`` / ``data`` / ``case``, a loop (or comprehension) over such a name or whose body builds a generator from a non-literal seed, or a second distinct integer
  literal given to a generator constructor / ``seed=`` / ``random_state=``.
* Excluded by name: tests whose name says they are not parity tests (``fall``, ``dispatch``, ``thresh``, ``downgrad``, ``demot``, ``fail``, ``clamp``, ``probe``, ``oom``, ``optout``, ``without``, ``skip``). A test that draws no random data
  (an adversarial-value test) is not reported.
* Suppression: ``# parity-ok: <reason>`` on any line of the test function (an adversarial-value test has no seed to vary). An empty reason does not suppress.
* Baseline: 93 percent of the broad population is red on day one, so use ``baseline_path`` (a shrink-only ratchet) from the start.

Measured on mlframe tests (audit 2026-11-16, finding 54-T11): 72 parity tests after the filters above, 67 of them with one seed or none; of the first five, 3 true and 2 unclear (the data may come from
a fixture). On a later snapshot of the same corpus this implementation reports 62; before the failure/demotion/clamp/probe name exclusions were added, 2 of its first 5 were not parity tests (a staging-buffer clamp test, failure-injection tests); after, the first 5 were all parity tests, and the next ones include 2 whose data may come from a fixture. Residual false positives are those fixture-fed or name-unmarked non-parity tests (measured about 20 to 40 percent on a hand judgement of 10 hits). Not implemented: the spec's ``cuda-parity-single-dtype`` (needs the
``{REAL}`` module link) and ``cuda-parity-no-boundary-sizes`` (a third-party claim, never prototyped).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_parity_test_shape import assert_cuda_parity_test_shape

    def test_gpu_parity_tests_use_two_seeds():
        assert_cuda_parity_test_shape("tests", baseline_path="tests/parity_seed_baseline.json", min_files=50)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, nodes_of, scan_python

__all__ = ["MARKER", "RULE", "assert_cuda_parity_test_shape", "find_cuda_parity_test_shape"]

RULE = "cuda-parity-single-seed"
MARKER = "parity-ok:"

_GPU = re.compile(r"gpu|cuda|cupy|device", re.IGNORECASE)
_HOST = re.compile(r"cpu|njit|host|reference", re.IGNORECASE)
_COMPARE = re.compile(r"allclose|array_equal|assert_.*equal|approx|isclose|^equal$|array_almost", re.IGNORECASE)
_NOT_PARITY = re.compile(r"fall|dispatch|thresh|downgrad|demot|fail|clamp|probe|oom|optout|without|skip", re.IGNORECASE)
_SEED_NAME = re.compile(r"seed|rng|dataset|data|case", re.IGNORECASE)
_RNG_CTORS = frozenset({"default_rng", "RandomState", "Generator", "seed", "manual_seed", "PCG64"})
_RNG_METHODS = frozenset({"rand", "randn", "randint", "normal", "uniform", "standard_normal", "integers", "permutation", "choice", "random_sample", "standard_t"})
_SEED_KEYWORDS = frozenset({"seed", "random_state", "random_seed"})
_BACKEND_KEYWORDS = frozenset({"backend", "device", "impl", "implementation", "engine", "mode"})


def _dotted(node: ast.AST) -> str:
    parts: "list[str]" = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _leaf(call: ast.Call) -> str:
    return _dotted(call.func).rpartition(".")[2]


def _names_in(node: ast.AST) -> "list[str]":
    """The identifiers and attribute names under *node* (a comparison's operands)."""
    out: "list[str]" = []
    for sub in nodes_of(node, ast.Name, ast.Attribute):
        out.append(sub.id if isinstance(sub, ast.Name) else sub.attr)
    return out


def _is_random(call: ast.Call) -> bool:
    leaf = _leaf(call)
    return leaf in _RNG_CTORS or leaf in _RNG_METHODS or ".random." in "." + _dotted(call.func) + "."


def _seed_sources(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> int:
    """How many independent seed sources the test has: 2 or more is enough."""
    count = 2 * sum(1 for a in (*fn.args.args, *fn.args.kwonlyargs) if _SEED_NAME.search(a.arg) and a.arg not in ("self",))
    for loop in nodes_of(fn, ast.For, ast.AsyncFor, ast.comprehension):
        assert isinstance(loop, (ast.For, ast.AsyncFor, ast.comprehension))
        target_names = [n.id for n in nodes_of(loop.target, ast.Name)]
        body_nodes: "list[ast.AST]" = list(loop.body) if isinstance(loop, (ast.For, ast.AsyncFor)) else list(loop.ifs)
        draws = any(_leaf(c) in _RNG_CTORS and any(not isinstance(a, ast.Constant) for a in c.args) for stmt in body_nodes for c in nodes_of(stmt, ast.Call))
        if any(_SEED_NAME.search(n) for n in target_names) or draws:
            count += 2  # a loop over seeds or datasets is two or more by construction
    literals: "set[int]" = set()
    for call in nodes_of(fn, ast.Call):
        if _leaf(call) in _RNG_CTORS:
            literals.update(a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, int) and not isinstance(a.value, bool))
        literals.update(
            k.value.value
            for k in call.keywords
            if k.arg in _SEED_KEYWORDS and isinstance(k.value, ast.Constant) and isinstance(k.value.value, int) and not isinstance(k.value.value, bool)
        )
    if len(literals) >= 2:
        count += len(literals)
    return count


def _marked(fn: "ast.FunctionDef | ast.AsyncFunctionDef", lines: "list[str]") -> bool:
    end = getattr(fn, "end_lineno", None) or fn.lineno
    for number in range(fn.lineno - len(fn.decorator_list) - 1, end + 1):
        if 0 < number <= len(lines):
            line = lines[number - 1]
            if "#" in line:
                comment = line[line.index("#") :]
                if MARKER in comment and comment.split(MARKER, 1)[1].strip():
                    return True
    return False


def _is_parity_test(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    gpu = host = compares = False
    calls = list(nodes_of(fn, ast.Call))
    for call in calls:
        leaf = _leaf(call)
        names = [leaf]
        if _COMPARE.search(leaf):
            compares = True
            names.extend(n for arg in (*call.args, *(k.value for k in call.keywords)) for n in _names_in(arg))
        names.extend(
            k.value.value for k in call.keywords if k.arg in _BACKEND_KEYWORDS and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)
        )
        for name in names:
            gpu = gpu or bool(_GPU.search(name))
            host = host or bool(_HOST.search(name))
    if not compares:
        compares = any(isinstance(n, ast.Assert) and isinstance(n.test, ast.Compare) for n in nodes_of(fn, ast.Assert))
    if not compares:
        return False
    return gpu and host and any(_is_random(c) for c in calls)


def find_cuda_parity_test_shape(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every GPU-vs-host parity test under *root* with fewer than two seed sources, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for a file that cannot be read (unless *allow_unparsed*).
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        lines = parsed.source.splitlines()
        for fn in nodes_of(parsed.tree, ast.FunctionDef, ast.AsyncFunctionDef):
            if not fn.name.startswith("test") or _NOT_PARITY.search(fn.name):
                continue
            if not _is_parity_test(fn) or _seed_sources(fn) >= 2 or _marked(fn, lines):
                continue
            out.append(
                Finding(
                    parsed.rel,
                    fn.lineno,
                    RULE,
                    f"parity test `{fn.name}` compares a device result with a host twin on fewer than two seeds or datasets: parametrize the seed, loop over seeds, or mark `# parity-ok: <reason>`",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_parity_test_shape(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a single-seed GPU-vs-host parity test, or with *baseline_path* on any finding the shrink-only baseline does not accept."""
    found = find_cuda_parity_test_shape(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "run the parity test on two or more independent seeds or datasets (CUDA.md 10.6), or mark it `# parity-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_parity_test_shape", refresh_command="PY_CI_SHARED_REFRESH=cuda_parity_test_shape")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} single-seed GPU parity test(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
