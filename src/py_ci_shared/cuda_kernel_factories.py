"""A kernel factory ``kernel(cp, real)`` that the register-pressure registry does not list is a kernel whose spills nobody checks on a device.

A spill (``local_size_bytes > 0``) costs 30 to 800 cycles on every access to the spilled value, and a compiled cupy kernel reports it directly, so the cheapest check is a test that builds every kernel of the package and
asserts no spill. That test only sees the kernels in its registry, and a registry is a list somebody has to remember to extend: a new kernel is silently outside it.

* ``cuda-kernel-factory-unregistered``: a module-level function named ``kernel`` or ``kernel_*`` whose first two parameters are ``cp`` and ``real`` (the shape ``kernel(cp, real)`` the registry compiles for each
  float type), or a module-level assignment of a call to such a name (``kernel = code_width_kernel(_kernel)``: the factory built by a shared helper instead of a wrapper function repeated in every module), that no
  entry of the consumer's registry names. The consumer passes its registry as ``registered``, an iterable of ``(dotted module, function name)``; a registered module matches a file when the
  file's dotted path (``pkg.sub.mod`` from the path below *root*) is the whole registered module or its tail (``registered`` may carry the package prefix *root* lacks).

Not reported: a function with another name or other first parameters (``kernel(x, y)``, ``kernel_size(cp)``), a method, a nested function, an assignment of a name other than ``kernel`` / ``kernel_*`` or of a non-call value (``kernel = None``), and a factory the registry lists.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_factories import assert_cuda_kernel_factories

    def test_every_kernel_factory_is_registered():
        registered = {(module, factory) for module, factory, _dtypes in _REGISTRY.values()}
        assert_cuda_kernel_factories("src/mlframe", registered=registered, min_files=100)
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["RULE", "assert_cuda_kernel_factories", "find_cuda_kernel_factories"]

RULE = "cuda-kernel-factory-unregistered"


def _dotted(rel: str) -> str:
    """``pkg/sub/mod.py`` -> ``pkg.sub.mod`` (``pkg/sub/__init__.py`` -> ``pkg.sub``)."""
    parts = rel[:-3].split("/") if rel.endswith(".py") else rel.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_factory(node: ast.FunctionDef) -> bool:
    if not (node.name == "kernel" or node.name.startswith("kernel_")):
        return False
    params = [a.arg for a in node.args.args]
    return params[:2] == ["cp", "real"]


def _factory_name(node: ast.stmt) -> Optional[str]:
    """The name a module-level statement defines as a ``kernel(cp, real)`` factory: a ``def`` of that shape, or ``kernel = <call>`` (a name ``kernel`` / ``kernel_*`` bound to the result of a call: the factory a helper built)."""
    if isinstance(node, ast.FunctionDef):
        return node.name if _is_factory(node) else None
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
        return name if name == "kernel" or name.startswith("kernel_") else None
    return None


def _is_registered(dotted: str, name: str, registered: "set[tuple[str, str]]") -> bool:
    for module, factory in registered:
        if factory == name and (module == dotted or module.endswith("." + dotted) or dotted.endswith("." + module)):
            return True
    return False


def _findings_in(tree: ast.Module, rel: str, registered: "set[tuple[str, str]]", aliases: ImportAliases) -> "list[Finding]":  # noqa: ARG001 - matcher table: every matcher takes (node, aliases)
    """The module-level factories of one parsed file that the registry does not list."""
    dotted = _dotted(rel)
    return [
        Finding(
            rel,
            node.lineno,
            RULE,
            f"the kernel factory {name}(cp, real, ...) is not in the register-pressure registry: its spills are never checked on a device; register it or rename it if it is not a kernel factory",
        )
        for node in tree.body
        for name in [_factory_name(node)]
        if name is not None and not _is_registered(dotted, name, registered)
    ]


def find_cuda_kernel_factories(
    root: Union[str, Path],
    *,
    registered: Iterable["tuple[str, str]"] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every module-level ``kernel`` / ``kernel_*(cp, real, ...)`` factory under *root* that *registered* (``(dotted module, name)`` pairs) does not list, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read
    the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    known = {(str(m), str(n)) for m, n in registered}
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, known, ImportAliases.from_tree(parsed.tree)))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_kernel_factories(
    root: Union[str, Path],
    *,
    registered: Iterable["tuple[str, str]"] = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any kernel factory the registry does not list, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_kernel_factories(root, registered=registered, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "add the factory to the register-pressure registry (module, function, dtypes), or rename it if it is not a kernel factory"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_kernel_factories", refresh_command="PY_CI_SHARED_REFRESH=cuda_kernel_factories")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(
            f"{len(found)} unregistered kernel factor{'y' if len(found) == 1 else 'ies'}; {guidance}:\n  " + "\n  ".join(f.render() for f in found)
        )
