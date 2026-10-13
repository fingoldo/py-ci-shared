"""A test that runs ``git`` in a child process must not inherit the caller's ``GIT_*`` variables. Opt-in ratchet over test files.

Under a git hook, ``GIT_DIR``, ``GIT_INDEX_FILE`` and ``GIT_WORK_TREE`` point at the repository that is committing.
A test that spawns ``git`` and inherits them runs its git commands against that repository, not its temporary one,
and passes or fails depending on whether it was started from a hook. The fix is an explicit environment with the
``GIT_*`` variables removed (``py_ci_shared._core.git.git_env()`` builds one).

Flagged, one finding per call:

* Python: ``subprocess.run``/``Popen``/``check_output``/``check_call``/``call`` whose first argument is a list starting
  with ``"git"`` (or a string starting with ``git ``) and that either passes no ``env=`` or passes one that does not
  mention ``GIT_`` nor a helper named like ``git_env``, ``clean_env``, ``scrub`` or ``strip``. A bare name passed as ``env=``
  is judged by the values assigned to that name in the module.
* Dart: ``Process.run``/``runSync``/``start`` of ``'git'`` with no ``environment:`` argument, one that does not
  mention ``GIT_`` nor such a helper, or a scrubbed one without ``includeParentEnvironment: false`` (Dart merges
  ``environment:`` into the parent's environment otherwise, so the hook's ``GIT_*`` reach the child anyway).

The environment check is by name, not by evaluating the expression: an ``env=`` that names a helper with a
misleading name passes. Python test files are parsed with ``_core.source.parse_source``, so a file that does not
parse fails the gate rather than being skipped. Keys are ``<repo-relative path>: <normalized line of the call>``.

Enable it explicitly (NOT in the default set)::

    [tool.py_ci_shared]
    enable = ["git_child_env"]

Record the existing calls in ``tool/meta/baselines/git_child_env.json`` as ``{"accepted": {key: note}}``, each note saying why
the call is safe without a scrubbed environment. Only a NEW unscrubbed git spawn fails. A ``*_dir`` keyword is resolved
against the repo root.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core.source import parse_source
from ._text_ratchet import DEFAULT_BASELINE_DIR, enforce_findings, is_test_path, normalize_line, text_files

__all__ = ["find_unscrubbed_git_spawns", "assert_no_new_unscrubbed_git_spawns"]

_SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
_DART_SPAWN = re.compile(r"Process\.(?:run|runSync|start)\(\s*['\"]git['\"]")
# GIT_ is case-sensitive (an environment variable name); the helper names are not, so _cleanEnv and clean_env both count.
_NO_PARENT_ENV = re.compile(r"\bincludeParentEnvironment\s*:\s*false\b")
_SCRUBBER = re.compile(r"GIT_|(?i:git_?env|clean_?env|scrub|strip)")


def _is_git_argv(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        first = node.elts[0]
        return isinstance(first, ast.Constant) and first.value == "git"
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("git ")


def _assigned_sources(source: str, tree: ast.Module) -> dict[str, list[str]]:
    """``{name: [source of each value assigned to it]}``, so ``env=env`` is judged by what ``env`` was built from."""
    assigned: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            targets = [node.target.id]
        else:
            continue
        value = node.value
        if value is None:
            continue
        for name in targets:
            assigned.setdefault(name, []).append(ast.get_source_segment(source, value) or "")
    return assigned


def _env_scrubs(env: ast.expr, source: str, assigned: dict[str, list[str]]) -> bool:
    if isinstance(env, ast.Name):
        return any(_SCRUBBER.search(text) for text in assigned.get(env.id, []))
    return bool(_SCRUBBER.search(ast.get_source_segment(source, env) or ""))


def _python_spawns(rel: str, path: Path) -> dict[str, str]:
    source, tree = parse_source(path)
    lines = source.splitlines()
    assigned = _assigned_sources(source, tree)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_CALLS and isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        if not node.args or not _is_git_argv(node.args[0]):
            continue
        env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
        if env is not None and _env_scrubs(env, source, assigned):
            continue
        line = lines[node.lineno - 1]
        why = "passes no env=" if env is None else "passes an env that neither drops GIT_* nor calls a scrubbing helper"
        found[f"{rel}: {normalize_line(line)}"] = f"line {node.lineno} spawns git and {why}"
    return found


def _dart_spawns(rel: str, text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _DART_SPAWN.finditer(text):
        depth = 0
        # The '(' that opens the call. match.end() - 1 is the closing quote of 'git', and walking from there ended the
        # body at the first nested call's ')' or, with none, kept only `Process.run('git'`: an `environment:` written as a
        # map literal was then reported as missing.
        start = text.index("(", match.start())
        end = start
        for index in range(start, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = text[match.start() : end + 1]
        environment = re.search(r"\benvironment\s*:", body)
        scrubbed = environment is not None and _SCRUBBER.search(body[environment.end() :])
        # Dart MERGES `environment:` into the parent's unless includeParentEnvironment is false, so a scrubbed map alone
        # still hands the child the hook's GIT_DIR.
        if scrubbed and _NO_PARENT_ENV.search(body):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.start())
        line = text[line_start : len(text) if line_end < 0 else line_end]
        if environment is None:
            why = "passes no environment:"
        elif scrubbed:
            why = "scrubs environment: without includeParentEnvironment: false, so Dart merges the parent's GIT_* back in"
        else:
            why = "passes an environment that neither drops GIT_* nor calls a scrubbing helper"
        number = text.count("\n", 0, match.start()) + 1
        found[f"{rel}: {normalize_line(line)}"] = f"line {number} spawns git and {why}"
    return found


def find_unscrubbed_git_spawns(root: Union[str, Path]) -> dict[str, str]:
    """``{key: description}`` for each git spawn in a test file under *root* that does not scrub ``GIT_*`` from its environment."""
    found: dict[str, str] = {}
    for file in text_files(root, ("*.py", "*.dart")):
        if not is_test_path(file.rel):
            continue
        if file.rel.endswith(".py"):
            found.update(_python_spawns(file.rel, file.path))
        else:
            found.update(_dart_spawns(file.rel, file.text))
    return found


def assert_no_new_unscrubbed_git_spawns(repo_root: Union[str, Path], *, baseline_dir: Optional[str] = None) -> None:
    """Fail on a git spawn in a test that does not scrub ``GIT_*`` and the baseline does not accept (``tool/meta/baselines/git_child_env.json``)."""
    enforce_findings(
        "git_child_env",
        find_unscrubbed_git_spawns(repo_root),
        repo_root=repo_root,
        baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
        label="git_child_env",
        guidance="A test's git child inherits GIT_DIR and friends from a hook and runs against the wrong repository. Pass an env with GIT_* removed.",
    )
