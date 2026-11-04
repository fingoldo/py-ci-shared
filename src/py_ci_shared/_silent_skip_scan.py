"""Silent-skip detection of adoption_matrix: how a consumer test can pass when py_ci_shared is missing (try/ImportError handlers, importorskip, find_spec guards)."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Optional, Union

from ._core import Finding
from ._core.node_index import walk as _fast_walk

_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
_EXIT_CALLS = frozenset({"sys.exit", "exit", "quit", "os._exit", "SystemExit"})


def _imports_shared(stmts: Sequence[ast.stmt]) -> bool:
    for node in (n for s in stmts for n in _fast_walk(s)):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "py_ci_shared" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "py_ci_shared":
            return True
        if isinstance(node, ast.Call) and node.args and _str_arg_is_shared(node.args[0]):
            name = _call_name(node)
            if name.endswith(("import_module", "__import__")):
                return True
    return False


def _str_arg_is_shared(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.split(".")[0] == "py_ci_shared"


def _call_name(node: ast.Call) -> str:
    parts = []
    cur: ast.AST = node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")) in _IMPORT_ERRORS for t in types)


def _exit_code_zero(call: ast.Call) -> bool:
    if not call.args:
        return True
    arg = call.args[0]
    return isinstance(arg, ast.Constant) and arg.value in (0, None, False)


_LOUD = "<loud>"  # a node that makes the fallback fail loudly


def _raise_shape(node: ast.Raise) -> Optional[str]:
    call = node.exc
    if isinstance(call, ast.Call) and _call_name(call) == "SystemExit" and _exit_code_zero(call):
        return "exit-0"
    return _LOUD


def _call_shape(node: ast.Call) -> Optional[str]:
    name = _call_name(node)
    if name in _EXIT_CALLS or name.endswith(".exit"):
        return "exit-0" if _exit_code_zero(node) else _LOUD
    if name.endswith("fail") and name.split(".")[0] in ("pytest", "fail"):
        return _LOUD
    if name in ("pytest.skip", "skip", "pytest.xfail", "unittest.SkipTest", "SkipTest"):
        return "skip"
    return None


def _return_shape(node: ast.Return) -> Optional[str]:
    val = node.value
    if val is None:
        return None
    if isinstance(val, ast.Constant) and isinstance(val.value, int) and not isinstance(val.value, bool) and val.value != 0:
        return _LOUD
    if isinstance(val, ast.Constant) and val.value in (0, None):
        return "exit-0" if val.value == 0 else "swallowed"
    return None


def _assign_shape(node: Union[ast.Assign, ast.AnnAssign, ast.AugAssign]) -> Optional[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    if any(n.startswith("collect_ignore") for n in names):
        return "collect-ignore"
    if isinstance(node.value, ast.Constant) and node.value.value in (False, None) and names:
        return "availability-flag"
    return None


def _node_shape(node: ast.AST) -> Optional[str]:
    """The silent-skip shape one node contributes, ``_LOUD`` when it fails loudly, None when it says nothing."""
    if isinstance(node, ast.Raise):
        return _raise_shape(node)
    if isinstance(node, ast.Call):
        return _call_shape(node)
    if isinstance(node, ast.Return):
        return _return_shape(node)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return _assign_shape(node)
    return None


def _silent_shape(body: Sequence[ast.stmt]) -> Optional[str]:
    """The silent-skip rule a fallback body matches, or None when it fails loudly (raise, non-zero exit, pytest.fail)."""
    shapes: set[str] = set()
    for node in (n for s in body for n in _fast_walk(s)):
        shape = _node_shape(node)
        if shape == _LOUD:
            return None
        if shape is not None:
            shapes.add(shape)
    for rule in ("collect-ignore", "exit-0", "skip", "availability-flag", "swallowed"):
        if rule in shapes:
            return rule
    return "swallowed"


_SHAPE_TEXT = {
    "collect-ignore": "sets collect_ignore when py_ci_shared does not import: those tests vanish from the run",
    "exit-0": "exits 0 when py_ci_shared does not import: a missing install reads as a pass",
    "skip": "skips when py_ci_shared does not import",
    "availability-flag": "sets a flag to False when py_ci_shared does not import: the checks behind it stop running",
    "swallowed": "swallows the ImportError of py_ci_shared and carries on",
}


def _is_find_spec_guard(test: ast.expr) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n).endswith("find_spec") and n.args and _str_arg_is_shared(n.args[0]) for n in _fast_walk(test))


def _flag_names(body: Sequence[ast.stmt]) -> list[str]:
    out: list[str] = []
    for node in (n for s in body for n in _fast_walk(s)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value in (False, None):
            out.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _loudly_checked_flags(tree: ast.AST) -> set[str]:
    """Names tested as ``if not NAME:`` (or ``NAME is False``/``is None``) where that branch fails loudly."""
    out: set[str] = set()
    for node in _fast_walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = None
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, (ast.Name, ast.Attribute)):
            name = test.operand.id if isinstance(test.operand, ast.Name) else test.operand.attr
        elif isinstance(test, ast.Compare) and isinstance(test.left, (ast.Name, ast.Attribute)) and isinstance(test.ops[0], (ast.Is, ast.Eq)):
            comp = test.comparators[0]
            if isinstance(comp, ast.Constant) and comp.value in (False, None):
                name = test.left.id if isinstance(test.left, ast.Name) else test.left.attr
        if name and _silent_shape(node.body) is None:
            out.add(name)
    return out


def _try_findings(node: ast.Try, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for handler in node.handlers:
        if not _catches_import_error(handler):
            continue
        rule = _silent_shape(handler.body)
        if rule == "availability-flag":
            names = ", ".join(f"`{n}`" for n in _flag_names(handler.body))
            out.append(Finding(rel, handler.lineno, rule, f"{names}: {_SHAPE_TEXT[rule]}"))
        elif rule is not None:
            out.append(Finding(rel, handler.lineno, rule, _SHAPE_TEXT[rule]))
    return out


def _find_spec_findings(node: ast.If, rel: str) -> list[Finding]:
    # `if find_spec("py_ci_shared") is None: <fallback>`: the fallback is the branch that does not import it.
    out: list[Finding] = []
    for branch in (node.body, node.orelse):
        if branch and not _imports_shared(branch):
            rule = _silent_shape(branch)
            if rule is not None and rule != "swallowed":
                out.append(Finding(rel, branch[0].lineno, rule, _SHAPE_TEXT[rule]))
    return out


def _importorskip_finding(node: ast.Call, rel: str) -> Finding:
    mod = node.args[0].value if isinstance(node.args[0], ast.Constant) else ""
    return Finding(rel, node.lineno, "importorskip", f"importorskip({mod!r}): the gate turns SKIPPED when the install is missing or too old")


def _skips_in_tree(tree: ast.AST, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for node in _fast_walk(tree):
        if isinstance(node, ast.Call) and _call_name(node).split(".")[-1] == "importorskip" and node.args and _str_arg_is_shared(node.args[0]):
            out.append(_importorskip_finding(node, rel))
        elif isinstance(node, ast.Try) and _imports_shared(node.body):
            out.extend(_try_findings(node, rel))
        elif isinstance(node, ast.If) and _is_find_spec_guard(node.test):
            out.extend(_find_spec_findings(node, rel))
    return out
