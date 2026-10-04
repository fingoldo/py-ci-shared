"""Tests for py_ci_shared.stub_signature_parity: the parameter rule, the static scan, and its teeth."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

import py_ci_shared.stub_signature_parity as gate
from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.stub_signature_parity import (
    assert_stub_signature_parity,
    find_stub_signature_parity,
    missing_parameters,
    params_of_callable,
    params_of_node,
)

BOM = b"\xef\xbb\xbf"
CANARY = Path(__file__).resolve().parent / "canary" / "stub_signature_parity"


def _corpus(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(text.encode())
    return tmp_path


def _canary(tmp_path: Path, case: str) -> Path:
    for p in (CANARY / case).rglob("*.canary"):
        dest = tmp_path / p.relative_to(CANARY / case).with_suffix("")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
    return tmp_path


def _missing(real_src: str, stub_src: str) -> list[str]:
    real = ast.parse(real_src).body[0]
    stub = ast.parse(stub_src).body[0]
    stub_node = stub.value if isinstance(stub, ast.Expr) else stub
    assert isinstance(real, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert isinstance(stub_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    return missing_parameters(params_of_node(real), params_of_node(stub_node))


# ---- the rule ------------------------------------------------------------------------------------------------------


def test_the_three_dashboard_stubs_each_miss_exactly_the_new_parameter():
    assert _missing(
        "async def submit_approved(conn, submission_id, request, *, provider=None, expected_dry_run=None): ...",
        "async def _submit(conn, submission_id, request, provider=None): ...",
    ) == ["expected_dry_run"]
    # `*_a` takes positions, not the `limit=` the caller passes by keyword
    assert _missing("def missing_detail_transaction_ids(conn, ace_id, *, limit=200): ...", 'lambda *_a: ["t1"]') == ["limit"]
    assert _missing("def _questions_line(job_uid, fl_cid, saved_answers=None): ...", 'lambda job_uid, fl_cid: "q"') == ["saved_answers"]


def test_the_fixed_stubs_accept_everything():
    assert _missing("def f(conn, sid, *, provider=None, expected_dry_run=None): ...", "def s(conn, sid, provider=None, expected_dry_run=None): ...") == []
    assert _missing("def f(conn, ace_id, *, limit=200): ...", "lambda *_a, **_k: 1") == []
    assert _missing("def f(a, b, c=None): ...", "lambda a, b, c=None: 1") == []


@pytest.mark.parametrize(
    ("real", "stub", "missing"),
    [
        ("def f(a, b): ...", "lambda _x, _y: 1", []),  # required positionals may be renamed
        ("def f(a, b): ...", "lambda _x: 1", ["b"]),
        ("def f(a, b): ...", "lambda *a: 1", []),
        ("def f(a, b): ...", "lambda *, a, b: 1", []),  # same names, keyword-only: callable by keyword
        ("def f(a, /, b): ...", "lambda **k: 1", ["a", "b"]),  # positional-only needs a slot; `b` needs a slot or its name
        ("def f(a, b=1): ...", "lambda a, c=1: 1", ["b"]),  # a defaulted parameter is passed by keyword
        ("def f(*, k): ...", "lambda **kw: 1", []),
        ("def f(*args): ...", "lambda: 1", []),  # variadics name no parameter
        ("def f(a, **kw): ...", "lambda *x: 1", []),
        ("def f(): ...", "lambda x=1: 1", []),
    ],
)
def test_the_parameter_rule(real, stub, missing):
    assert _missing(real, stub) == missing


def test_live_signatures_agree_with_the_ast_reading():
    def real(a, /, b, c=1, *args, d, e=2, **kw):
        return a

    node = ast.parse("def real(a, /, b, c=1, *args, d, e=2, **kw): ...").body[0]
    assert isinstance(node, ast.FunctionDef)
    assert params_of_callable(real) == params_of_node(node)
    assert params_of_callable(object()) is None


# ---- the static scan -----------------------------------------------------------------------------------------------


def test_reports_the_three_motivating_stubs_by_test_target_and_parameter(tmp_path):
    found = find_stub_signature_parity(_canary(tmp_path, "violation"), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 8, gate.RULE), ("seed.py", 12, gate.RULE), ("seed.py", 16, gate.RULE)]
    m0, m1, m2 = (f.message for f in found)
    assert m0.startswith("test_submit_reports_the_outcome: stub _submit(") and "seedpkg.real.submit_approved(" in m0
    assert m0.endswith("cannot accept expected_dry_run")
    assert "seedpkg.real.missing_detail_transaction_ids" in m1 and m1.endswith("cannot accept limit")
    assert "seedpkg.real._questions_line" in m2 and m2.endswith("cannot accept saved_answers")
    with pytest.raises(AssertionError, match="3 stub-signature-parity finding"):
        assert_stub_signature_parity(tmp_path, use_git=False)


def test_negative_control_the_fixed_stubs_are_not_reported(tmp_path):
    assert find_stub_signature_parity(_canary(tmp_path, "clean"), use_git=False) == []
    assert_stub_signature_parity(tmp_path, use_git=False)


REAL = "def fetch(conn, *, limit=10):\n    return conn\n\n\nclass Store:\n    def load(self, key, *, fresh=False):\n        return key\n"


@pytest.mark.parametrize(
    ("test_src", "expected"),
    [
        ('import pkg.real\n\ndef test_a(monkeypatch):\n    monkeypatch.setattr("pkg.real.fetch", lambda conn: 1)\n', ["limit"]),
        ("from pkg import real\nfrom unittest import mock\n\ndef test_a():\n    mock.patch.object(real, 'fetch', lambda c: 1)\n", ["limit"]),
        ("from unittest.mock import patch\n\ndef test_a():\n    patch('pkg.real.fetch', side_effect=lambda c: 1)\n", ["limit"]),
        ("from pkg.real import Store\n\ndef test_a(monkeypatch):\n    monkeypatch.setattr(Store, 'load', lambda self, key: 1)\n", ["fresh"]),
        # a re-export is followed to the def
        ("from pkg import facade\n\ndef test_a(monkeypatch):\n    monkeypatch.setattr(facade, 'fetch', lambda c: 1)\n", ["limit"]),
        # a stub bound by name at module level
        ("from pkg import real\n\n_stub = lambda c: 1\n\ndef test_a(monkeypatch):\n    monkeypatch.setattr(real, 'fetch', _stub)\n", ["limit"]),
    ],
)
def test_target_spellings_resolve(tmp_path, test_src, expected):
    root = _corpus(tmp_path, {"pkg/__init__.py": "", "pkg/real.py": REAL, "pkg/facade.py": "from pkg.real import fetch\n", "tests/test_x.py": test_src})
    found = find_stub_signature_parity(root, use_git=False)
    assert len(found) == 1 and found[0].message.endswith("cannot accept " + ", ".join(expected)), found


def test_unresolvable_targets_are_left_to_the_plugin(tmp_path):
    src = (
        "import os\nfrom pkg import real\n\n"
        "def test_a(monkeypatch):\n"
        "    store = object()\n"
        "    monkeypatch.setattr(store, 'fetch', lambda: 1)\n"  # a local object: not resolvable statically
        "    monkeypatch.setattr(os, 'getcwd', lambda x: 1)\n"  # not in the tree
        "    monkeypatch.setattr(real, 'fetch', make_stub())\n"  # a stub the scan cannot see
        "    monkeypatch.setattr(real, 'LIMIT', 5)\n"
    )
    root = _corpus(tmp_path, {"pkg/__init__.py": "", "pkg/real.py": REAL + "LIMIT = 1\n", "tests/test_x.py": src})
    assert find_stub_signature_parity(root, use_git=False) == []


def test_scanned_from_inside_the_package_the_target_still_resolves(tmp_path):
    src = "from dashboard import db\n\ndef test_a(monkeypatch):\n    monkeypatch.setattr(db, 'borrowed', lambda: 1)\n"
    root = _corpus(tmp_path, {"dashboard/db.py": "def borrowed(*, autocommit=False):\n    return 1\n", "dashboard/tests/test_x.py": src})
    found = find_stub_signature_parity(root / "dashboard", use_git=False)
    assert len(found) == 1 and found[0].message.endswith("cannot accept autocommit")


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_stub_signature_parity(_canary(tmp_path / "plain", "violation"), use_git=False)
    bom = find_stub_signature_parity(_canary(tmp_path / "bom", "bom"), use_git=False)
    assert plain and [(f.path, f.line, f.message) for f in bom] == [(f.path, f.line, f.message) for f in plain]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _canary(tmp_path, "violation")
    (root / "broken.py").write_bytes(b"def broken(:\n")
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_stub_signature_parity(root, use_git=False)
    assert len(find_stub_signature_parity(root, use_git=False, allow_unparsed=True)) == 3


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_stub_signature_parity(tmp_path, use_git=False)


def test_a_baseline_accepts_the_recorded_findings(tmp_path, monkeypatch):
    root = _canary(tmp_path / "c", "violation")
    baseline = tmp_path / "baseline.json"
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    with pytest.raises(pytest.skip.Exception, match="3 entr"):
        assert_stub_signature_parity(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_stub_signature_parity(root, baseline_path=baseline, use_git=False)


# ---- teeth ---------------------------------------------------------------------------------------------------------


def _mutant(old: str, new: str, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the substitution {old!r} no longer matches the gate's source"
    module = types.ModuleType("py_ci_shared._stub_signature_parity_mutant")
    module.__package__ = "py_ci_shared"
    monkeypatch.setitem(sys.modules, module.__name__, module)  # dataclasses look their module up there
    exec(compile(source.replace(old, new), gate.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize(
    ("old", "new", "lost"),
    [
        # keyword-only parameters: `expected_dry_run`
        ("            ok = VARKW in stub_kinds or p.name in stub_names\n", "            ok = True\n", "expected_dry_run"),
        # defaulted positional-or-keyword parameters taken through *args or not at all: `saved_answers`
        (
            "(by_name if p.has_default else by_position or p.name in stub_names)",
            "(True if p.has_default else by_position or p.name in stub_names)",
            "saved_answers",
        ),
    ],
)
def test_teeth_reverting_the_core_condition_loses_the_motivating_case(tmp_path, monkeypatch, old, new, lost):
    root = _canary(tmp_path, "violation")
    assert any(f.message.endswith(lost) for f in find_stub_signature_parity(root, use_git=False))
    mutant = _mutant(old, new, monkeypatch)
    assert not any(f.message.endswith(lost) for f in mutant.find_stub_signature_parity(root, use_git=False)), "the mutant still bites"
