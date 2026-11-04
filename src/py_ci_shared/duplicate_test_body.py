"""Two test functions in one file with identical bodies, decorators and signature: a copy-pasted twin that asserts nothing new.

A test is copied under a new name and the copy is never edited to exercise the case its name promises (``test_zero_to_nonzero_is_changed``
repeating the body of ``test_prev_zero_returns_true``). The suite counts it as extra coverage; a mutant on the branch the second name
promised survives. ``duplicate_function_body`` reports such twins too, but is too noisy to run on a test tree (fixtures, AST helpers and
one-line bodies are identical by design). This gate narrows to what is never intentional.

Reported, per file and per enclosing class: two functions named ``test*`` whose normalised body (the docstring dropped) is identical,
whose decorator lists are identical and whose signatures are identical, and whose body dumps to at least *min_dump_chars* characters
(shorter bodies are one-liners that coincide legitimately). The second and later twin carries the finding.

Not reported: a twin that differs in one literal, a decorator (``@pytest.mark.parametrize`` values included) or an argument; non-test
helpers and fixtures; twins in different files or different classes.

Usage::

    from py_ci_shared.duplicate_test_body import assert_no_duplicate_test_bodies

    def test_no_duplicate_test_bodies():
        assert_no_duplicate_test_bodies(REPO / "tests", baseline_path=REPO / "tests" / "duplicate_test_body_baseline.json")
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = ["RULE", "assert_no_duplicate_test_bodies", "find_duplicate_test_bodies"]

RULE = "duplicate-test-body"
DEFAULT_MIN_DUMP_CHARS = 120
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _body_dump(fn: _FuncDef) -> str:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return "|".join(ast.dump(stmt) for stmt in body)


def _signature(fn: _FuncDef) -> tuple:
    return (type(fn).__name__, ast.dump(fn.args), tuple(ast.dump(d) for d in fn.decorator_list))


def _scopes(tree: ast.Module) -> list:
    scopes: list = [tree.body]
    scopes.extend(node.body for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return scopes


def _findings_in(tree: ast.Module, rel: str, min_dump_chars: int) -> list:
    out: list = []
    for body in _scopes(tree):
        seen: dict = {}
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
                continue
            dump = _body_dump(node)
            if len(dump) < min_dump_chars:
                continue
            key = (_signature(node), dump)
            first = seen.get(key)
            if first is None:
                seen[key] = node
                continue
            out.append(
                Finding(
                    rel,
                    node.lineno,
                    RULE,
                    f"{node.name} repeats the body, decorators and signature of {first.name} (line {first.lineno}); "
                    "edit it to test the case its name promises, or delete it",
                )
            )
    return out


def find_duplicate_test_bodies(
    root: Union[str, Path],
    *,
    min_dump_chars: int = DEFAULT_MIN_DUMP_CHARS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every copied test twin under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, min_dump_chars))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_duplicate_test_bodies(
    root: Union[str, Path],
    *,
    min_dump_chars: int = DEFAULT_MIN_DUMP_CHARS,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any copied test twin, or with *baseline_path* on any the baseline does not accept."""
    found = find_duplicate_test_bodies(root, min_dump_chars=min_dump_chars, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "edit the copy to test the case its name promises, or delete it"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="duplicate_test_body", refresh_command="PY_CI_SHARED_REFRESH=duplicate_test_body")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} duplicate-test-body finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
