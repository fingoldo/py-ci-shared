"""Shared check: the codes a blocking lint gate IGNORES may only shrink.

An ignore list is where a blocking gate stops blocking, and nothing watches it. production_scrapers
(2026-09-11) is the case: F401 sat in both of its blocking ruff gates' ignore lists while the project's
own audit disposition said unused imports were "caught going forward", and 17 had accumulated behind
it. The list said "known debt"; it was really "a place new debt goes unseen".

The rule, per ignored code, against a committed baseline of how many findings it hid:

* the count GREW -- new findings are arriving under the ignore, which is what the ignore was never
  meant to allow;
* the count reached ZERO -- the code must leave the ignore list, or the next finding goes unseen;
* the count SHRANK -- fails until the baseline is refreshed, so it cannot quietly grow back (the
  same stance as ``deferred_drift`` and ``loc_budget``);
* a code is ignored with no baseline entry, or a baseline entry names a code no longer ignored.

Counting runs the project's own ruff with ``--select`` on exactly the ignored codes, so the project
config (per-file ignores, excludes) applies as it does for the gate.

Usage::

    from py_ci_shared.ignore_ratchet import workflow_input_codes, ruff_counts, assert_ignore_list_only_shrinks

    def test_the_blocking_gate_ignore_list_only_shrinks():
        codes = workflow_input_codes(WORKFLOW, job="ruff-blocking")
        assert_ignore_list_only_shrinks(codes, ruff_counts(PROJECT_ROOT, codes), BASELINE)
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path


def _split_codes(value: str) -> list[str]:
    return [c.strip() for c in value.replace(";", ",").split(",") if c.strip()]


def workflow_input_codes(workflow: Path, *, job: str, key: str = "ignore") -> list[str]:
    """The comma-separated codes a reusable-workflow job passes as ``with: <key>``."""
    import yaml

    data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    value = (((data.get("jobs") or {}).get(job) or {}).get("with") or {}).get(key, "")
    return _split_codes(str(value))


def precommit_arg_codes(precommit: Path, *, hook: str, flag: str = "--ignore") -> list[str]:
    """The codes a pre-commit hook (matched by ``id`` or ``alias``) passes after *flag*."""
    import yaml

    data = yaml.safe_load(precommit.read_text(encoding="utf-8")) or {}
    hooks = [h for repo in data.get("repos", []) or [] for h in repo.get("hooks", []) or [] if hook in (h.get("id"), h.get("alias"))]
    if len(hooks) != 1:
        raise LookupError(f"{precommit.name}: {len(hooks)} hooks named {hook!r}, expected exactly one")
    args = [str(a) for a in hooks[0].get("args", []) or []]
    codes: list[str] = []
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            codes += _split_codes(args[i + 1])
        elif arg.startswith(flag + "="):
            codes += _split_codes(arg.split("=", 1)[1])
    return codes


def ruff_counts(root: Path, codes: Iterable[str], *, target: str = ".", ruff: Sequence[str] = (sys.executable, "-m", "ruff")) -> dict[str, int]:
    """How many findings each code has in *root* under the project's own ruff config."""
    wanted = sorted(set(codes))
    if not wanted:
        return {}
    cmd = [*ruff, "check", target, "--select", ",".join(wanted), "--output-format", "json", "--exit-zero", "--no-cache"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if out.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed ({out.returncode}): {out.stderr.strip()[:400]}")
    counts = collections.Counter(item.get("code") for item in json.loads(out.stdout or "[]"))
    return {code: counts.get(code, 0) for code in wanted}


def ratchet_problems(codes: Iterable[str], counts: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """One problem string per way the ignore list and its baseline disagree with the tree."""
    ignored = set(codes)
    problems: list[str] = []
    for code in sorted(ignored):
        now = counts.get(code, 0)
        if code not in baseline:
            problems.append(f"{code}: ignored by the gate with no baseline entry ({now} finding(s)) -- record it, or stop ignoring it")
        elif now == 0:
            problems.append(f"{code}: 0 findings left -- remove it from the ignore list, or the next one goes unseen")
        elif now > baseline[code]:
            problems.append(f"{code}: {now} findings, up from {baseline[code]} -- new debt is arriving under the ignore")
        elif now < baseline[code]:
            problems.append(f"{code}: {now} findings, down from {baseline[code]} -- refresh the baseline so the smaller number is locked in")
    problems += [f"{code}: in the baseline but no longer ignored -- remove the entry" for code in sorted(set(baseline) - ignored)]
    return problems


def write_ignore_baseline(path: Path, counts: dict[str, int]) -> None:
    path.write_text(json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8")


def assert_ignore_list_only_shrinks(codes: Iterable[str], counts: dict[str, int], baseline_path: Path) -> None:
    import pytest

    codes = list(codes)
    if not codes:
        pytest.fail("the gate's ignore list parsed as empty -- the workflow moved or the key changed, and this would check nothing")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    problems = ratchet_problems(codes, counts, baseline)
    if problems:
        pytest.fail(f"{len(problems)} problem(s) with the gate's ignore list ({baseline_path.name}):\n  " + "\n  ".join(problems))
