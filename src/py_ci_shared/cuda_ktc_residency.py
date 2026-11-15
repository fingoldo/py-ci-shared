"""A GPU-capable kernel-tuning-cache registration declares the data placements its callers can have, or says why it has one.

A host-versus-device crossover measured with host-resident inputs is "only valid for the data placement the sweep used" (CUDA.md section 4): a caller that already holds device arrays gets
the host-in threshold. The kernel-tuning cache (pyutilz ``kernel_tuner(..., gpu_capable=True)``) therefore asks a GPU-capable entry to declare its placements (``residencies=((in, out), ...)``),
or the module to declare them (``_SWEEP_RESIDENCIES``), or to state that it has a single boundary and why. Measured (audit 2026-11-16, finding 54-T4 / G13): 71 of 72 GPU-capable registrations
of mlframe declare nothing. Detection is exact AST, no heuristic; the cost is a baseline.

* ``cuda-ktc-residency-undeclared`` (ADVISORY, a ratchet): a call of the registration API (``kernel_tuner``, also under an import alias, ``module.kernel_tuner`` or a bare name) whose
  ``gpu_capable`` keyword is not the constant ``False`` and that is not declared by any of:
  the ``residencies=`` keyword (also inside a ``**spec`` splat of a dict built once in the same scope or the module, literal or ``dict(...)``); a module-level binding of ``_SWEEP_RESIDENCIES``;
  or ``# residency-ok: <reason>`` on a line of the call (the reason names the single boundary). A ``**spec`` the gate cannot resolve is reported: it cannot be read. A ``gpu_capable`` that is
  not a constant (``gpu_capable=_HAS_CUPY``) counts as capable.

The registration is named by its ``kernel_name`` literal (otherwise the unparsed expression), and a baseline entry is keyed ``rule::path::registration``, so moving the call does not rekey it.

Every name is a parameter (``api_names``, ``gpu_keyword``, ``name_keyword``, ``declaring_keywords``, ``module_declarations``, ``marker``) with the pyutilz/mlframe names as defaults, so the gate
works for any consumer of that API.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_ktc_residency import assert_cuda_ktc_residency

    def test_gpu_registrations_declare_residency():
        assert_cuda_ktc_residency("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, child_nodes, nodes_of, scan_python

__all__ = ["MARKER", "RULE", "assert_cuda_ktc_residency", "find_cuda_ktc_residency"]

RULE = "cuda-ktc-residency-undeclared"
MARKER = "residency-ok:"

DEFAULT_API_NAMES = ("kernel_tuner",)
DEFAULT_GPU_KEYWORD = "gpu_capable"
DEFAULT_NAME_KEYWORD = "kernel_name"
DEFAULT_DECLARING_KEYWORDS = ("residencies",)
DEFAULT_MODULE_DECLARATIONS = ("_SWEEP_RESIDENCIES",)


def _bound_once(scope: ast.AST) -> "dict[str, Optional[ast.expr]]":
    """``{name: value}`` for the plain assignments directly in *scope* (not in nested defs); a name bound twice maps to ``None``."""
    out: "dict[str, Optional[ast.expr]]" = {}
    todo = list(child_nodes(scope))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = None if target.id in out else node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = None if node.target.id in out else node.value
        todo.extend(child_nodes(node))
    return out


def _dict_keys(value: Optional[ast.expr]) -> "Optional[set[str]]":
    """The string keys of a dict literal or ``dict(k=...)`` call; ``None`` when the value is anything else or has a non-literal key."""
    if isinstance(value, ast.Dict):
        keys: "set[str]" = set()
        for key in value.keys:
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            keys.add(key.value)
        return keys
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict" and not value.args and all(k.arg for k in value.keywords):
        return {k.arg for k in value.keywords if k.arg}
    return None


def _dict_value(value: Optional[ast.expr], key: str) -> Optional[ast.expr]:
    if isinstance(value, ast.Dict):
        for k, v in zip(value.keys, value.values):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == key:
                return kw.value
    return None


def _is_api_call(call: ast.Call, aliases: ImportAliases, api_names: "tuple[str, ...]") -> bool:
    qualified = aliases.qualified_name(call.func)
    if qualified is not None:
        return qualified.rsplit(".", 1)[-1] in api_names
    func = call.func
    return (isinstance(func, ast.Name) and func.id in api_names) or (isinstance(func, ast.Attribute) and func.attr in api_names)


def _marked(call: ast.Call, py_lines: "list[str]") -> bool:
    last = getattr(call, "end_lineno", None) or call.lineno
    for number in range(call.lineno, last + 1):
        if 0 < number <= len(py_lines):
            line = py_lines[number - 1]
            if "#" in line:
                comment = line[line.index("#") :]
                match = re.search(re.escape(MARKER) + r"\s*(\S)", comment)
                if match:
                    return True
    return False


def _lookup(scopes: "list[dict[str, Optional[ast.expr]]]", name: str) -> Optional[ast.expr]:
    for scope in scopes:
        if name in scope:
            return scope[name]
    return None


def _effective_keywords(call: ast.Call, scopes: "list[dict[str, Optional[ast.expr]]]") -> "tuple[dict[str, ast.expr], bool]":
    """The keywords of *call*, with those of every ``**spec`` splat that resolves to a dict literal or ``dict(...)`` call; and whether a splat could not be resolved."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    unresolved = False
    for kw in call.keywords:
        if kw.arg is not None:
            continue
        spec = _lookup(scopes, kw.value.id) if isinstance(kw.value, ast.Name) else kw.value
        keys = _dict_keys(spec)
        if keys is None:
            unresolved = True
            continue
        for key in keys:
            keywords.setdefault(key, _dict_value(spec, key) or ast.Constant(value=True))
    return keywords, unresolved


def _registration_name(keywords: "dict[str, ast.expr]", name_keyword: str, scopes: "list[dict[str, Optional[ast.expr]]]") -> str:
    reg: Optional[ast.expr] = keywords.get(name_keyword)
    if isinstance(reg, ast.Name):
        reg = _lookup(scopes, reg.id) or reg
    if isinstance(reg, ast.Constant) and isinstance(reg.value, str):
        return reg.value
    return ast.unparse(reg) if reg is not None else "<unnamed>"


def _findings_in(
    tree: ast.Module,
    rel: str,
    py_lines: "list[str]",
    *,
    api_names: "tuple[str, ...]",
    gpu_keyword: str,
    name_keyword: str,
    declaring_keywords: "tuple[str, ...]",
    module_declarations: "tuple[str, ...]",
) -> "list[Finding]":
    aliases = ImportAliases.from_tree(tree)
    module_scope = _bound_once(tree)
    module_declared = any(name in module_scope for name in module_declarations)
    scope_of: "dict[int, dict[str, Optional[ast.expr]]]" = {}
    for fn in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        local = _bound_once(fn)
        for call in nodes_of(fn, ast.Call):
            scope_of[id(call)] = local  # the innermost function wins: nodes_of lists outer functions first
    out: "list[Finding]" = []
    for call in nodes_of(tree, ast.Call):
        if not _is_api_call(call, aliases, api_names):
            continue
        scopes: "list[dict[str, Optional[ast.expr]]]" = [scope_of.get(id(call), {}), module_scope]
        keywords, unresolved_splat = _effective_keywords(call, scopes)
        gpu = keywords.get(gpu_keyword)
        if isinstance(gpu, ast.Name):
            gpu = _lookup(scopes, gpu.id) or gpu
        if gpu is None and not unresolved_splat:
            continue  # no gpu_capable at all: a CPU-only registration
        if isinstance(gpu, ast.Constant) and gpu.value is False:
            continue
        if any(k in keywords for k in declaring_keywords) or module_declared or _marked(call, py_lines):
            continue
        name = _registration_name(keywords, name_keyword, scopes)
        why = (
            "its `**` keyword set cannot be read statically"
            if unresolved_splat and gpu is None
            else f"it sets {gpu_keyword} and passes none of {', '.join(declaring_keywords)}"
        )
        out.append(
            Finding(
                rel,
                call.lineno,
                RULE,
                f"registration `{name}`: {why}; declare the placements its callers can have ({declaring_keywords[0]}=((in, out), ...) or a module-level {module_declarations[0]}) or mark the call `# residency-ok: <the single boundary and why>`",
                key=f"{RULE}::{rel}::{name}",
            )
        )
    return out


def find_cuda_ktc_residency(
    root: Union[str, Path],
    *,
    api_names: "tuple[str, ...]" = DEFAULT_API_NAMES,
    gpu_keyword: str = DEFAULT_GPU_KEYWORD,
    name_keyword: str = DEFAULT_NAME_KEYWORD,
    declaring_keywords: "tuple[str, ...]" = DEFAULT_DECLARING_KEYWORDS,
    module_declarations: "tuple[str, ...]" = DEFAULT_MODULE_DECLARATIONS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every GPU-capable registration under *root* that declares no residency scenario, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for an unreadable file (unless *allow_unparsed*)."""
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(
            _findings_in(
                parsed.tree,
                parsed.rel,
                parsed.source.splitlines(),
                api_names=api_names,
                gpu_keyword=gpu_keyword,
                name_keyword=name_keyword,
                declaring_keywords=declaring_keywords,
                module_declarations=module_declarations,
            )
        )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_ktc_residency(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    api_names: "tuple[str, ...]" = DEFAULT_API_NAMES,
    gpu_keyword: str = DEFAULT_GPU_KEYWORD,
    name_keyword: str = DEFAULT_NAME_KEYWORD,
    declaring_keywords: "tuple[str, ...]" = DEFAULT_DECLARING_KEYWORDS,
    module_declarations: "tuple[str, ...]" = DEFAULT_MODULE_DECLARATIONS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any GPU-capable registration without a residency declaration, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_ktc_residency(
        root,
        api_names=api_names,
        gpu_keyword=gpu_keyword,
        name_keyword=name_keyword,
        declaring_keywords=declaring_keywords,
        module_declarations=module_declarations,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "declare the data placements (`residencies=((in, out), ...)` or a module-level `_SWEEP_RESIDENCIES`) or mark the call `# residency-ok: <the single boundary and why>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_ktc_residency", refresh_command="PY_CI_SHARED_REFRESH=cuda_ktc_residency")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} GPU-capable registration(s) without a residency declaration; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
