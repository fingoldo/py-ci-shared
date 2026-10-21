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

RUFF_VERSION = "0.16.10"

#: The black the shared ``black-filtered.yml`` workflow runs (its ``BLACK_CMD``); a consumer pins its own ``black==`` to
#: this so its local hook formats exactly as CI checks. A test holds the workflow to this value.
BLACK_VERSION = "26.5.1"

#: The lint tools ``lint-blocking.yml`` runs when a consumer leaves its ``*-version`` input empty. Each was the newest release at least
#: two weeks old when it was chosen; an empty input used to mean "whatever PyPI serves today", so a compromised or broken release ran
#: in every consumer's CI at once. Bumping one is a deliberate change here, with a CHANGELOG entry.
ACTIONLINT_PY_VERSION = "1.7.12.25"
ZIZMOR_VERSION = "1.30.1"
CODESPELL_VERSION = "2.4.3"
YAMLLINT_VERSION = "1.38.0"
BANDIT_VERSION = "1.9.4"
VULTURE_VERSION = "2.16"
INTERROGATE_VERSION = "1.7.0"
DEPTRY_VERSION = "0.25.1"
