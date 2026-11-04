"""Reports [tool.ruff]/[tool.mypy] config divergence across consumer repos.

Fetches pyproject.toml from each consumer repo's default branch and diffs the fields that are
meant to stay in sync (line-length, target-version, the shared `extend` path, mypy strictness
flags) against each other. This is informational, not a pass/fail gate: legitimate per-repo
divergence exists (e.g. a repo mid-migration to a new rule), so the scheduled workflow that runs
this posts the report as a step summary rather than failing the run -- see README.md's "Keeping
this repo in sync with consumers" section for why no automated gate existed before this script.

A field set in one repo and absent from another IS divergence (the absent one runs the tool's
default), and values are compared by their canonical JSON form, so list- and table-valued fields
(``extend-select``, per-module overrides) are compared instead of crashing the report.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional

from ._consumers import load_consumers
from ._toml_compat import tomllib

# The two repos this report began with; used only when configs/consumers.toml is not beside the package (an installed wheel).
FALLBACK_CONSUMERS = {
    "mlframe": "https://raw.githubusercontent.com/fingoldo/mlframe/master/pyproject.toml",
    "pyutilz": "https://raw.githubusercontent.com/fingoldo/pyutilz/master/pyproject.toml",
}
CONSUMERS_FILE = Path(__file__).resolve().parents[2] / "configs" / "consumers.toml"
TOKEN_ENV = "CONSUMER_READ_TOKEN"


def consumer_urls(path: Path = CONSUMERS_FILE, token: Optional[str] = None) -> tuple[dict[str, str], list[str]]:
    """``({name: url of its pyproject.toml}, [private consumers skipped for want of a token])`` for every consumer in ``configs/consumers.toml``.

    A public repo is read from raw.githubusercontent.com on its own ``branch``; a private one through the contents API, which ``_fetch_pyproject`` authenticates with
    ``CONSUMER_READ_TOKEN``. Before 34-F3 two repos were hard-coded (and on ``master``), so ruff/mypy drift in the other consumers never showed.
    """
    if not path.is_file():
        return dict(FALLBACK_CONSUMERS), []
    urls: dict[str, str] = {}
    skipped: list[str] = []
    for consumer in load_consumers(path):
        if consumer.private:
            if not (token if token is not None else os.environ.get(TOKEN_ENV)):
                skipped.append(consumer.name)
                continue
            urls[consumer.name] = f"https://api.github.com/repos/{consumer.repo}/contents/pyproject.toml?ref={consumer.branch}"
        else:
            urls[consumer.name] = f"https://raw.githubusercontent.com/{consumer.repo}/{consumer.branch}/pyproject.toml"
    return urls, skipped


RUFF_FIELDS_TO_COMPARE = ("extend", "target-version", "line-length")
MYPY_FIELDS_TO_COMPARE = ("python_version", "disallow_untyped_defs", "warn_return_any")


def _fetch_pyproject(url: str) -> dict:
    headers = {}
    token = os.environ.get(TOKEN_ENV)
    if token and url.startswith("https://api.github.com/"):
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"}
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=15
    ) as resp:  # nosec B310 -- fixed, first-party GitHub raw-content URLs, not user input
        doc: dict = tomllib.loads(resp.read().decode("utf-8"))
        return doc


_MISSING = "<missing>"


def _canonical(value: Any) -> str:
    """A hashable, order-stable form of any TOML value (lists and tables included)."""
    return _MISSING if value is None else json.dumps(value, sort_keys=True, default=str)


def _diff_section(section_name: str, fields: tuple, parsed: dict) -> list[str]:
    lines = []
    values = {}
    for repo, doc in parsed.items():
        if section_name not in doc.get("tool", {}):
            continue  # a consumer without the table (a Flutter or docs repo) is not "diverging" on a tool it does not use
        section = doc["tool"][section_name]
        values[repo] = {f: section.get(f) for f in fields}
    for field in fields:
        seen = {repo: v[field] for repo, v in values.items()}
        distinct = {_canonical(v) for v in seen.values()}
        if len(distinct) > 1:
            shown = {repo: (v if v is not None else _MISSING) for repo, v in seen.items()}
            lines.append(f"  [tool.{section_name}].{field} diverges: {shown}")
    return lines


def main(consumers: Optional[Mapping[str, str]] = None, fetch: Optional[Callable[[str], dict]] = None) -> int:
    parsed = {}
    fetch_one = fetch or _fetch_pyproject
    skipped: list[str] = []
    if consumers is None:
        consumers, skipped = consumer_urls()
    if skipped:
        print(f"WARNING: {len(skipped)} private consumer(s) skipped, {TOKEN_ENV} is not set: {', '.join(skipped)}", file=sys.stderr)
    for repo, url in consumers.items():
        # PERF203: 2 network calls total, not a hot loop; broad catch so one consumer's fetch failure doesn't abort the report
        try:
            parsed[repo] = fetch_one(url)
        except Exception as e:  # noqa: PERF203
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
