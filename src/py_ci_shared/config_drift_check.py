"""Reports [tool.ruff]/[tool.mypy] config divergence across consumer repos.

Fetches pyproject.toml from each consumer repo's default branch and diffs the fields that are
meant to stay in sync (line-length, target-version, the shared `extend` path, mypy strictness
flags) against each other. This is informational, not a pass/fail gate: legitimate per-repo
divergence exists (e.g. a repo mid-migration to a new rule), so the scheduled workflow that runs
this posts the report as a step summary rather than failing the run -- see README.md's "Keeping
this repo in sync with consumers" section for why no automated gate existed before this script.
"""

from __future__ import annotations

import sys
import tomllib
import urllib.request

CONSUMERS = {
    "mlframe": "https://raw.githubusercontent.com/fingoldo/mlframe/master/pyproject.toml",
    "pyutilz": "https://raw.githubusercontent.com/fingoldo/pyutilz/master/pyproject.toml",
}

RUFF_FIELDS_TO_COMPARE = ("extend", "target-version", "line-length")
MYPY_FIELDS_TO_COMPARE = ("python_version", "disallow_untyped_defs", "warn_return_any")


def _fetch_pyproject(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310 -- fixed, first-party GitHub raw-content URLs, not user input
        doc: dict = tomllib.loads(resp.read().decode("utf-8"))
        return doc


def _diff_section(section_name: str, fields: tuple, parsed: dict) -> list[str]:
    lines = []
    values = {}
    for repo, doc in parsed.items():
        section = doc.get("tool", {}).get(section_name, {})
        values[repo] = {f: section.get(f) for f in fields}
    for field in fields:
        seen = {repo: v[field] for repo, v in values.items()}
        distinct = {v for v in seen.values() if v is not None}
        if len(distinct) > 1:
            lines.append(f"  [tool.{section_name}].{field} diverges: {seen}")
    return lines


def main() -> int:
    parsed = {}
    for repo, url in CONSUMERS.items():
        try:
            parsed[repo] = _fetch_pyproject(url)
        except Exception as e:  # noqa: PERF203 -- 2 network calls total, not a hot loop; broad catch so one consumer's fetch failure doesn't abort the report for the others
            print(f"WARNING: could not fetch {repo}'s pyproject.toml ({type(e).__name__}: {e}), skipping it", file=sys.stderr)
    if len(parsed) < 2:
        print("Fewer than 2 consumer pyproject.toml files fetched successfully -- nothing to diff.")
        return 0

    report = []
    ruff_diff = _diff_section("ruff", RUFF_FIELDS_TO_COMPARE, parsed)
    if ruff_diff:
        report.append("[tool.ruff] divergence:")
        report.extend(ruff_diff)
    mypy_diff = _diff_section("mypy", MYPY_FIELDS_TO_COMPARE, parsed)
    if mypy_diff:
        report.append("[tool.mypy] divergence:")
        report.extend(mypy_diff)

    if not report:
        print("No divergence found across consumer repos' [tool.ruff]/[tool.mypy] sections.")
        return 0

    print("Config divergence detected across consumer repos (informational, not a failure):")
    for line in report:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
