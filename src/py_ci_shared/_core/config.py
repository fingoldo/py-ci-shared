"""``[tool.py_ci_shared]``: the per-repo table that enables gates and gives them their arguments.

Shape::

    [tool.py_ci_shared]
    enable = ["naive_utcnow", "identity_comparisons"]   # gates run with their defaults (plus path injection)
    budget = "warn"                                      # "warn" (default), "fail" or "off"
    resource_leak_guard = true                           # also load the py_ci_shared.resource_leak_guard pytest plugin

    [tool.py_ci_shared.gates.function_length]            # a table enables the gate and holds its kwargs
    files = ["src/**/*.py"]
    root = "."
    baseline_path = "tests/baselines/function_length.json"

    [tool.py_ci_shared.gates.rounds_format]              # a second run of one module under another name
    module = "audit_round_format"
    entry = "assert_rounds_countable"
    audits_dir = "audits/2026-09-24"

Reserved keys in a gate table (not passed to the gate): ``module``, ``entry``, ``budget_s``, ``enabled``.
Every other key is a keyword argument. Paths are relative to the repo root (see :func:`resolve_kwargs`).
"""

from __future__ import annotations

import glob as _glob
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import CoreError

__all__ = [
    "BUDGET_MODES",
    "ConfigError",
    "GateRun",
    "RepoConfig",
    "find_repo_root",
    "load_config",
    "resolve_kwargs",
]

BUDGET_MODES = ("warn", "fail", "off")
RESERVED_KEYS = frozenset({"module", "entry", "budget_s", "enabled"})

# Keyword names whose value is one path, and those whose value is a list of paths (globs expanded).
_PATH_KEYS = frozenset({"root", "repo", "repo_root", "path", "tracker", "verifier", "package_root", "readme_path", "manifest_path"})
_PATH_SUFFIXES = ("_dir", "_path", "_root")
_PATH_LIST_KEYS = frozenset({"files", "md_files", "roots", "scan_roots", "package_roots", "test_files", "paths", "audit_files", "test_paths", "catalogues"})
# Parameters the runner fills with the repo root when the table leaves them out.
_ROOT_PARAMS = ("repo_root", "root", "repo", "project_root")


class ConfigError(CoreError):
    """``[tool.py_ci_shared]`` is malformed or names something that does not exist."""


@dataclass(frozen=True)
class GateRun:
    """One enabled gate: which module and entry function to call, with which keyword arguments."""

    name: str
    module: str
    entry: Optional[str]
    kwargs: dict[str, Any] = field(default_factory=dict)
    budget_s: Optional[float] = None


@dataclass(frozen=True)
class RepoConfig:
    """The parsed ``[tool.py_ci_shared]`` table of one repo."""

    repo_root: Path
    gates: tuple[GateRun, ...]
    budget: str = "warn"
    resource_leak_guard: bool = False

    def gate(self, name: str) -> GateRun:
        for run in self.gates:
            if run.name == name:
                return run
        raise ConfigError(f"gate {name!r} is not enabled in [tool.py_ci_shared] of {self.repo_root / 'pyproject.toml'}")


def find_repo_root(start: Optional[Path] = None) -> Path:
    """The nearest directory at or above *start* (default: cwd) holding a ``pyproject.toml``."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ConfigError(f"no pyproject.toml at or above {here}")


def _bool_key(pyproject: Path, table: dict[str, Any], key: str) -> bool:
    value = table.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{pyproject}: [tool.py_ci_shared] {key} = {value!r}; expected true or false")
    return value


def load_config(repo_root: Path) -> Optional[RepoConfig]:
    """The repo's ``[tool.py_ci_shared]`` table, or None when the repo has none (the plugin then stays silent)."""
    from .._toml_compat import tomllib

    pyproject = Path(repo_root) / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{pyproject}: not valid TOML: {exc}") from exc
    table = data.get("tool", {}).get("py_ci_shared")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ConfigError(f"{pyproject}: [tool.py_ci_shared] must be a table")
    budget = table.get("budget", "warn")
    if budget not in BUDGET_MODES:
        raise ConfigError(f"{pyproject}: [tool.py_ci_shared] budget = {budget!r}; expected one of {BUDGET_MODES}")
    unknown = set(table) - {"enable", "budget", "gates", "resource_leak_guard"}
    if unknown:
        raise ConfigError(f"{pyproject}: unknown key(s) in [tool.py_ci_shared]: {sorted(unknown)}")
    leak_guard = _bool_key(pyproject, table, "resource_leak_guard")
    runs = _gate_runs(pyproject, table)
    return RepoConfig(repo_root=Path(repo_root), gates=tuple(runs), budget=budget, resource_leak_guard=leak_guard)


def _gate_runs(pyproject: Path, table: dict[str, Any]) -> list[GateRun]:
    """The ``enable`` list, then one run per ``[tool.py_ci_shared.gates.<name>]`` table not set ``enabled = false``."""
    runs: list[GateRun] = [GateRun(name=name, module=name, entry=None) for name in table.get("enable", [])]
    for name, section in table.get("gates", {}).items():
        if not isinstance(section, dict):
            raise ConfigError(f"{pyproject}: [tool.py_ci_shared.gates.{name}] must be a table")
        if section.get("enabled", True) is False:
            continue
        if any(r.name == name for r in runs):
            raise ConfigError(f"{pyproject}: {name!r} is both in `enable` and has a gates table; keep one")
        budget_s = section.get("budget_s")
        runs.append(
            GateRun(
                name=name,
                module=str(section.get("module", name)),
                entry=section.get("entry"),
                kwargs={k: v for k, v in section.items() if k not in RESERVED_KEYS},
                budget_s=float(budget_s) if budget_s is not None else None,
            )
        )
    return runs


def _is_path_key(key: str) -> bool:
    return key in _PATH_KEYS or key.endswith(_PATH_SUFFIXES)


def _annotation(param: Optional[inspect.Parameter]) -> str:
    if param is None or param.annotation is inspect.Parameter.empty:
        return ""
    ann = param.annotation
    return ann if isinstance(ann, str) else repr(ann)


_COLLECTION_WORDS = ("Iterable", "Sequence", "list", "List", "tuple", "Tuple", "set", "Set", "Collection")


def _outside_collections(ann: str) -> str:
    """*ann* with every ``Iterable[...]``/``list[...]``/... subscript cut out, brackets matched."""
    out, i = [], 0
    while i < len(ann):
        word = next((w for w in _COLLECTION_WORDS if ann.startswith(w + "[", i) and (i == 0 or not (ann[i - 1].isalnum() or ann[i - 1] == "_"))), None)
        if word is None:
            out.append(ann[i])
            i += 1
            continue
        depth, j = 0, i + len(word)
        while j < len(ann):
            depth += {"[": 1, "]": -1}.get(ann[j], 0)
            j += 1
            if depth == 0:
                break
        i = j
    return "".join(out)


def _wants_paths(key: str, param: Optional[inspect.Parameter]) -> tuple[bool, bool]:
    """``(one path, list of paths)`` for *key*: from the parameter's annotation when it names ``Path``, else its name.

    Both are true for ``Union[str, Path, Iterable[...]]``: one path or many, so a plain string stays one path (a
    directory the gate enumerates) and only a glob or a list becomes a list."""
    ann = _annotation(param)
    if "Path" in ann:
        many = any(word in ann for word in _COLLECTION_WORDS)
        return ((not many) or "Path" in _outside_collections(ann), many)
    return (_is_path_key(key), key in _PATH_LIST_KEYS)


def _expand(repo_root: Path, item: str) -> list[Path]:
    if any(ch in item for ch in "*?["):
        return sorted(Path(p) for p in _glob.glob(str(repo_root / item), recursive=True) if "__pycache__" not in Path(p).parts)
    return [repo_root / item]


def resolve_kwargs(func: Callable[..., Any], kwargs: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Turn TOML values into the call's arguments.

    A parameter annotated with ``Path`` gets paths: a string becomes ``repo_root / value``, a list becomes paths with
    globs expanded (``**`` recursive). Without an annotation the key's name decides: ``root``, ``repo_root``,
    ``*_dir``, ``*_path``, ``*_root`` ... take one path, ``files``, ``roots``, ``md_files`` ... a list. A parameter
    named ``repo_root``/``root``/``repo``/``project_root`` that the table leaves out is set to the repo root.
    Unknown keys raise :class:`ConfigError` naming the signature.
    """
    params = inspect.signature(func).parameters
    accepts_any = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    unknown = [k for k in kwargs if k not in params and not accepts_any]
    if unknown:
        raise ConfigError(f"{func.__module__}.{func.__name__} takes no argument(s) {unknown}; it takes {list(params)}")
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        one, many = _wants_paths(key, params.get(key))
        if isinstance(value, str) and (one or many):
            out[key] = repo_root / value if one and not (many and any(ch in value for ch in "*?[")) else _expand(repo_root, value)
        elif isinstance(value, list) and (one or many) and all(isinstance(v, str) for v in value):
            out[key] = [p for v in value for p in _expand(repo_root, v)]
        else:
            out[key] = value
    for name in _ROOT_PARAMS:
        if name in params and name not in out and params[name].kind != inspect.Parameter.VAR_KEYWORD:
            out[name] = repo_root
    return out
