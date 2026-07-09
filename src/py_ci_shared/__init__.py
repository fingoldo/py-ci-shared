"""Shared CI/lint tooling for fingoldo Python projects.

This package ships thin orchestration scripts (``black_filtered_apply``, ``format_warn``,
``bandit_warn``, ``vulture_warn``) invoked via ``python -m py_ci_shared.<script>`` from a
consuming repo's CI workflows and pre-commit hooks. The shared ruff base config
(``configs/ruff-base.toml``) is NOT part of this installed package -- see the repo README for
how consuming repos pull it in via ruff's ``extend``.
"""

__version__ = "0.1.0"
