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

# 3.12+ syntax (PEP 695 type params, a loop assert). Only interpreters that parse it take part in the "modern" key (45-T3c).
_MODERN = (
    "type Pair[T] = tuple[T, T]\n\n"
    "class Box[T]:\n"
    "    def get(self, x: T) -> T:\n"
    "        return x\n\n\n"
    "def f(x, y, rows):\n"
    "    z = x or [1, 2]\n"
    "    for (a, b) in rows:\n"
    "        assert a == b\n"
    "    return z, y or dict(k=1)\n"
)

_PROBE = """
import ast, json, sys
from pathlib import Path
from py_ci_shared._core.unparse import unparse_stable
from py_ci_shared.code_audit.assert_in_loop import scan_assert_in_loop_reports_only_the_first
from py_ci_shared.code_audit.default_via_or import scan_default_via_or_trap
from py_ci_shared.drifted_duplicate_functions import _canonical, find_drifted_duplicate_functions
root = Path(sys.argv[1])
modern = Path(sys.argv[2])
out = {}
for name, fn in (("assert_in_loop", scan_assert_in_loop_reports_only_the_first), ("default_via_or", scan_default_via_or_trap)):
    out[name] = sorted((f.check, f.file, f.detail) for f in fn(root))
out["unparse"] = [unparse_stable(n) for n in ast.walk(ast.parse((root / "a.py").read_text())) if isinstance(n, (ast.Lambda, ast.For, ast.comprehension))]
out["canonical"] = [_canonical(ast.parse((root / n).read_text())) for n in ("a.py", "b.py")]
out["drift"] = [(g.name, round(g.similarity, 6)) for g in find_drifted_duplicate_functions([root], similarity=0.5)]
try:
    ast.parse((modern / "m.py").read_text())
except SyntaxError:
    out["modern"] = None  # this interpreter does not parse 3.12+ syntax: it cannot take part in that key
else:
    out["modern"] = sorted((f.check, f.file, f.detail) for fn in (scan_assert_in_loop_reports_only_the_first, scan_default_via_or_trap) for f in fn(modern))
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


_MISSING_MODULE = "ModuleNotFoundError"


def _probe(exe: str, corpus: Path, modern: Path) -> "Optional[dict]":
    """The gate keys one interpreter computes, or None when it lacks a requirement (tomli, pyyaml).

    Any other failure is a crash of the gate code on that interpreter: it raises, with the interpreter's stderr. Dropping it
    (returning None for every non-zero exit) let a 3.15-only break leave the comparison silently (45-T3b).
    """
    env = dict(os.environ, PYTHONPATH=str(_SRC), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([exe, "-c", _PROBE, str(corpus), str(modern)], capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode != 0:
        if _MISSING_MODULE in proc.stderr:
            return None
        raise AssertionError(f"{exe} crashed while computing the gate keys (exit {proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


def _write_corpus(tmp_path: Path) -> "tuple[Path, Path]":
    corpus = tmp_path / "plain"
    modern = tmp_path / "modern"
    corpus.mkdir()
    modern.mkdir()
    for name, text in _CORPUS.items():
        (corpus / name).write_text(text, encoding="utf-8")
    (modern / "m.py").write_text(_MODERN, encoding="utf-8")
    return corpus, modern


def test_gate_keys_are_equal_on_every_available_interpreter(tmp_path: Path) -> None:
    corpus, modern = _write_corpus(tmp_path)
    reference = _probe(sys.executable, corpus, modern)
    assert reference is not None, "the probe must run on the interpreter under test"
    assert reference["assert_in_loop"] or reference["default_via_or"], "the corpus must produce findings or the comparison is empty"
    if reference["modern"] is not None:
        assert reference["modern"], "the 3.12+ fixture must produce a finding on an interpreter that parses it"
    others = [exe for exe in _other_interpreters() if os.path.normcase(exe) != os.path.normcase(sys.executable)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda exe: _probe(exe, corpus, modern), others))
    compared = 0
    for exe, other in zip(others, results):
        if other is None:
            continue
        compared += 1
        assert {k: v for k, v in other.items() if k != "modern"} == {
            k: v for k, v in reference.items() if k != "modern"
        }, f"{exe} disagrees with {sys.executable} on baseline keys"
        if other["modern"] is not None and reference["modern"] is not None:
            assert other["modern"] == reference["modern"], f"{exe} disagrees on the 3.12+ fixture"
    if not compared:
        pytest.skip("no second interpreter with the requirements installed (set PY_CI_SHARED_OTHER_PYTHONS)")


def test_probe_detects_a_divergent_key(tmp_path: Path) -> None:
    """Teeth: the comparison is over data that does change when a key changes."""
    corpus, modern = _write_corpus(tmp_path)
    first = _probe(sys.executable, corpus, modern)
    (corpus / "a.py").write_text(_CORPUS["a.py"].replace("y or dict(k=1)", "y or dict(k=9)"), encoding="utf-8")
    second = _probe(sys.executable, corpus, modern)
    assert first is not None and second is not None and first != second


def test_probe_raises_on_a_crash_that_is_not_a_missing_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Teeth for 45-T3b: a crash on one interpreter fails the comparison instead of dropping that interpreter."""
    corpus, modern = _write_corpus(tmp_path)

    def crashing(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="Traceback ... RuntimeError: boom")

    monkeypatch.setattr(subprocess, "run", crashing)
    with pytest.raises(AssertionError, match="crashed while computing"):
        _probe("python-x", corpus, modern)


def test_probe_skips_an_interpreter_that_lacks_a_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus, modern = _write_corpus(tmp_path)

    def lacking(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'yaml'")

    monkeypatch.setattr(subprocess, "run", lacking)
    assert _probe("python-x", corpus, modern) is None
