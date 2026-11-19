"""Dump every code-audit finding over a package as sorted JSON, one entry per finding, so an optimisation can be checked for identical output.

Usage: PYTHONPATH=<py-ci-shared>/src python dump_code_audit_findings.py <package-dir> <out.json> [exclude_dir ...]
Run it before and after a change and compare the two files: they must be byte-identical (same findings, same order after sorting).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from py_ci_shared import code_audit  # noqa: F401  (registers the scanners)
from py_ci_shared.code_audit import registry

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
exclude = frozenset(sys.argv[3:]) or frozenset({"legacy", "benchmarks", "profiling", "_benchmarks"})
rows = []
failed = {}
for name, fn in registry._SCANNERS.items():
    findings, error = registry._run_scanner(name, fn, root, exclude)
    if error:
        failed[name] = error
    rows.extend([f.check, f.file, f.line, f.severity, f.snippet, f.detail] for f in findings)
rows.sort()
out.write_text(json.dumps({"failed": failed, "findings": rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print(f"{len(rows)} findings, {len(failed)} failed scanners -> {out}")
