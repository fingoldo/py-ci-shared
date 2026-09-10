"""The linter versions every consuming repo runs, defined in one place.

The reusable workflows (``ruff-blocking.yml``, ``lint-advisory.yml``) and ``self-ci.yml`` read
``RUFF_VERSION`` from this file at run time, and each consumer's pre-commit hook runs
``python -m py_ci_shared.pinned_tool_versions``, which fails when that repo's own ``ruff==`` pin or the
ruff its interpreter runs differs from it.

Before this file the version was a literal repeated in four workflow steps and copied by hand into every
consumer's pin. They drifted in both directions: mlframe's pin moved to 0.16.1 while the shared workflows
it calls stayed on 0.15.22, with a comment saying CI ran 0.16.1; and a shared interpreter carrying 0.16.1
made the local hooks of repos pinned to 0.15.22 check a different rule set than their CI.

To bump: change the value here, release, then set each consumer's pin to match. A consumer's hook fails
until it does, which is the point.
"""

RUFF_VERSION = "0.16.1"
