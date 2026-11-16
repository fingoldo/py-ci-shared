"""The Python versions pyproject advertises are the ones self-ci runs blocking (audit 45-T4).

A consumer (and PyPI, and pip's resolver) can read ``Programming Language :: Python :: 3.X`` classifiers, but nothing said which
interpreters this package is tested on. The classifiers must equal the blocking legs of the self-ci Python matrix: a leg that
is ``preview: true`` (continue-on-error) is not advertised until it is promoted.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

try:
    import tomllib
except ImportError:  # Python 3.9 and 3.10
    import tomli as tomllib  # type: ignore[no-redef]

_ROOT = Path(__file__).resolve().parent.parent
_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")


def _advertised_versions() -> "set[str]":
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found = set()
    for classifier in data["project"]["classifiers"]:
        match = _CLASSIFIER.match(classifier)
        if match:
            found.add(match.group(1))
    return found


def _blocking_versions() -> "set[str]":
    workflow = yaml.safe_load((_ROOT / ".github" / "workflows" / "self-ci.yml").read_text(encoding="utf-8"))
    blocking: "set[str]" = set()
    for job in workflow["jobs"].values():
        matrix = (job.get("strategy") or {}).get("matrix")
        if not isinstance(matrix, dict) or "python" not in matrix:
            continue
        blocking.update(str(version) for version in matrix["python"])
        for entry in matrix.get("include") or []:
            if entry.get("python") and entry.get("preview") is not True:
                blocking.add(str(entry["python"]))
    return blocking


def test_advertised_python_versions_are_the_blocking_ci_legs() -> None:
    blocking = _blocking_versions()
    assert blocking, "the self-ci matrix must name at least one blocking Python"
    assert _advertised_versions() == blocking, f"pyproject advertises {sorted(_advertised_versions())} but self-ci blocks on {sorted(blocking)}"
