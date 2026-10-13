"""Tests for py_ci_shared.emitted_vocabulary_literals."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.emitted_vocabulary_literals import (
    RULE,
    EmitSite,
    assert_emitted_vocabulary_literals,
    find_emitted_vocabulary_literals,
)

BOM = b"\xef\xbb\xbf"
SITES = [EmitSite("Disclosure", keyword="kind")]
PUBLISHED = {"composed", "priced"}


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root: Path, **kw):
    kw.setdefault("sites", SITES)
    kw.setdefault("published", PUBLISHED)
    return find_emitted_vocabulary_literals(root, use_git=False, **kw)


VIOLATION = b'def f():\n    a = Disclosure(kind="composed")\n    b = Disclosure(kind="priced")\n    return Disclosure(kind="rebased")\n'


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"pkg/emit.py": VIOLATION})
    found = _find(root, vocabulary="docs/v.json#kind")
    assert [(f.path, f.line, f.rule, f.message) for f in found] == [
        ("pkg/emit.py", 4, RULE, "Disclosure(kind) emits 'rebased', which docs/v.json#kind does not hold"),
    ]
    with pytest.raises(AssertionError, match="rebased"):
        assert_emitted_vocabulary_literals(root, sites=SITES, published=PUBLISHED, use_git=False)


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    # Every resolution path the gate follows, each landing on a published member.
    root = _corpus(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/kinds.py": b'PRICED = "priced"\n\nclass Kinds:\n    COMPOSED = "composed"\n',
            "pkg/emit.py": (
                b"from pkg.kinds import PRICED, Kinds\n"
                b"from pkg import kinds\n"
                b'LOCAL = "composed"\n\n'
                b"def _pick(flag):\n"
                b'    if flag:\n        return 1.0, "priced", ""\n'
                b'    return 0.0, LOCAL, ""\n\n'
                b"def f(flag):\n"
                b"    mean, kind, _ = _pick(flag)\n"
                b"    a, b = PRICED, Kinds.COMPOSED\n"
                b"    out = [Disclosure(kind=kind), Disclosure(kind=a), Disclosure(kind=b)]\n"
                b'    out.append(Disclosure(kind=LOCAL if flag else "priced"))\n'
                b"    out.append(Disclosure(kind=kinds.PRICED))\n"
                b'    out.append(Other(kind="anything"))\n'
                b"    return out\n"
            ),
        },
    )
    assert _find(root) == []


def test_an_unresolvable_argument_is_reported_unless_passed_through(tmp_path):
    root = _corpus(
        tmp_path,
        {"emit.py": b'def f(u, k):\n    Disclosure(kind="composed")\n    Disclosure(kind="priced")\n    Disclosure(kind=u.reason)\n'},
    )
    found = _find(root)
    assert len(found) == 1 and "`u.reason`" in found[0].message and found[0].line == 4
    assert _find(root, pass_through={"u.reason"}) == []


def test_a_parameter_forwarded_is_not_a_value_the_gate_can_vouch_for(tmp_path):
    root = _corpus(tmp_path, {"emit.py": b'def f(kind):\n    Disclosure(kind="composed")\n    Disclosure(kind="priced")\n    return Disclosure(kind=kind)\n'})
    assert ["`kind`" in f.message for f in _find(root)] == [True]


def test_positional_slots_are_swept_and_others_are_not(tmp_path):
    root = _corpus(tmp_path, {"emit.py": b'Match(1, "x", "composed")\nMatch(1, "x", "priced")\nMatch(1, "invented", "priced")\n'})
    assert _find(root, sites=[EmitSite("Match", positions=(2,))]) == []
    found = _find(root, sites=[EmitSite("Match", positions=(1,))], check_unemitted=False)
    assert sorted(f.message for f in found) == [
        "Match(arg1) emits 'invented', which the published vocabulary does not hold",
        "Match(arg1) emits 'x', which the published vocabulary does not hold",
        "Match(arg1) emits 'x', which the published vocabulary does not hold",
    ]


def test_a_published_member_no_site_emits_is_reported_against_the_vocabulary(tmp_path):
    root = _corpus(tmp_path, {"emit.py": b'Disclosure(kind="composed")\n'})
    found = _find(root, vocabulary="docs/v.json#kind")
    assert [(f.path, f.message) for f in found] == [("docs/v.json#kind", "'priced' is published, and no emit site sends it")]
    assert _find(root, check_unemitted=False) == []


def test_a_class_default_of_the_field_counts_as_emitted(tmp_path):
    root = _corpus(
        tmp_path,
        {"emit.py": b'class Disclosure:\n    kind: str = "priced"\n\nDisclosure(kind="composed")\nDisclosure()\n'},
    )
    assert _find(root) == []


def test_ignored_sentinels_are_neither_reported_nor_required(tmp_path):
    root = _corpus(tmp_path, {"emit.py": b'Disclosure(kind="")\nDisclosure(kind="composed")\nDisclosure(kind="priced")\n'})
    assert len(_find(root)) == 1
    assert _find(root, ignore_values={""}) == []


def test_an_empty_site_list_or_vocabulary_is_refused(tmp_path):
    root = _corpus(tmp_path, {"emit.py": b"x = 1\n"})
    with pytest.raises(ValueError, match="EmitSite"):
        _find(root, sites=[])
    with pytest.raises(ValueError, match="empty"):
        _find(root, published=set())


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _find(_corpus(tmp_path / "a", {"emit.py": VIOLATION}))
    bom = _find(_corpus(tmp_path / "b", {"emit.py": BOM + VIOLATION}))
    assert plain and [(f.line, f.message) for f in bom] == [(f.line, f.message) for f in plain]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "emit.py": b'Disclosure(kind="composed")\nDisclosure(kind="priced")\n'})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path)


def test_a_baseline_accepts_recorded_findings(tmp_path):
    root = _corpus(tmp_path / "src", {"emit.py": VIOLATION})
    baseline = tmp_path / "baseline.json"
    # A refresh rewrites the baseline and skips the test that ran it, saying so.
    with pytest.raises(pytest.skip.Exception, match="rewritten"):
        assert_emitted_vocabulary_literals(root, sites=SITES, published=PUBLISHED, baseline_path=baseline, refresh=True, grow=True, use_git=False)
    assert_emitted_vocabulary_literals(root, sites=SITES, published=PUBLISHED, baseline_path=baseline, use_git=False)
    (root / "emit.py").write_bytes(VIOLATION + b'Disclosure(kind="another")\n')
    with pytest.raises(BaseException, match="another"):
        assert_emitted_vocabulary_literals(root, sites=SITES, published=PUBLISHED, baseline_path=baseline, use_git=False)


def test_constants_resolve_when_the_scan_root_is_the_package_itself(tmp_path):
    # Scanning `autopsia/` directly: `from autopsia.kinds import X` and `from .kinds import Y` name files the scan
    # sees as `kinds.py`. Both used to be unresolvable, and every emit through a constant read as a finding.
    root = _corpus(
        tmp_path,
        {
            "kinds.py": b'PRICED = "priced"\nCOMPOSED = "composed"\n',
            "a.py": b"from pkg.kinds import PRICED\nDisclosure(kind=PRICED)\n",
            "b.py": b"from .kinds import COMPOSED\nDisclosure(kind=COMPOSED)\n",
        },
    )
    assert _find(root) == []
