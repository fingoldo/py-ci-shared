"""A function that is handed group ids but builds a row-level splitter without them.

``def f(X, y, groups): ... KFold(5).split(X)`` (or ``train_test_split(X, y)``, ``ShuffleSplit``, ...) puts rows of
one group (patient, user, wellbore) on both sides of the split: the validation score is inflated by memorisation
and early stopping or a threshold is chosen on leaked rows. mlframe hand-fixed it in aff82273a, 4cccaff05,
afe080b6f, 1615e143e, a32aba204 and c4ee79f57; the real survivor was a ``train_test_split`` in the float
``val_size`` branch of an evaluation helper while the other branch honoured ``groups``.

What is reported: inside a function with a parameter named like ``groups``/``group``/``group_ids``/
``group_labels``/``group_labels``, a call of ``KFold``, ``StratifiedKFold``, ``RepeatedKFold``, ``ShuffleSplit``,
``StratifiedShuffleSplit`` or ``train_test_split`` that neither passes ``groups=`` nor receives the groups
parameter as any argument. A call lexically inside an ``if`` (or its ``else``) whose test mentions the groups
parameter is the documented fallback shape (``if groups is None: KFold ... else: GroupKFold``) and is not reported.
``Group*`` splitters are never reported. A reviewed exception is ``# group-blind-ok: <reason>`` on the call line,
or an *allowed* entry ``{"path::function": reason}``.

Usage in a consumer's meta test::

    from py_ci_shared.group_blind_split import assert_group_blind_split

    def test_group_blind_split():
        assert_group_blind_split("src", baseline_path=BASELINE_PATH)

``BASELINE_PATH`` is the consumer's own baseline file, kept in the consumer's repository.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, child_nodes, scan_python
from ._core import walk as shared_walk
from ._core.call_names import call_name_or_none as _call_name

__all__ = ["DEFAULT_GROUP_PARAMS", "DEFAULT_SPLITTERS", "RULE", "SUPPRESSION", "assert_group_blind_split", "find_group_blind_split"]

RULE = "group-blind-split"
SUPPRESSION = "# group-blind-ok:"

DEFAULT_GROUP_PARAMS = frozenset({"groups", "group", "group_ids", "group_labels", "group_id", "group_col"})
DEFAULT_SPLITTERS = frozenset(
    {"KFold", "StratifiedKFold", "RepeatedKFold", "RepeatedStratifiedKFold", "ShuffleSplit", "StratifiedShuffleSplit", "train_test_split"}
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _params(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> list[str]:
    a = func.args
    names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return names


def _mentions(node: ast.AST, names: Collection[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in shared_walk(node))


def _guarded_by_groups_test(call: ast.Call, parents: dict[int, ast.AST], func: ast.AST, group_names: Collection[str]) -> bool:
    cur = parents.get(id(call))
    while cur is not None and cur is not func:
        if isinstance(cur, ast.If) and _mentions(cur.test, group_names):
            return True
        if isinstance(cur, ast.IfExp) and _mentions(cur.test, group_names):
            return True
        cur = parents.get(id(cur))
    return False


def _findings_in(
    tree: ast.Module,
    rel: str,
    lines: list[str],
    group_params: Collection[str],
    splitters: Collection[str],
    allowed: Mapping[str, str],
) -> list[Finding]:
    out: list[Finding] = []
    seen: set[int] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        mine = [p for p in _params(func) if p in group_params]
        if not mine or f"{rel}::{func.name}" in allowed:
            continue
        parents = {id(c): p for p in shared_walk(func) for c in child_nodes(p)}
        for call in (n for n in shared_walk(func) if isinstance(n, ast.Call)):
            name = _call_name(call)
            if name not in splitters or call.lineno in seen:
                continue
            if any(kw.arg == "groups" for kw in call.keywords) or _mentions(call, mine):
                continue
            if _guarded_by_groups_test(call, parents, func, mine):
                continue
            seen.add(call.lineno)
            if 1 <= call.lineno <= len(lines) and SUPPRESSION in lines[call.lineno - 1]:
                continue
            out.append(
                Finding(
                    rel,
                    call.lineno,
                    RULE,
                    f"'{func.name}' receives '{mine[0]}' but builds {name}(...) without it: rows of one group can land on both sides; "
                    "use a Group* splitter or pass groups",
                )
            )
    return out


def find_group_blind_split(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    group_params: Collection[str] = DEFAULT_GROUP_PARAMS,
    splitters: Collection[str] = DEFAULT_SPLITTERS,
    allowed: Optional[Mapping[str, str]] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *allowed* maps ``"relative/path.py::function"`` to the reason its row-level split is right. Raises
    ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot
    be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines(), group_params, splitters, allowed or {}))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_group_blind_split(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    group_params: Collection[str] = DEFAULT_GROUP_PARAMS,
    splitters: Collection[str] = DEFAULT_SPLITTERS,
    allowed: Optional[Mapping[str, str]] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_group_blind_split(
        root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, group_params=group_params, splitters=splitters, allowed=allowed
    )
    guidance = "split with GroupKFold/GroupShuffleSplit (or pass groups=), keep the KFold only in the `groups is None` branch, or `# group-blind-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="group_blind_split", refresh_command="PY_CI_SHARED_REFRESH=group_blind_split")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} group-blind-split finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
