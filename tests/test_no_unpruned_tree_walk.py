"""Meta-meta test: no module under ``src/py_ci_shared`` walks a whole tree without pruning ``.claude``.

Claude Code keeps its agent worktrees under ``<repo>/.claude/worktrees``, each a COMPLETE copy of the repository. A gate that
walks the root it was handed with ``root.rglob(...)``, ``root.glob("**/...")`` or ``os.walk(root)`` and does not prune ``.claude``
enters every copy: one consumer audit test took 19 to 30 minutes with about 20 of them. The pruned implementations are
``code_audit._base._iter_py_files``, ``_core.corpus.iter_files_pruned`` / ``iter_files`` and ``code_audit._scan_cache``.

A call is accepted when it is in one of those helper modules, or when a line of the call carries ``# walk-ok: <reason>`` (why the
root is always a narrow subtree the caller chose, or why entering excluded directories is the point). Calls inside docstrings
and comments are not calls, so the AST never sees them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "py_ci_shared"

# Modules that implement pruning themselves (each verified to drop _DEFAULT_EXCLUDE_DIRS / DEFAULT_EXCLUDE, which contain .claude).
HELPER_MODULES = frozenset({"code_audit/_base.py", "_core/corpus.py", "code_audit/_scan_cache.py"})

_MARKER = re.compile(r"#\s*walk-ok:\s*\S")


def _is_walk_call(call: ast.Call, bare_os_walk: bool) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return bare_os_walk and func.id == "walk"
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "rglob":
        return True
    if func.attr == "glob":
        first = call.args[0] if call.args else None
        return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("**")
    if func.attr == "walk":
        recv = func.value
        if isinstance(recv, ast.Call):
            recv = recv.func  # Path(root).walk()
        name = recv.id if isinstance(recv, ast.Name) else recv.attr if isinstance(recv, ast.Attribute) else ""
        return name in ("os", "Path")
    return False


def unpruned_walks(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` of every tree walk in *source* that has no ``# walk-ok: <reason>`` marker on any line of the call."""
    tree = ast.parse(source)
    lines = source.splitlines()
    bare_os_walk = any(isinstance(n, ast.ImportFrom) and n.module == "os" and any(a.name == "walk" for a in n.names) for n in ast.walk(tree))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_walk_call(node, bare_os_walk):
            span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
            if not any(_MARKER.search(line) for line in span):
                out.append((node.lineno, lines[node.lineno - 1].strip()))
    return sorted(out)


def test_no_module_walks_a_tree_without_pruning():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in HELPER_MODULES:
            continue
        offenders.extend(f"{rel}:{line}: {text}" for line, text in unpruned_walks(path.read_text(encoding="utf-8")))
    assert not offenders, (
        "unpruned tree walk (enters .claude/worktrees, each a full repo copy). Use _core.corpus.iter_files_pruned or "
        "code_audit._base._iter_py_files, or mark the line `# walk-ok: <reason>`:\n" + "\n".join(offenders)
    )


def test_helper_modules_exist():
    assert all((SRC / rel).is_file() for rel in HELPER_MODULES)


@pytest.mark.parametrize(
    "snippet",
    [
        "for p in root.rglob('*.py'): pass",
        "for p in root.glob('**/*.py'): pass",
        "import os\nfor d, n, f in os.walk(root): pass",
        "from os import walk\nfor d, n, f in walk(root): pass",
        "from pathlib import Path\nfor d, n, f in Path(root).walk(): pass",
        "x = sorted(\n    root.rglob('*.py')\n)  # walk-ok:\n",
    ],
)
def test_scanner_flags_a_synthetic_offender(snippet):
    assert len(unpruned_walks(snippet)) == 1


@pytest.mark.parametrize(
    "snippet",
    [
        "for p in root.rglob('*.py'): pass  # walk-ok: the caller passes one package directory",
        "x = sorted(\n    root.rglob('*.py')  # walk-ok: narrow subtree\n)",
        "import ast\nfor n in ast.walk(tree): pass",
        "for p in root.glob('*.py'): pass",
        '"""root.rglob(\'*.py\') in a docstring"""\n# os.walk(root) in a comment',
    ],
)
def test_scanner_passes_marked_and_unrelated_calls(snippet):
    assert unpruned_walks(snippet) == []
