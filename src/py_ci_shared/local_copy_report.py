"""Local meta-tests that duplicate a central gate: a list to migrate, and a ratchet so the list only shrinks.

A consumer that copied a meta-test before it was centralised keeps the copy: the central fix never reaches it and the
two drift (the module-reload copies in llm_bench, pyutilz and autopsia each grew their own exemptions). This module
names the copies. A local test FILE is reported against a central gate when it does not import ``py_ci_shared`` (or
pyutilz's ``code_audit``, whose scanners ``code_audit_meta`` runs centrally) and either

* its name says the same thing and it walks a corpus (``ast.walk``/``rglob``/...): the tokens of the gate's name, minus
  generic words, are contained in the file's name tokens and make up at least 80% of them (``test_no_import_cycles.py``
  -> ``import_cycles``); plural and singular forms match; or
* its content matches EVERY regex of a signature the caller or the built-in :data:`CONTENT_SIGNATURES` table gives for
  that gate (the rule's subject together with the AST walk that implements it).

Only test files under a meta-test directory (:data:`META_DIRS`) are judged by default, and a central name must keep two
specific tokens to match by name (``cli`` or ``registry`` alone would match any test).

It is a REPORT: whether a local copy can be replaced is a judgement (it may check a repo-specific rule under a shared
name). :func:`assert_local_copies_do_not_grow` ratchets the list with a baseline, so a NEW copy fails while the known
ones are migrated.

Usage::

    from py_ci_shared.local_copy_report import assert_local_copies_do_not_grow

    def test_no_new_local_copies_of_central_gates(request):
        assert_local_copies_do_not_grow(REPO, baseline_path=HERE / "_local_copies_baseline.json", request=request)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ParsedFile, ScanResult, scan_python
from ._gate_run import enforce_findings

__all__ = [
    "CODE_AUDIT_SCANNERS",
    "CONTENT_SIGNATURES",
    "REFRESH_FLAG",
    "assert_local_copies_do_not_grow",
    "central_gate_names",
    "META_DIRS",
    "find_local_copies",
]

REFRESH_FLAG = "--refresh-local-copies-baseline"
GATE = "local-copies"
RULE = "local-copy"

#: Words that say nothing about which rule a file checks.
_GENERIC = frozenset(
    {"test", "tests", "no", "new", "not", "any", "all", "the", "a", "an", "of", "in", "on", "is", "are", "be", "must", "never", "without", "meta"}
    | {"unsafe", "safety", "safe", "gate", "gates", "check", "checks", "module", "modules", "level", "code", "py", "only", "has"}
    | {"audit", "audits", "fix", "fixes", "regression", "regressions", "integrity", "parity", "coverage", "consistency", "hygiene"}
)
#: A central name must keep this many specific tokens to be matched by name: ``cli``/``registry`` would match everything.
_MIN_NAME_TOKENS = 2
#: ...and share this fraction of the local name's specific tokens (Jaccard), so a long, site-specific name is not a copy.
_MIN_NAME_OVERLAP = 0.8
#: A copy of a gate WALKS a corpus; a per-site regression test named after the rule (``test_default_via_or_trap_gpu``)
#: calls the fixed function and walks nothing, so a name match also needs one of these.
_SCANS_A_CORPUS = re.compile(r"\bast\.(walk|parse)\(|\bNodeVisitor\b|\btokenize\.|\.rglob\(|\.glob\(|\bos\.walk\(")

#: pyutilz code_audit scanners a consumer runs through ``code_audit_meta``: a local copy of one is as redundant as a copy
#: of a py_ci_shared module. Name -> the central target to report.
CODE_AUDIT_SCANNERS: dict[str, str] = {
    "bare_except": "code_audit_meta:bare_except",
    "broad_except": "code_audit_meta:broad_except_swallow",
    "console_unicode": "code_audit_meta:console_unicode",
    "unicode_in_console_output": "code_audit_meta:console_unicode",
    "stale_source_citations": "code_audit_meta:stale_source_citation",
    "mutable_defaults": "code_audit_meta:mutable_defaults",
    "mutable_default_arguments": "code_audit_meta:mutable_defaults",
    "wall_clock_assertion": "code_audit_meta:wall_clock_assertion",
    "unpicklable_resource_state": "code_audit_meta:unpicklable_resource_state",
    "default_via_or": "code_audit_meta:default_via_or",
}

#: Central target -> regexes that must ALL match for a file to be a copy of that rule: each set pairs the rule's subject
#: with the AST walk that implements it, so a test that merely USES the thing (calls ``importlib.reload``) is not a copy.
CONTENT_SIGNATURES: dict[str, tuple[str, ...]] = {
    "module_reload_safety": (r"ast\.walk\(", r"[\"']reload[\"']"),
    "source_text_claims": (r"ast\.walk\(", r"[\"']getsource[\"']"),
    "import_cycles": (r"strongly[_ ]connected|\bstrongconnect\b|\btarjan\b|partially[ -]initiali[sz]ed", r"ast\.(ImportFrom|Import)\b"),
    "nondiscriminating_shapes": (r"ast\.walk\(", r"def _\w*tautolog\w*\("),
    "code_audit_meta:mutable_defaults": (r"ast\.(List|Dict|Set)\b", r"mutable[_ ]default"),
    "code_audit_meta:console_unicode": (r"ast\.walk\(", r"[\"']print[\"']", r"UnicodeEncodeError|non-ASCII|ord\(\w+\)\s*>\s*127"),
}
#: Directory names a local meta-test lives under; ``None`` in :func:`find_local_copies` scans every test file.
META_DIRS = ("test_meta", "meta", "meta_tests")


def _tokens(name: str) -> set[str]:
    out = set()
    for tok in re.split(r"[^a-z0-9]+", name.lower()):
        if tok and tok not in _GENERIC:
            out.add(tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok)
    return out


def central_gate_names(package_dir: Optional[Path] = None) -> list[str]:
    """Public module names of this package (``import_cycles``, ...): every one is a central gate a copy could shadow."""
    here = package_dir or Path(__file__).resolve().parent
    return sorted(p.stem for p in here.glob("*.py") if not p.stem.startswith("_"))


def _imports_central(f: ParsedFile) -> bool:
    aliases = ImportAliases.from_tree(f.tree)
    targets = set(aliases.mapping.values())
    return any(t == "py_ci_shared" or t.startswith(("py_ci_shared.", "pyutilz.dev.code_audit")) for t in targets)


def _name_matches(stem: str, catalogue: Mapping[str, str]) -> list[str]:
    local = _tokens(stem)
    hits: list[str] = []
    for name, target in catalogue.items():
        want = _tokens(name)
        if len(want) >= _MIN_NAME_TOKENS and want <= local and len(want) / len(local) >= _MIN_NAME_OVERLAP and target not in hits:
            hits.append(target)
    return hits


def _content_matches(source: str, signatures: Mapping[str, Iterable[str]]) -> list[str]:
    return [t for t, patterns in signatures.items() if all(re.search(p, source, re.IGNORECASE) for p in patterns)]


def find_local_copies(
    repo_root: Union[str, Path],
    *,
    test_dirs: Iterable[str] = ("tests",),
    meta_dirs: Optional[Iterable[str]] = META_DIRS,
    central: Optional[Iterable[str]] = None,
    extra_central: Optional[Mapping[str, str]] = None,
    signatures: Optional[Mapping[str, Iterable[str]]] = None,
    exclude_parts: Iterable[str] = (),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], list[ScanResult]]:
    """``(findings, scans)``: one finding per (local test file, central target it duplicates).

    *central* defaults to every public module of this package; *extra_central* adds ``name -> target`` pairs on top of
    :data:`CODE_AUDIT_SCANNERS`; *signatures* replaces :data:`CONTENT_SIGNATURES`. Only files under a directory named in
    *meta_dirs* are judged (``None``: every ``test_*.py``); the floor still counts every parsed file.
    """
    meta = None if meta_dirs is None else frozenset(meta_dirs)
    root = Path(repo_root)
    catalogue: dict[str, str] = {n: n for n in (central if central is not None else central_gate_names())}
    catalogue.update(CODE_AUDIT_SCANNERS)
    catalogue.update(extra_central or {})
    sigs = dict(CONTENT_SIGNATURES if signatures is None else signatures)
    findings: list[Finding] = []
    scans: list[ScanResult] = []
    for sub in test_dirs:
        scan = scan_python(root / sub, root=root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
        scans.append(scan)
        for f in scan:
            if not f.path.name.startswith("test_") or (meta is not None and not meta & set(Path(f.rel).parts[:-1])):
                continue
            if _imports_central(f):
                continue
            by_name = _name_matches(f.path.stem, catalogue) if _SCANS_A_CORPUS.search(f.source) else []
            by_content = [t for t in _content_matches(f.source, sigs) if t not in by_name]
            for target, how in [*((t, "name") for t in by_name), *((t, "content") for t in by_content)]:
                findings.append(Finding(f.rel, 1, RULE, f"duplicates central `{target}` (matched by {how}); it does not import py_ci_shared"))
    findings.sort(key=lambda x: (x.path, x.message))
    return findings, scans


def assert_local_copies_do_not_grow(
    repo_root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]],
    test_dirs: Iterable[str] = ("tests",),
    meta_dirs: Optional[Iterable[str]] = META_DIRS,
    central: Optional[Iterable[str]] = None,
    extra_central: Optional[Mapping[str, str]] = None,
    signatures: Optional[Mapping[str, Iterable[str]]] = None,
    exclude_parts: Iterable[str] = (),
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a local copy not in *baseline_path* (and on a stale entry: a migrated copy must leave the baseline).

    ``baseline_path=None`` fails on every copy. A missing baseline fails; refresh with ``REFRESH_FLAG`` or
    ``PY_CI_SHARED_REFRESH=local-copies``.
    """
    findings, scans = find_local_copies(
        repo_root,
        test_dirs=test_dirs,
        meta_dirs=meta_dirs,
        central=central,
        extra_central=extra_central,
        signatures=signatures,
        exclude_parts=exclude_parts,
        use_git=use_git,
    )
    enforce_findings(
        findings,
        scans,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="replace the local copy with a call to the central gate (keep only repo-specific configuration locally)",
    )
