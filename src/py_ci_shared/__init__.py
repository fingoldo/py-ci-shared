"""Shared CI gates and tools for fingoldo Python projects.

What the package holds (the full list, one line per module, is :data:`py_ci_shared.registry.GATES` and the README
gate catalogue):

- **gates**: modules with ``assert_*`` entry functions a consumer calls from a meta-test, or enables in its
  ``[tool.py_ci_shared]`` table so the pytest plugin (``pytest11`` entry ``py_ci_shared``) and
  ``py-ci-shared run-all`` run them;
- **command-line tools** (``black_filtered_apply``, ``format_warn``, ``safe_precommit``, ``worktree_hygiene`` ...),
  run with ``py-ci-shared tool <name>`` or ``python -m py_ci_shared.<name>``;
- **libraries** of scanners a consumer wraps in its own test.

Public API: every module listed in the registry, and within it the names without a leading underscore.
``py_ci_shared._core`` and the other underscore modules are internal and may change in any release.
The shared ruff configs ship as package data (``py-ci-shared config-path ruff-base``).
"""

__version__ = "1.18.0"
