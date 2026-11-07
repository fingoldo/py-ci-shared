"""The same gate corpus on two interpreters must give the same baseline keys (audit 45-F5).

Every divergence found so far (``ast.unparse`` shape, ``ast.dump`` field printing, ``sys.stdlib_module_names``) was found by a
consumer's red shard. This runs one corpus through unparse-keyed and fingerprint-keyed gates in a subprocess per interpreter and
compares the key lists. It needs a second interpreter with the requirements installed (CI legs that have one run it); otherwise it skips.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

_CORPUS = {
    "a.py": (
        "def f(x, y, rows):\n"
        "    z = x or [1, 2]\n"
        "    g = lambda: 1\n"
        "    for (a, b) in rows:\n"
        "        assert a == b\n"
        "    return [q for (q, r) in rows], z, g, y or dict(k=1)\n"
    ),
    "b.py": "def f(x, y, rows):\n    z = x or [1, 2]\n    return z, y or dict(k=1)\n",
    "c.py": "def f(x, y, rows):\n    z = x or [1, 2]\n    return z, y or dict(k=2)\n",
}

_PROBE = """
import ast, json, sys
from pathlib import Path
from py_ci_shared._core.unparse import unparse_stable
from py_ci_shared.code_audit.assert_in_loop import scan_assert_in_loop_reports_only_the_first
from py_ci_shared.code_audit.default_via_or import scan_default_via_or_trap
from py_ci_shared.drifted_duplicate_functions import _canonical, find_drifted_duplicate_functions
root = Path(sys.argv[1])
out = {}
for name, fn in (("assert_in_loop", scan_assert_in_loop_reports_only_the_first), ("default_via_or", scan_default_via_or_trap)):
    out[name] = sorted((f.check, f.file, f.detail) for f in fn(root))
out["unparse"] = [unparse_stable(n) for n in ast.walk(ast.parse((root / "a.py").read_text())) if isinstance(n, (ast.Lambda, ast.For, ast.comprehension))]
out["canonical"] = [_canonical(ast.parse((root / n).read_text())) for n in ("a.py", "b.py")]
out["drift"] = [(g.name, round(g.similarity, 6)) for g in find_drifted_duplicate_functions([root], similarity=0.5)]
print(json.dumps(out, sort_keys=True))
"""


def _other_interpreters() -> "list[str]":
    """One interpreter per minor version >= 3.9 under uv's python directory (the newest patch), plus ``PY_CI_SHARED_OTHER_PYTHONS``."""
    newest: "dict[str, str]" = {}
    uv_root = Path(os.environ.get("APPDATA", "")) / "uv" / "python"
    for exe in sorted(uv_root.glob("cpython-3.*.*-*/python.exe")) if uv_root.is_dir() else []:
        version = exe.parent.name.split("-")[1]
        minor = version.rsplit(".", 1)[0]
        if tuple(int(part) for part in minor.split(".")) >= (3, 9):
            newest[minor] = str(exe)
    extra = os.environ.get("PY_CI_SHARED_OTHER_PYTHONS", "")
    return list(newest.values()) + [item for item in extra.split(os.pathsep) if item]


def _probe(exe: str, corpus: Path) -> "Optional[dict]":
    env = dict(os.environ, PYTHONPATH=str(_SRC), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([exe, "-c", _PROBE, str(corpus)], capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode != 0:
        return None  # the interpreter lacks a requirement (tomli, pyyaml): it cannot take part
    return json.loads(proc.stdout)


def test_gate_keys_are_equal_on_every_available_interpreter(tmp_path: Path) -> None:
    for name, text in _CORPUS.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    reference = _probe(sys.executable, tmp_path)
    assert reference is not None, "the probe must run on the interpreter under test"
    assert reference["assert_in_loop"] or reference["default_via_or"], "the corpus must produce findings or the comparison is empty"
    others = [exe for exe in _other_interpreters() if os.path.normcase(exe) != os.path.normcase(sys.executable)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda exe: _probe(exe, tmp_path), others))
    compared = 0
    for exe, other in zip(others, results):
        if other is None:
            continue
        compared += 1
        assert other == reference, f"{exe} disagrees with {sys.executable} on baseline keys"
    if not compared:
        pytest.skip("no second interpreter with the requirements installed (set PY_CI_SHARED_OTHER_PYTHONS)")


def test_probe_detects_a_divergent_key(tmp_path: Path) -> None:
    """Teeth: the comparison is over data that does change when a key changes."""
    for name, text in _CORPUS.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    first = _probe(sys.executable, tmp_path)
    (tmp_path / "a.py").write_text(_CORPUS["a.py"].replace("y or dict(k=1)", "y or dict(k=9)"), encoding="utf-8")
    second = _probe(sys.executable, tmp_path)
    assert first is not None and second is not None and first != second
