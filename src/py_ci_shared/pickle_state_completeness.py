"""Runtime caches and live handles that a pickle round trip (joblib fan-out, a saved model) cannot carry.

An object pickles fine in a smoke test and fails later in a real save or a joblib worker, because the attribute that
breaks it only exists after the object has been USED: a memo filled on first call, a lock or pool created lazily, a
compiled kernel cached on the instance. Two halves:

Runtime (:func:`assert_pickle_round_trips`): the consumer lists factories that build a representative object and,
optionally, a warm-up that uses it; each is built, warmed, pickled and unpickled, and the warm-up is run again on the
clone, so a cache that ``__getstate__`` dropped must also be rebuildable.

Static (:func:`find_pickle_state_gaps`), two rules, each only where a class has taken a position on its state:

* ``state-not-excluded``: the class defines ``__getstate__`` in the COPY form (it starts from ``self.__dict__``,
  ``vars(self)`` or ``super().__getstate__()``) and a method other than ``__init__``/``__setstate__`` assigns
  ``self.<name>`` where the name ENDS in a runtime-state noun (``_cache``, ``_memo``, ``_lock``, ``_pool``, ``_handle``,
  ``_buf``, ``_session``, ``_client``, ``_conn``, ``_executor``, ``_compiled``, ...) or starts with ``cached_``, and
  ``__getstate__`` never names
  it (a module- or class-level tuple/list/set of names it refers to counts as naming them; a flag or counter such
  as ``self._caches_warmed = False`` is not the cache). A ``__getstate__`` that builds its dict from explicit keys
  excludes everything it does not name, so it is not judged.
* ``unpicklable-without-getstate``: a class with no ``__getstate__``/``__reduce__``/``__reduce_ex__`` assigns
  ``self.<name>`` a lock, thread, pool, executor, open file, socket or DB connection OUTSIDE ``__init__``. (Inside
  ``__init__`` this is pyutilz's ``unpicklable_resource_state`` scanner; the lazily created one is what it misses.)

Existing findings are ratcheted by the baseline.
"""

from __future__ import annotations

import ast
import pickle
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ScanResult, scan_python
from ._gate_run import enforce_findings

__all__ = [
    "REFRESH_FLAG",
    "RoundTripFailure",
    "assert_no_pickle_state_gaps",
    "assert_pickle_round_trips",
    "find_pickle_state_gaps",
    "round_trip_failures",
]

REFRESH_FLAG = "--refresh-pickle-state-baseline"
GATE = "pickle-state"
RULE_NOT_EXCLUDED = "state-not-excluded"
RULE_NO_GETSTATE = "unpicklable-without-getstate"
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "testing", "benchmarks", "bench", "examples", "scripts"})
#: The noun a runtime-state attribute ends in (``_adjacency_cache``, ``_cache_lock``, ``session``); a name that only uses one
#: as a modifier (``_identity_cache_ycorr_``, a measured correlation) is not runtime state.
_RUNTIME_NOUNS = frozenset(
    {"cache", "caches", "memo", "memos", "lock", "rlock", "pool", "handle", "handles", "buf", "buffer", "buffers", "session"}
    | {"client", "conn", "connection", "executor", "compiled", "kernel", "kernels", "stream", "thread", "fh"}
)


def _is_runtime_name(name: str) -> bool:
    tokens = [t for t in name.lower().strip("_").split("_") if t]
    return bool(tokens) and (tokens[-1] in _RUNTIME_NOUNS or tokens[0] == "cached")


_UNPICKLABLE = frozenset(
    {
        "threading.Lock",
        "threading.RLock",
        "threading.Event",
        "threading.Condition",
        "threading.Semaphore",
        "threading.BoundedSemaphore",
        "threading.Thread",
        "multiprocessing.Lock",
        "multiprocessing.Pool",
        "multiprocessing.Process",
        "concurrent.futures.ThreadPoolExecutor",
        "concurrent.futures.ProcessPoolExecutor",
        "open",
        "io.open",
        "socket.socket",
        "sqlite3.connect",
        "tempfile.TemporaryFile",
        "tempfile.NamedTemporaryFile",
    }
)
_STATE_EXEMPT_METHODS = frozenset({"__init__", "__setstate__", "__new__", "__post_init__"})
_REDUCERS = frozenset({"__getstate__", "__reduce__", "__reduce_ex__"})


# ---------------------------------------------------------------------------------------------------------- runtime half


class RoundTripFailure:
    """One factory whose object did not survive build -> warm -> pickle -> unpickle -> warm."""

    __slots__ = ("error", "name", "stage")

    def __init__(self, name: str, stage: str, error: BaseException) -> None:
        self.name = name
        self.stage = stage
        self.error = error

    def render(self) -> str:
        return f"{self.name}: {self.stage} failed: {type(self.error).__name__}: {self.error}"


def round_trip_failures(
    factories: Mapping[str, Callable[[], Any]],
    *,
    warm: Optional[Mapping[str, Callable[[Any], Any]]] = None,
    protocol: int = pickle.HIGHEST_PROTOCOL,
    dumps: Callable[..., bytes] = pickle.dumps,
    loads: Callable[[bytes], Any] = pickle.loads,
) -> list[RoundTripFailure]:
    """Every factory that fails; *dumps*/*loads* may be swapped for cloudpickle or joblib's pickler."""
    out: list[RoundTripFailure] = []
    warmers = dict(warm or {})
    for name, factory in factories.items():
        stage = "build"
        try:
            obj = factory()
            warmer = warmers.get(name)
            stage = "warm"
            if warmer is not None:
                warmer(obj)
            stage = "pickle"
            blob = dumps(obj, protocol=protocol)
            stage = "unpickle"
            clone = loads(blob)
            stage = "warm after unpickle"
            if warmer is not None:
                warmer(clone)
        except Exception as exc:  # each factory is judged on its own; the failure IS the result
            out.append(RoundTripFailure(name, stage, exc))
    return out


def assert_pickle_round_trips(
    factories: Mapping[str, Callable[[], Any]],
    *,
    warm: Optional[Mapping[str, Callable[[Any], Any]]] = None,
    protocol: int = pickle.HIGHEST_PROTOCOL,
    dumps: Callable[..., bytes] = pickle.dumps,
    loads: Callable[[bytes], Any] = pickle.loads,
) -> None:
    """Fail when any factory's warmed object cannot be pickled, unpickled and used again. An empty mapping fails."""
    import pytest

    if not factories:
        pytest.fail("assert_pickle_round_trips got no factories: nothing was checked", pytrace=False)
    unknown = sorted(set(warm or {}) - set(factories))
    if unknown:
        pytest.fail(f"warm-ups name no factory: {unknown}", pytrace=False)
    failures = round_trip_failures(factories, warm=warm, protocol=protocol, dumps=dumps, loads=loads)
    if failures:
        pytest.fail(
            "objects that do not survive a pickle round trip once used (exclude runtime state in __getstate__ and rebuild it "
            "lazily):\n    " + "\n    ".join(f.render() for f in failures),
            pytrace=False,
        )


# ----------------------------------------------------------------------------------------------------------- static half


def _self_attr_targets(node: ast.AST) -> Iterable[tuple[str, ast.expr, int]]:
    """``(name, value, line)`` for each ``self.<name> = value`` (plain, annotated or augmented) under *node*."""
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
            targets, value = [n.target], n.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                yield t.attr, value, n.lineno


def _is_copy_form(getstate: ast.FunctionDef) -> bool:
    for n in ast.walk(getstate):
        if isinstance(n, ast.Attribute) and n.attr == "__dict__":
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "vars":
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "__getstate__":
            return True
    return False


def _string_collections(body: Iterable[ast.stmt]) -> dict[str, set[str]]:
    """``NAME = ("a", "b")`` (tuple/list/set/frozenset of strings, or a ``+``/``|`` of such names) at one scope level."""
    out: dict[str, set[str]] = {}
    for stmt in body:
        target = stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else getattr(stmt, "target", None)
        value = getattr(stmt, "value", None)
        if isinstance(target, ast.Name) and value is not None and not isinstance(stmt, ast.AugAssign):
            strings = {n.value for n in ast.walk(value) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            for ref in ast.walk(value):
                if isinstance(ref, ast.Name) and ref.id in out:
                    strings |= out[ref.id]
            if strings:
                out[target.id] = strings
    return out


def _names_in(getstate: ast.FunctionDef, collections: Mapping[str, set[str]]) -> set[str]:
    """Every name ``__getstate__`` mentions, with a referenced constant collection expanded to its strings."""
    out: set[str] = set()
    for n in ast.walk(getstate):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        elif isinstance(n, (ast.Attribute, ast.Name)):
            ident = n.attr if isinstance(n, ast.Attribute) else n.id
            out.add(ident)
            out |= collections.get(ident, set())
    return out


def _is_flag_value(value: ast.expr) -> bool:
    """``self._caches_warmed = False``: a flag or a counter ABOUT the cache, not the cache."""
    return isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, (bool, int, float)))


def _methods(cls: ast.ClassDef) -> dict[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
    return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _class_findings(rel: str, cls: ast.ClassDef, aliases: ImportAliases, module_collections: Mapping[str, set[str]]) -> list[Finding]:
    methods = _methods(cls)
    getstate = methods.get("__getstate__")
    out: list[Finding] = []
    lazy = [(m.name, name, value, line) for m in methods.values() if m.name not in _STATE_EXEMPT_METHODS for name, value, line in _self_attr_targets(m)]
    if isinstance(getstate, ast.FunctionDef) and _is_copy_form(getstate):
        named = _names_in(getstate, {**module_collections, **_string_collections(cls.body)})
        seen: set[str] = set()
        for method, name, value, line in lazy:
            if name in seen or name in named or not _is_runtime_name(name) or _is_flag_value(value):
                continue
            seen.add(name)
            message = f"{cls.name}.{method} sets runtime state self.{name}, which {cls.name}.__getstate__ copies (it never names it)"
            out.append(Finding(rel, line, RULE_NOT_EXCLUDED, message))
    elif not (_REDUCERS & set(methods)):
        for method, name, value, line in lazy:
            callee = aliases.qualified_name(value.func) if isinstance(value, ast.Call) else None
            if callee in _UNPICKLABLE:
                message = f"{cls.name}.{method} sets self.{name} = {callee}(...) and {cls.name} defines no __getstate__/__reduce__"
                out.append(Finding(rel, line, RULE_NO_GETSTATE, message))
    return out


def find_pickle_state_gaps(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` for both static rules over the production files under *root*."""
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        aliases = ImportAliases.from_tree(f.tree)
        module_collections = _string_collections(f.tree.body)
        for node in ast.walk(f.tree):
            if isinstance(node, ast.ClassDef):
                findings.extend(_class_findings(f.rel, node, aliases, module_collections))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_no_pickle_state_gaps(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a static finding not accepted by *baseline_path*. Missing baseline fails; refresh with ``REFRESH_FLAG``
    or ``PY_CI_SHARED_REFRESH=pickle-state``."""
    findings, scan = find_pickle_state_gaps(root, exclude_parts=exclude_parts, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="drop the attribute in __getstate__ (and rebuild it lazily), or add __getstate__/__setstate__",
    )
