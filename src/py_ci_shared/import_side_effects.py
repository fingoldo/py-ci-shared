"""Importing a module has no side effects: no network, no environment writes, no forbidden files.

Two halves of one rule, which the consuming repos carried as separate copies.

THE PROBE. Importing a package must not open a socket, call ``urlopen``, write ``os.environ`` or open a database
file. A module that does so at import time blocks every ``import`` on the network, fails offline and in sandboxes,
and multiplies the side effect by every pytest-xdist worker that collects it. pyutilz, glossum and llm_bench each ran
a subprocess that patched those primitives and imported the package. They had drifted: pyutilz printed a violation a
module had swallowed and exited 0; its environment block had been written and then switched off; only glossum
failed on a swallowed violation. :func:`assert_imports_have_no_side_effects` runs the one probe, and any recorded
violation fails whether or not the module caught the exception.

THE SCAN. A TEST module that writes ``os.environ`` at import time leaks the write into every later test the same
xdist worker runs, and which tests those are changes with the distribution -- so it fails a different innocent file
each run (mlframe: a module-level ``os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")`` disabled the GPU for ~70
tests). The probe cannot see test modules, which pytest imports; :func:`assert_no_new_import_time_env_mutations`
reads them instead. Existing sites may sit in a baseline keyed by file and statement text, not line number, so a
comment added above one does not re-report it; a baseline entry nothing reproduces fails.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ENV_REFRESH_FLAG",
    "ProbeResult",
    "assert_imports_have_no_side_effects",
    "assert_no_new_import_time_env_mutations",
    "find_import_time_env_mutations",
    "import_time_env_mutations",
    "probe_import_side_effects",
]

ENV_REFRESH_FLAG = "--refresh-import-env-mutation-baseline"

_MUTATING_METHODS = frozenset({"setdefault", "update", "pop", "clear", "popitem"})
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return True
    return isinstance(node, ast.Name) and node.id == "environ"


def _import_time_nodes(tree: ast.Module):
    """Every node evaluated at import time: module-level ``if``/``try``/``with``/``for`` bodies, never a callable's."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_NODES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def import_time_env_mutations(tree: ast.Module) -> list[tuple[int, str]]:
    """``[(line, statement text), ...]`` for each import-time ``os.environ`` write, ``del`` or ``putenv``/``unsetenv``."""
    out: list[tuple[int, str]] = []
    for node in _import_time_nodes(tree):
        hit = False
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            hit = any((isinstance(t, ast.Subscript) and _is_environ(t.value)) or (isinstance(node, ast.AugAssign) and _is_environ(t)) for t in targets)
        elif isinstance(node, ast.Delete):
            hit = any(isinstance(t, ast.Subscript) and _is_environ(t.value) for t in node.targets)
        elif isinstance(node, ast.Call):
            f = node.func
            hit = isinstance(f, ast.Attribute) and ((f.attr in _MUTATING_METHODS and _is_environ(f.value)) or f.attr in ("putenv", "unsetenv"))
        if hit:
            out.append((node.lineno, ast.unparse(node)[:160]))
    return sorted(out)


def find_import_time_env_mutations(root: Path, *, pattern: str = "test_*.py") -> tuple[int, list[str]]:
    """``(files parsed, ["<relpath>::<statement>", ...])`` under *root*; repeats in one file are numbered ``#1``, ``#2``."""
    parsed = 0
    keys: list[str] = []
    for path in sorted(root.rglob(pattern)):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        parsed += 1
        rel = path.relative_to(root).as_posix()
        seen: Counter[str] = Counter()
        for _line, text in import_time_env_mutations(tree):
            base = f"{rel}::{text}"
            keys.append(base if seen[base] == 0 else f"{base}#{seen[base]}")
            seen[base] += 1
    return parsed, keys


def assert_no_new_import_time_env_mutations(
    root: Path,
    baseline_path: Path | None = None,
    *,
    pattern: str = "test_*.py",
    min_files: int = 1,
    refresh_flag: str = ENV_REFRESH_FLAG,
) -> None:
    """Fail on an import-time environment write not in the baseline, on a stale baseline entry, or on an empty scan.

    Without *baseline_path* every site fails. With it, passing *refresh_flag* on the pytest command line rewrites it.
    """
    import pytest

    parsed, keys = find_import_time_env_mutations(root, pattern=pattern)
    if parsed < min_files:
        pytest.fail(f"only {parsed} file(s) matching {pattern!r} parsed under {root}; expected at least {min_files} -- Check root, the scan has lost its subject")
    current = set(keys)
    if baseline_path is not None and refresh_flag in sys.argv:
        baseline_path.write_text(json.dumps(sorted(current), indent=2) + "\n", encoding="utf-8")
        pytest.skip(f"{baseline_path.name} rewritten with {len(current)} entr(ies)")
    known: set[str] = set(json.loads(baseline_path.read_text(encoding="utf-8"))) if baseline_path is not None and baseline_path.exists() else set()
    new = sorted(current - known)
    stale = sorted(known - current)
    problems: list[str] = []
    if new:
        problems.append(
            f"{len(new)} module(s) write os.environ at IMPORT time, which leaks into every later test the same xdist worker "
            "runs. Use monkeypatch.setenv in the test or a fixture; if a variable must be set before a module-scope import, "
            "set it in conftest.py or move the import into the test:\n  " + "\n  ".join(new)
        )
    if stale:
        problems.append(f"{len(stale)} baseline entr(ies) no longer reproduced -- Remove them, or refresh with {refresh_flag}:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))


_RESULT_TAG = "IMPORT_SIDE_EFFECT_PROBE_RESULT:"
_MARK = "IMPORT-SIDE-EFFECT"

_PROBE = r"""
import builtins, json, os, socket, sys

cfg = json.loads(sys.argv[1])
violations = []
_PLUMBING = {"os", "_collections_abc", "socket", "urllib.request", "builtins", "io", "codecs", "__main__"}


def _origin():
    # The module whose code made the call: the innermost frame outside this probe and the stdlib plumbing it patches.
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if frame.f_code.co_filename != "<string>" and name not in _PLUMBING:
            return name
        frame = frame.f_back
    return ""


def _record(what):
    violations.append([what, _origin()])


if cfg["block_network"]:
    class _BlockedSocket(socket.socket):
        def __init__(self, *a, **kw):
            _record(f"socket.socket(*{a!r}, **{kw!r})")
            raise RuntimeError("IMPORT-SIDE-EFFECT: socket opened at import time")

    socket.socket = _BlockedSocket
    import urllib.request

    def _blocked_urlopen(*a, **kw):
        _record(f"urllib.request.urlopen({a[:1]!r})")
        raise RuntimeError("IMPORT-SIDE-EFFECT: urlopen called at import time")

    urllib.request.urlopen = _blocked_urlopen

if cfg["block_environ"]:
    allowed = set(cfg["allowed_env_keys"])
    environ_cls = type(os.environ)
    _set, _del = environ_cls.__setitem__, environ_cls.__delitem__

    def _logged_set(self, key, value):
        if key not in allowed:
            _record(f"os.environ[{key!r}] = {value!r}")
        _set(self, key, value)

    def _logged_del(self, key):
        if key not in allowed:
            _record(f"del os.environ[{key!r}]")
        _del(self, key)

    environ_cls.__setitem__, environ_cls.__delitem__ = _logged_set, _logged_del

if cfg["forbid_open_tokens"]:
    _open = builtins.open

    def _guarded_open(file, *a, **kw):
        text = str(file)
        for token in cfg["forbid_open_tokens"]:
            if token in text:
                _record(f"open({text!r})")
                raise RuntimeError(f"IMPORT-SIDE-EFFECT: open() of {text!r} at import time")
        return _open(file, *a, **kw)

    builtins.open = _guarded_open

failed, unimportable = [], []
for target in cfg["targets"]:
    try:
        __import__(target)
    except ImportError as e:
        unimportable.append(f"{target}: ImportError: {e}")
    except Exception as e:
        (failed if "IMPORT-SIDE-EFFECT" in str(e) else unimportable).append(f"{target}: {type(e).__name__}: {e}")

print("IMPORT_SIDE_EFFECT_PROBE_RESULT:" + json.dumps({"violations": violations, "failed": failed, "unimportable": unimportable}))
"""


@dataclass
class ProbeResult:
    """What one probe run recorded: violations (even ones a module swallowed), failed imports, unimportable targets.

    Each violation is ``(what happened, the module whose code did it)``.
    """

    violations: list[tuple[str, str]] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unimportable: list[str] = field(default_factory=list)
    crashed: list[str] = field(default_factory=list)

    def merge(self, other: ProbeResult) -> None:
        """Fold *other* into this result."""
        self.violations += other.violations
        self.failed += other.failed
        self.unimportable += other.unimportable
        self.crashed += other.crashed


def _run_probe(targets: list[str], cfg: dict, timeout: float, python: str, cwd: Path | None, env: dict[str, str] | None) -> ProbeResult:
    payload = json.dumps({**cfg, "targets": targets})
    try:
        proc = subprocess.run([python, "-c", _PROBE, payload], capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return ProbeResult(crashed=[f"{targets}: no result within {timeout}s"])
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_RESULT_TAG):
            data = json.loads(line[len(_RESULT_TAG) :])
            return ProbeResult([(what, origin) for what, origin in data["violations"]], data["failed"], data["unimportable"])
    return ProbeResult(crashed=[f"{targets}: exit {proc.returncode} with no result line; stderr tail: {proc.stderr[-800:]}"])


def probe_import_side_effects(
    targets: Iterable[str],
    *,
    block_network: bool = True,
    block_environ: bool = False,
    allowed_env_keys: Iterable[str] = (),
    forbid_open_tokens: Iterable[str] = (),
    isolate: bool = False,
    timeout: float = 600,
    python: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> ProbeResult:
    """Import *targets* in a fresh interpreter with the chosen primitives patched, and report what happened.

    ``isolate=True`` gives each target its own interpreter, so one module's imports cannot hide another's side
    effect behind a cached import; the processes run on a thread pool, since each one waits without the GIL.
    """
    names = list(targets)
    cfg = {"block_network": block_network, "block_environ": block_environ, "allowed_env_keys": sorted(allowed_env_keys), "forbid_open_tokens": list(forbid_open_tokens)}
    exe = python or sys.executable
    run_env = env if env is not None else dict(os.environ)
    result = ProbeResult()
    if not isolate:
        result.merge(_run_probe(names, cfg, timeout, exe, cwd, run_env))
        return result
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(names)))) as pool:
        for part in pool.map(lambda t: _run_probe([t], cfg, timeout, exe, cwd, run_env), names):
            result.merge(part)
    return result


def _owned(origin: str, first_party: tuple[str, ...]) -> bool:
    return any(origin == pkg or origin.startswith(pkg + ".") for pkg in first_party)


def assert_imports_have_no_side_effects(
    targets: Iterable[str],
    *,
    first_party: Iterable[str] | None = None,
    tolerate_unimportable: bool = False,
    **probe_kw,
) -> None:
    """Fail on a recorded side effect, a crashed probe, or (unless tolerated) a target that could not be imported.

    *first_party* names the packages whose own code is judged. A violation made by a dependency's code -- urllib3
    opening a socket to test for IPv6, numpy setting an OpenBLAS variable -- is not the importing package's to fix,
    so with *first_party* given only violations made by those packages' modules count. ``None`` counts everything.
    An import that FAILED because of a block always counts: the target cannot be imported offline. Remaining keyword
    arguments go to :func:`probe_import_side_effects`.
    """
    import pytest

    names = list(targets)
    if not names:
        pytest.fail("no import targets given -- Add the package and the subpackages worth probing")
    result = probe_import_side_effects(names, **probe_kw)
    owners = tuple(first_party) if first_party is not None else None
    counted = sorted({f"{what}  (from {origin or '?'})" for what, origin in result.violations if owners is None or _owned(origin, owners)})
    problems: list[str] = []
    if counted or result.failed:
        problems.append("side effects at import time (Move them behind an explicit call the caller makes):\n  " + "\n  ".join(counted + result.failed))
    if result.crashed:
        problems.append("the probe itself did not finish -- Check the interpreter and timeout:\n  " + "\n  ".join(result.crashed))
    if result.unimportable and not tolerate_unimportable:
        problems.append(
            "targets that could not be imported, so were not probed -- Fix the import, drop the target, or pass tolerate_unimportable=True for "
            "optional dependencies:\n  " + "\n  ".join(result.unimportable)
        )
    if result.unimportable and tolerate_unimportable and len(result.unimportable) == len(names):
        problems.append("every target was unimportable, so nothing was probed -- Check the environment:\n  " + "\n  ".join(result.unimportable))
    if problems:
        pytest.fail("\n".join(problems))
