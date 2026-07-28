"""Shared check: every environment variable production code reads via
``os.environ.get(...)``/``os.getenv(...)`` is documented in the project's
README.

Generalizes a 2026-07-21 audit finding (O-13): an env var this code
actually reads but never documents means an operator can't discover it
exists to set it -- and if the code fails closed when it's unset (an
auth check, a feature gate), the failure is silent. Two entry points:

- ``assert_readme_documents_every_env_var`` -- hard-fail on ANY
  undocumented var. Use once a repo is already at (or near) zero gap.
- ``assert_no_new_undocumented_env_vars`` -- baseline/grandfather style
  (same API shape as ``code_audit_meta``/``loc_budget``), for a repo
  adopting this check with existing undocumented-var debt: only a NEW
  gap (introduced after the baseline was captured) fails.

Deliberately dependency-light: ``pytest``/``orjson`` are imported lazily
inside the functions, matching this package's other modules.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

DEFAULT_HEADING = "## Environment variables"
REFRESH_FLAG = "--refresh-readme-env-var-baseline"


def _is_environ_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_environ_get = isinstance(func, ast.Attribute) and func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
    is_getenv = isinstance(func, ast.Attribute) and func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os"
    return is_environ_get or is_getenv


def _literal_str_elts(node: ast.expr) -> set[str] | None:
    """``{"A", "B"}`` if ``node`` is a Tuple/List/Set of string constants, else None."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out: set[str] = set()
    for elt in node.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        out.add(elt.value)
    return out


def _loop_target_name(target: ast.expr) -> str | None:
    return target.id if isinstance(target, ast.Name) else None


def _module_level_name_literals(tree: ast.AST) -> dict[str, set[str]]:
    """``{"KEY_NAMES": {"A", "B"}}`` for every ``NAME = (LITERAL, ...)``-shaped
    assignment anywhere in ``tree``."""
    name_literals: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            literals = _literal_str_elts(node.value)
            if literals is not None:
                name_literals[node.targets[0].id] = literals
    return name_literals


def _loop_var_literal_bindings(tree: ast.AST, name_literals: dict[str, set[str]]) -> dict[str, set[str]]:
    """``{"name": {"A", "B"}}`` for every ``for``/comprehension loop whose
    target is a bare name and whose iterable resolves (directly, or via
    ``name_literals``) to a literal string tuple/list/set."""
    loop_var_literals: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            pairs = [(node.target, node.iter)]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            pairs = [(gen.target, gen.iter) for gen in node.generators]
        else:
            continue
        for target, iter_expr in pairs:
            var_name = _loop_target_name(target)
            if var_name is None:
                continue
            literals = _literal_str_elts(iter_expr)
            if literals is None and isinstance(iter_expr, ast.Name):
                literals = name_literals.get(iter_expr.id)
            if literals:
                loop_var_literals.setdefault(var_name, set()).update(literals)
    return loop_var_literals


def _env_var_names_in_file(tree: ast.AST, loop_var_literals: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (_is_environ_call(node) and isinstance(node, ast.Call)):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            found.add(arg0.value)
        elif isinstance(arg0, ast.Name) and arg0.id in loop_var_literals:
            found.update(loop_var_literals[arg0.id])
    return found


def find_env_vars_read(files: Iterable[Path]) -> set[str]:
    """Every env-var name passed to ``os.environ.get(...)``/``os.getenv(...)``
    across ``files``, AST-based. Handles two shapes:

    1. A literal string arg: ``os.environ.get("NAME")``.
    2. A ``for name in (LITERAL, ...): ... os.environ.get(name)`` /
       ``[... for name in (LITERAL, ...) if os.environ.get(name)]`` shape
       (covers both a ``for`` statement and any comprehension form), where
       the iterable is either a literal tuple/list/set of strings, or a
       bare name previously assigned such a literal at module level (e.g.
       ``KEY_NAMES = ("A", "B"); [n for n in KEY_NAMES if os.environ.get(n)]``).

    Shape 2 is a best-effort heuristic, not full scope analysis: it maps a
    loop-target name to its resolved literal set WITHOUT verifying a given
    ``os.environ.get(name)`` call site sits lexically inside that specific
    loop (a same-named variable reused unrelated elsewhere in the same
    file could over-associate) -- acceptable for a documentation-
    completeness linter where the failure mode is "one extra var to
    document," never a false negative on the shape that matters.
    """
    found: set[str] = set()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        name_literals = _module_level_name_literals(tree)
        loop_var_literals = _loop_var_literal_bindings(tree, name_literals)
        found.update(_env_var_names_in_file(tree, loop_var_literals))
    return found


def find_readme_documented_vars(readme_path: Path, heading: str = DEFAULT_HEADING) -> set[str]:
    """Every ```VAR``` documented in ``readme_path``'s markdown table under
    ``heading`` -- a row's first cell may list more than one name joined by
    e.g. " / " (``\\`GIT_SHA\\` / \\`COMMIT_SHA\\```)."""
    lines = readme_path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading.strip()), None)
    if start is None:
        raise ValueError(f"{readme_path}'s {heading!r} section/table not found -- renamed, or heading doesn't match?")
    # Scanned line by line rather than matched as one regex: any number of explanatory lines may sit between the
    # heading and the table, and a `.*?` bridge silently matches only when the table starts on the second line,
    # which turns a real check into a no-op for every README that introduces its table with a sentence.
    table: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    if not table:
        raise ValueError(f"{readme_path}'s {heading!r} section/table not found -- renamed, or heading doesn't match?")
    names: set[str] = set()
    for line in table:
        cells = line.split("|")
        if len(cells) < 2:
            continue
        names.update(re.findall(r"`([A-Za-z][A-Za-z0-9_]*)`", cells[1]))
    return names


def assert_readme_documents_every_env_var(
    files: Iterable[Path],
    readme_path: Path,
    heading: str = DEFAULT_HEADING,
    third_party_vars: frozenset[str] = frozenset(),
) -> None:
    """Fail if any env var read by production code (``files``) isn't
    documented in ``readme_path``'s table. ``third_party_vars`` excludes
    vars consumed only by a third-party library the project depends on
    (never read by the project's own code, so this AST scan can't find
    them anyway, and they're expected to be documented by hand instead).
    """
    import pytest

    read_vars = find_env_vars_read(files) - third_party_vars
    documented = find_readme_documented_vars(readme_path, heading)
    undocumented = sorted(read_vars - documented)
    if undocumented:
        pytest.fail(f"Env var(s) read by production code but missing from {readme_path}'s {heading!r} table:\n  " + "\n  ".join(undocumented))


def assert_no_new_undocumented_env_vars(
    files: Iterable[Path],
    readme_path: Path,
    baseline_path: Path,
    heading: str = DEFAULT_HEADING,
    third_party_vars: frozenset[str] = frozenset(),
) -> None:
    """Baseline/grandfather variant of ``assert_readme_documents_every_env_var``,
    for a repo adopting this check with pre-existing undocumented-var debt:
    seeds/refreshes ``baseline_path`` (first run, or the
    ``--refresh-readme-env-var-baseline`` flag) with the CURRENT undocumented
    set and ``pytest.skip()``s that run; otherwise fails only on a var
    undocumented now that WASN'T in the baseline (a genuinely new gap),
    never on a pre-existing one. Call directly as a ``test_*`` body.

    Unlike ``assert_readme_documents_every_env_var``, a missing ``heading``
    section is NOT an error here -- a repo adopting this check may not have
    an env-var table at all yet, in which case every var it reads is
    grandfathered into the baseline on first run (documenting them is then a
    separate, deliberate improvement, not something this check demands
    up front).
    """
    import orjson
    import pytest
    import sys

    read_vars = find_env_vars_read(files) - third_party_vars
    try:
        documented = find_readme_documented_vars(readme_path, heading)
    except ValueError:
        documented = set()
    current_undocumented = sorted(read_vars - documented)

    if REFRESH_FLAG in sys.argv or not baseline_path.exists():
        baseline_path.write_text(
            orjson.dumps(current_undocumented, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode("utf-8"),
            encoding="utf-8",
        )
        pytest.skip(f"README env-var baseline refreshed at {baseline_path.name} ({len(current_undocumented)} grandfathered undocumented var(s))")

    baseline: list[str] = orjson.loads(baseline_path.read_bytes())
    new_undocumented = sorted(set(current_undocumented) - set(baseline))
    if new_undocumented:
        pytest.fail(
            f"{len(new_undocumented)} NEW env var(s) read by production code but not documented in "
            f"{readme_path}'s {heading!r} table (pre-existing undocumented vars are grandfathered in "
            f"the baseline -- this is a genuinely new one):\n  " + "\n  ".join(new_undocumented)
        )


def register_refresh_option(parser) -> None:
    """Register ``--refresh-readme-env-var-baseline`` as a no-op boolean
    flag. Call from a consuming repo's own ``pytest_addoption``."""
    try:
        parser.addoption(
            REFRESH_FLAG,
            action="store_true",
            default=False,
            help="rewrite the README env-var baseline JSON instead of comparing (intentional new-var doc backlog)",
        )
    except ValueError:
        pass  # already registered
