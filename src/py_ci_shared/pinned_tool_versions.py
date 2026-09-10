"""Fail when a repo's ruff pin, or the ruff its interpreter runs, differs from the shared version.

A consumer's blocking ruff hook is ``language: system``: pre-commit builds no isolated environment, so
``ruff`` resolves to whatever the interpreter has, not to the pin in ``pyproject.toml``, and not to what
the shared ``ruff-blocking.yml`` runs on CI. Ruff adds rules to already-selected families between
releases, so a local ``All checks passed!`` on a different version is not evidence that CI will agree.

Three things must match: the version py-ci-shared's workflows run (:data:`py_ci_shared.tool_versions.RUFF_VERSION`),
the repo's exact ``ruff==`` pin, and ``python -m ruff --version``. Wire it ahead of the ruff hook::

    - id: pinned-tool-versions
      entry: python -m py_ci_shared.pinned_tool_versions
      language: system
      pass_filenames: false
      always_run: true

Exit codes: 0 when all three agree; 1 otherwise, one line per problem naming both versions and the fix.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404 - runs `python -m <tool> --version` with a fixed argv, no shell
import sys
from pathlib import Path
from typing import Callable, Optional

from py_ci_shared.tool_versions import RUFF_VERSION

#: tool -> (module run as ``python -m``, distribution name in the pin, version the shared workflows run)
TOOLS: dict[str, tuple[str, str, str]] = {"ruff": ("ruff", "ruff", RUFF_VERSION)}


def pinned_version(pyproject_text: str, dist: str) -> Optional[str]:
    """The exact version of a ``"<dist>==<version>"`` requirement in ``pyproject_text``, or None."""
    match = re.search(r'"' + re.escape(dist) + r'==([0-9][^";\s]*)', pyproject_text)
    return match.group(1) if match else None


def installed_version(module: str) -> Optional[str]:
    """The last token of ``python -m <module> --version``, or None when it cannot run."""
    try:
        out = subprocess.run(  # nosec B603 - fixed argv (sys.executable plus literal flags), shell=False
            [sys.executable, "-m", module, "--version"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    tokens = (out.stdout or out.stderr).split()
    return tokens[-1] if tokens else None


_RUFF_PRE_COMMIT_REV = re.compile(r"repo:\s*https://github\.com/astral-sh/ruff-pre-commit\s*\n\s*rev:\s*['\"]?v?([0-9][^\s'\"#]*)")


def precommit_ruff_revs(precommit_text: str) -> list[str]:
    """Every ``rev:`` of an ``astral-sh/ruff-pre-commit`` repo in a ``.pre-commit-config.yaml``.

    Hooks from that repo run the ruff their ``rev`` names, in pre-commit's own environment -- a third
    copy of the version, besides the pin and the interpreter, that can drift from CI.
    """
    return _RUFF_PRE_COMMIT_REV.findall(precommit_text)


def find_problems(
    pyproject_text: str,
    installed: Callable[[str], Optional[str]] = installed_version,
    precommit_text: str = "",
) -> list[str]:
    """One message per disagreement between the shared version, the repo's pin, the installed tool and
    any ``ruff-pre-commit`` rev."""
    problems: list[str] = [
        f"ruff: .pre-commit-config.yaml runs astral-sh/ruff-pre-commit at v{rev} while py-ci-shared's "
        f"workflows run {RUFF_VERSION}. Set its rev to v{RUFF_VERSION}"
        for rev in precommit_ruff_revs(precommit_text)
        if rev != RUFF_VERSION
    ]
    for name, (module, dist, shared) in TOOLS.items():
        pin = pinned_version(pyproject_text, dist)
        if pin is None:
            problems.append(f"{name}: pyproject.toml has no exact pin; add {dist}=={shared}, the version py-ci-shared's workflows run")
        elif pin != shared:
            problems.append(
                f"{name}: pyproject.toml pins {pin} while py-ci-shared's workflows run {shared}. Set the pin to "
                f"{dist}=={shared}, or change RUFF_VERSION in py-ci-shared's tool_versions.py first"
            )
        have = installed(module)
        if have is None:
            problems.append(f"{name}: not runnable as `python -m {module}` in this interpreter; pip install {dist}=={shared}")
        elif have != shared:
            problems.append(
                f"{name}: this interpreter has {have} while CI runs {shared}; the blocking hook is language: system, "
                f"so a local pass is measured on {have}. Fix: pip install {dist}=={shared}"
            )
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    """Check ``--pyproject`` (default ``./pyproject.toml``) and print each problem."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pyproject", default="pyproject.toml", help="the consuming repo's pyproject.toml")
    parser.add_argument("--precommit", default=".pre-commit-config.yaml", help="checked for ruff-pre-commit revs when it exists")
    args = parser.parse_args(argv)
    precommit = Path(args.precommit)
    problems = find_problems(
        Path(args.pyproject).read_text(encoding="utf-8"),
        precommit_text=precommit.read_text(encoding="utf-8") if precommit.is_file() else "",
    )
    for problem in problems:
        print("pinned-tool-version mismatch: " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
