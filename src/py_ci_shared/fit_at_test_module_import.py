"""A test module must not train a model while it is imported (audit 50-T5).

Every CI shard imports every test module to collect it. One full ``.fit(`` at module level, or in a class body, runs
on every shard that collects the file, so a slow fit keeps the macOS shards in collection until the job cap cancels
them (mlframe: three hours with nothing run). Calls inside a ``def`` body or a ``lambda`` run only when the test runs
and are not reported. Decorators and default argument values are evaluated at import and are reported.

Scope: ``test_*.py``, ``*_test.py`` and ``conftest.py`` under the tests root. A call is waived by a comment
``# import-fit-ok: <reason>`` on any line the call spans.
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Union

from ._core import Finding, child_nodes
from ._core.corpus import iter_files_pruned
from ._core.scan import scan_python
from ._core.source import comment_tokens
from ._core.unparse import unparse_stable

__all__ = ["DEFAULT_HEAVY_CALLS", "RULE", "WAIVER", "assert_no_fit_at_test_module_import", "find_fit_at_test_module_import"]

RULE = "fit-at-test-module-import"
WAIVER = "import-fit-ok:"
DEFAULT_HEAVY_CALLS: tuple[str, ...] = ("fit", "fit_transform", "fit_predict")
_TEST_PATTERNS = ("test_*.py", "*_test.py", "conftest.py")


def _test_modules(root: Path) -> list[Path]:
    """The test modules under *root*, never entering ``.claude`` worktrees or other pruned trees (a full repo copy each)."""
    return [p for p in iter_files_pruned(root, (".py",)) if any(fnmatch.fnmatch(p.name, pat) for pat in _TEST_PATTERNS)]

PathLike = Union[str, Path]
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _import_time_nodes(tree: ast.Module) -> Iterator[ast.AST]:
    """Every node evaluated when the module is imported: module and class bodies, decorators and defaults, not bodies."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _FUNCS):
            if not isinstance(node, ast.Lambda):
                stack.extend(node.decorator_list)
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        yield node
        stack.extend(child_nodes(node))


def _waived_lines(source: str) -> set[int]:
    return {line for line, text in comment_tokens(source) if WAIVER in text}


def _heavy_import_time_calls(tree: ast.Module, heavy: frozenset[str]) -> Iterator[ast.Call]:
    for node in _import_time_nodes(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in heavy:
            yield node


def find_fit_at_test_module_import(tests_root: PathLike, *, heavy_calls: Sequence[str] = DEFAULT_HEAVY_CALLS) -> list[Finding]:
    """Every import-time call to a name in *heavy_calls* in a test module under *tests_root* that is not waived.

    An unparsable test module is not reported here; :func:`assert_no_fit_at_test_module_import` fails on it."""
    root = Path(tests_root)
    files = _test_modules(root)
    scan = scan_python(files, root=root, min_files=0)
    heavy = frozenset(heavy_calls)
    out: list[Finding] = []
    for parsed in scan:
        waived = _waived_lines(parsed.source)
        for call in _heavy_import_time_calls(parsed.tree, heavy):
            span = set(range(call.lineno, (call.end_lineno or call.lineno) + 1))
            if span & waived:
                continue
            text = unparse_stable(call)[:120]
            out.append(
                Finding(
                    parsed.rel,
                    call.lineno,
                    RULE,
                    f"`{text}` runs when this test module is imported, so every shard that collects it pays for it; move it into a "
                    f"fixture or the test body, or waive it with `# {WAIVER} <reason>`",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_fit_at_test_module_import(
    tests_root: PathLike, *, heavy_calls: Sequence[str] = DEFAULT_HEAVY_CALLS, min_files: int = 1, allow_unparsed: bool = False
) -> None:
    """Fail on any import-time heavy call, on an unparsable test module, and on a tree with fewer than *min_files* test modules."""
    root = Path(tests_root)
    files = _test_modules(root)
    scan_python(files, root=root, min_files=min_files).assert_ok(allow_unparsed=allow_unparsed)
    found = find_fit_at_test_module_import(root, heavy_calls=heavy_calls)
    if found:
        raise AssertionError(f"{len(found)} import-time fit call(s) in test modules:\n  " + "\n  ".join(f.render() for f in found))
