"""Self meta-tests on this package's own source (audit 2026-11-04, 35-F6, 35-F7, 35-F8, 35-F9).

Each rule is a ratchet with a shrink-only baseline in ``tests/baselines/self_api_hygiene.json``: a NEW offender fails, and so does a baseline entry that no longer offends (fix it, then
delete the entry). ``python tests/test_self_api_hygiene.py --write`` rewrites the baseline from the tree, for the day a rule is deliberately widened; it should only ever lose lines.

* ``unannotated``  public module-level functions with a parameter or return left unannotated (mypy treats a call as ``Any``).
* ``no_docstring`` public module-level functions without a docstring.
* ``no_all``       public modules (no leading underscore) that define no ``__all__`` (a star-import leaks their helpers).
* ``library_print`` ``print()`` calls outside registry ``cli = true`` modules and outside ``main``/``_main``/``_cmd_*``, with no ``file=`` and no ``noqa: T201``: a library function that prints
  pollutes a consumer's pytest output and cannot be silenced.
* ``import_time_calls`` module-body call statements other than registration helpers (``append``, ``add``, ``update``, ``setdefault``, ``register``, ``extend``): code that runs in every
  consumer that imports a gate.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "py_ci_shared"
BASELINE = ROOT / "tests" / "baselines" / "self_api_hygiene.json"

_REGISTRATION_METHODS = frozenset({"append", "add", "update", "setdefault", "register", "extend"})
_ENTRYPOINT_NAMES = ("main", "_main")
_ENTRYPOINT_PREFIX = "_cmd_"


def _modules() -> "list[tuple[str, ast.Module]]":
    out = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((path.relative_to(SRC).as_posix(), ast.parse(path.read_bytes(), filename=str(path))))
    return out


def _is_public_module(rel: str) -> bool:
    return not any(part.startswith("_") for part in rel.split("/"))


def _fully_annotated(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    a = fn.args
    params = [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]
    params = [p for p in params if p.arg not in ("self", "cls")]
    return fn.returns is not None and all(p.annotation is not None for p in params)


def _cli_modules() -> "set[str]":
    from py_ci_shared import registry

    return {f"{g.name}.py" for g in registry.GATES if g.cli}


def _is_print(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "print" and not any(k.arg == "file" for k in call.keywords)


def _print_calls(tree: ast.Module, lines: "list[str]") -> "list[str]":
    """Enclosing-function names of each unreviewed print call, one entry per call."""
    found: "list[str]" = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name if scope == "<module>" else f"{scope}.{child.name}")
            elif isinstance(child, ast.ClassDef):
                visit(child, child.name if scope == "<module>" else f"{scope}.{child.name}")
            else:
                if isinstance(child, ast.Call) and _is_print(child) and "T201" not in lines[child.lineno - 1]:
                    top = scope.split(".")[0]
                    if top not in _ENTRYPOINT_NAMES and not top.startswith(_ENTRYPOINT_PREFIX):
                        found.append(scope)
                visit(child, scope)

    visit(tree, "<module>")
    return found


def _callee(call: ast.Call) -> str:
    f = call.func
    parts: "list[str]" = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts)) or "<dynamic>"


def current() -> "dict[str, dict[str, int]]":
    """The tree's offenders for every rule, as ``{rule: {key: count}}``."""
    cli = _cli_modules()
    out: "dict[str, dict[str, int]]" = {k: {} for k in ("unannotated", "no_docstring", "no_all", "library_print", "import_time_calls")}
    for rel, tree in _modules():
        public = _is_public_module(rel)
        if (
            public
            and not rel.endswith("__init__.py")
            and not any(isinstance(s, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in s.targets) for s in tree.body)
        ):
            if not any(isinstance(s, (ast.AnnAssign, ast.AugAssign)) and getattr(s.target, "id", "") == "__all__" for s in tree.body):
                out["no_all"][rel] = 1
        if public:
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and not stmt.name.startswith("_"):
                    if not _fully_annotated(stmt):
                        out["unannotated"][f"{rel}::{stmt.name}"] = 1
                    if ast.get_docstring(stmt) is None:
                        out["no_docstring"][f"{rel}::{stmt.name}"] = 1
        if rel.split("/")[-1] not in cli and rel not in cli and not rel.endswith("__main__.py"):
            lines = (SRC / rel).read_text(encoding="utf-8-sig").split("\n")
            for scope in _print_calls(tree, lines):
                key = f"{rel}::{scope}"
                out["library_print"][key] = out["library_print"].get(key, 0) + 1
        for stmt in tree.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                name = _callee(stmt.value)
                if name.split(".")[-1] in _REGISTRATION_METHODS:
                    continue
                key = f"{rel}::{name}"
                out["import_time_calls"][key] = out["import_time_calls"].get(key, 0) + 1
    return out


def _baseline() -> "dict[str, dict[str, int]]":
    return json.loads(BASELINE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _check(rule: str) -> None:
    now, base = current()[rule], _baseline()[rule]
    grew = {k: n for k, n in now.items() if n > base.get(k, 0)}
    assert (
        not grew
    ), f"{rule}: new offenders {grew}. Fix them (annotate, document, add __all__, use a logger or file=, move the call under a function); do not add them to the baseline."
    healed = sorted(k for k, n in base.items() if now.get(k, 0) < n)
    assert not healed, f"{rule}: baseline entries that no longer offend, delete them from tests/baselines/self_api_hygiene.json: {healed}"


def test_public_functions_are_fully_annotated() -> None:
    _check("unannotated")


def test_public_functions_have_a_docstring() -> None:
    _check("no_docstring")


def test_public_modules_define_all() -> None:
    _check("no_all")


def test_library_modules_do_not_print() -> None:
    _check("library_print")


def test_import_time_calls_are_only_registration() -> None:
    _check("import_time_calls")


def test_rule_detects_what_it_claims() -> None:
    """Teeth: each detector fires on a seeded offender and stays silent on its clean twin."""
    bad = ast.parse("def f(a):\n    print(a)\nregister_all()\n")
    good = ast.parse('def f(a: int) -> None:\n    """Doc."""\n    print(a, file=None)\nREG.append(1)\n')
    fn = bad.body[0]
    assert isinstance(fn, ast.FunctionDef) and not _fully_annotated(fn) and ast.get_docstring(fn) is None
    gfn = good.body[0]
    assert isinstance(gfn, ast.FunctionDef) and _fully_annotated(gfn) and ast.get_docstring(gfn) == "Doc."
    assert _print_calls(bad, ["def f(a):", "    print(a)", "register_all()"]) == ["f"]
    assert _print_calls(good, ["", "", "    print(a, file=None)", ""]) == []
    assert _print_calls(ast.parse("def main():\n    print(1)\n"), ["", "    print(1)"]) == []
    assert _print_calls(ast.parse("def f():\n    print(1)  # noqa: T201\n"), ["", "    print(1)  # noqa: T201"]) == []
    assert _callee(bad.body[1].value) == "register_all"  # type: ignore[attr-defined]


def test_baseline_file_is_sorted_and_has_every_rule() -> None:
    base = _baseline()
    assert set(base) == {"unannotated", "no_docstring", "no_all", "library_print", "import_time_calls"}
    for rule, entries in base.items():
        assert list(entries) == sorted(entries), f"{rule} keys must be sorted so a diff shows what shrank"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        data = {rule: dict(sorted(entries.items())) for rule, entries in current().items()}
        BASELINE.write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        sys.stdout.write(repr({rule: len(entries) for rule, entries in data.items()}) + "\n")
