"""Gate-teeth self-check over the canary corpus in ``tests/canary/`` (audit 2026-09-24, INFRA-2 and INFRA-3).

Each ``tests/canary/<gate>/`` holds a tiny corpus for one corpus-scanning gate:

* ``violation/``: the minimal seeded defect the gate exists to report;
* ``clean/``: the nearest correct code, which it must not report;
* ``bom/``: the violation with a UTF-8 BOM in front of every file, which must still be reported as the violation;
* ``unparsable/``: a file the gate cannot parse, laid over ``clean/``, which must fail the gate by name;
* ``support/`` (optional): baselines, a README or a pyproject the gate needs, laid into every variant.

Fixture files carry a ``.canary`` suffix so that neither pytest nor this repository's own gates collect or scan
them; :func:`_materialize` strips it while copying the corpus into ``tmp_path``. Nothing is ever written into the
repository. Every gate is driven through its public ``assert_*`` entry, since several ``find_*`` functions leave
unparsed files to the assert by design; a gate that raises for the violation must name the seed, so a raise
caused by bad test configuration cannot pass for teeth.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Callable, Optional

import pytest
from _gate_teeth_canaries import EXEMPT
from _gate_teeth_canaries_more import CANARIES

from py_ci_shared import registry

logger = logging.getLogger(__name__)

CANARY_DIR = Path(__file__).resolve().parent / "canary"
SUFFIX = ".canary"
BOM = b"\xef\xbb\xbf"

def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*" + SUFFIX) if p.is_file())


def _copy(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    for p in _files(src):
        target = dst / p.relative_to(src).as_posix()[: -len(SUFFIX)]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())


def _materialize(gate: str, variant: str, tmp_path: Path) -> Path:
    base = CANARY_DIR / gate
    d = tmp_path / "corpus"
    d.mkdir(parents=True)
    _copy(base / "support", d)
    if variant == "empty":
        # The clean corpus's directories without its files, so a floor is tested rather than a missing root.
        for sub in (base / "clean").rglob("*"):
            if sub.is_dir():
                (d / sub.relative_to(base / "clean")).mkdir(parents=True, exist_ok=True)
        return d
    if variant == "unparsable":
        _copy(base / "clean", d)
    _copy(base / variant, d)
    return d


def _normalized(text: str, root: Path) -> str:
    return text.replace("\\", "/").replace(root.as_posix(), "<corpus>")


def _outcome(fn: Callable[[], object]) -> Optional[str]:
    """``None`` when the gate passed; the failure text when it raised. A skip counts as a pass: it is silent."""
    try:
        fn()
    except pytest.skip.Exception as exc:
        logger.debug("gate skipped, counted as a pass: %s", type(exc).__name__)
        return None
    except (Exception, pytest.fail.Exception) as exc:
        logger.debug("gate raised, reported as a failure: %s", type(exc).__name__)
        return f"{type(exc).__name__}: {exc}"
    return None


def _cases():
    for name in sorted(CANARIES):
        c = CANARIES[name]
        for case in ("violation", "clean", "bom", "unparsable", "empty"):
            if case == "unparsable" and not c.parses:
                continue
            if case == "empty" and not c.empty:
                continue
            marks = [pytest.mark.xfail(strict=True, reason=c.xfail[case])] if case in c.xfail else []
            yield pytest.param(name, case, id=f"{name}-{case}", marks=marks)


@pytest.mark.parametrize(("gate", "case"), list(_cases()))
def test_gate_bites_its_canary(gate: str, case: str, tmp_path: Path) -> None:
    c = CANARIES[gate]
    d = _materialize(gate, case, tmp_path)
    failure = _outcome(lambda: c.run(d))
    if case == "clean":
        assert failure is None, f"{gate} reported its clean control:\n{failure}"
    elif case == "violation":
        assert failure is not None, f"{gate} missed its seeded violation"
        assert c.token in failure, f"{gate} failed on the violation, but not naming the seed {c.token!r}:\n{failure}"
        missing = [tok for tok in c.tokens if tok not in failure]
        assert not missing, f"{gate} reported its violation but missed seeded shape(s) {missing}:\n{failure}"
    elif case == "bom":
        assert failure is not None, f"{gate} passed a BOM-prefixed copy of its violation"
        assert c.token in failure, f"{gate} failed on the BOM variant without naming the seed {c.token!r}:\n{failure}"
        missing = [tok for tok in c.tokens if tok not in failure]
        assert not missing, f"{gate} reported its BOM violation but missed seeded shape(s) {missing}:\n{failure}"
        # The BOM copy must be reported exactly as the plain violation is, not as an unreadable file.
        plain_dir = _materialize(gate, "violation", tmp_path / "plain")
        plain = _outcome(lambda: c.run(plain_dir))
        assert plain is not None
        assert _normalized(failure, d) == _normalized(
            plain, plain_dir
        ), f"{gate} reports the BOM variant differently from the plain violation:\nBOM:   {failure}\nplain: {plain}"
    elif case == "unparsable":
        base = CANARY_DIR / gate
        broken = [p.relative_to(base / "unparsable") for p in _files(base / "unparsable")]
        names = [p.name[: -len(SUFFIX)] for p in broken]
        assert failure is not None, f"{gate} passed a corpus holding the unparsable {names}"
        # A broken file laid over a same-named clean one is the single path the caller passed; any failure surfaces it.
        replaced = all((base / "clean" / p).is_file() for p in broken)
        assert replaced or any(n in failure or Path(n).stem in failure for n in names), f"{gate} failed, but without naming the unparsable {names}:\n{failure}"
    else:
        assert failure is not None, f"{gate} passed an empty corpus: no floor"


def _parse_error(path: Path, data: bytes) -> Optional[str]:
    """Why ``data`` does not parse as its file type, or ``None`` when it parses (or the type has no parser here)."""
    name = path.name[: -len(SUFFIX)]
    text = data.decode("utf-8-sig")
    try:
        if name.endswith(".py"):
            ast.parse(text)
        elif name.endswith((".toml", "uv.lock")):
            from py_ci_shared._toml_compat import tomllib

            tomllib.loads(text)
        elif name.endswith((".conf", ".conf.template")):
            from py_ci_shared.nginx_location_headers import parse_nginx

            parse_nginx(text)
        elif name.endswith((".yml", ".yaml")):
            yaml = pytest.importorskip("yaml")
            yaml.safe_load(text)
    except Exception as exc:
        logger.debug("file does not parse as its type: %s", type(exc).__name__)
        return f"{type(exc).__name__}: {exc}"
    return None


@pytest.mark.parametrize("gate", sorted(CANARIES))
def test_canary_fixture_has_teeth(gate: str) -> None:
    """The fixtures themselves: a seed that equals its control, or a BOM copy that drifted, tests nothing."""
    base = CANARY_DIR / gate
    violation = {p.relative_to(base / "violation"): p.read_bytes() for p in _files(base / "violation")}
    clean = {p.relative_to(base / "clean"): p.read_bytes() for p in _files(base / "clean")}
    bom = {p.relative_to(base / "bom"): p.read_bytes() for p in _files(base / "bom")}
    assert violation, f"{gate}: violation/ is empty"
    assert clean, f"{gate}: clean/ is empty"
    assert violation != clean, f"{gate}: the seeded violation is byte-identical to its clean control"
    assert bom == {k: BOM + v for k, v in violation.items()}, f"{gate}: bom/ is not exactly the violation with a BOM prefix"
    for variant, files in (("violation", violation), ("clean", clean)):
        for rel, data in files.items():
            why = _parse_error(rel, data)
            assert why is None, f"{gate}/{variant}/{rel} must parse, or the gate would fail it for the wrong reason: {why}"
    if CANARIES[gate].parses:
        broken = _files(base / "unparsable")
        assert broken, f"{gate}: unparsable/ is empty"
        for p in broken:
            assert _parse_error(p, p.read_bytes()) is not None, f"{gate}: {p.name} parses, so it cannot test the unparsed path"


# Audit 2026-11-07 44-F1: a canary that pins only the default token 'seed' proves one detection branch. The ceiling may only go down; give a canary
# `tokens=` (one per seeded shape) and lower it. Plan for the rest: gates whose own unit tests already pin each branch go last.
_DEFAULT_TOKEN_ONLY_CEILING = 55

# Audit 2026-11-07 44-F7: tokens that any message about the gate or the file carries. Frozen; may only shrink.
_GENERIC_TOKENS = frozenset({"seed", "main", "alice", "limit", "WHERE", "OSError"})
_WEAK_TOKENS_FROZEN: frozenset[str] = frozenset()


def _weak_tokens() -> set[str]:
    weak = set()
    for gate, c in CANARIES.items():
        for tok in {c.token, *c.tokens} - {"seed"}:
            if tok in _GENERIC_TOKENS or tok in gate:
                weak.add(f"{gate}:{tok}")
    return weak


def test_default_token_only_canaries_never_grow() -> None:
    thin = sorted(g for g, c in CANARIES.items() if c.token == "seed" and not c.tokens)
    assert len(thin) <= _DEFAULT_TOKEN_ONLY_CEILING, f"a new canary pins only the default token 'seed'; add tokens= (one per detection branch): {thin}"
    assert len(thin) >= _DEFAULT_TOKEN_ONLY_CEILING - 3, f"lower _DEFAULT_TOKEN_ONLY_CEILING to {len(thin)} so the ratchet keeps its grip"


def test_weak_canary_tokens_never_grow() -> None:
    weak = _weak_tokens()
    assert weak <= _WEAK_TOKENS_FROZEN, f"a canary token any message echoes proves nothing: {sorted(weak - _WEAK_TOKENS_FROZEN)}"


def _scanning_modules() -> set[str]:
    names = {g.name for g in registry.GATES if g.kind in ("gate", "library")}
    return names | {m for m in registry.unregistered_modules() if not m.startswith("_")}


def test_every_scanning_gate_has_a_canary_or_a_reasoned_exemption() -> None:
    modules = _scanning_modules()
    missing = sorted(modules - set(CANARIES) - set(EXEMPT))
    assert not missing, f"Add tests/canary/<gate>/ and a CANARIES entry (or an EXEMPT reason) for: {missing}"
    both = sorted(set(CANARIES) & set(EXEMPT))
    assert not both, f"Remove the EXEMPT entry for gates that have a canary: {both}"
    stale = sorted((set(CANARIES) | set(EXEMPT)) - modules)
    assert not stale, f"Remove canary/exemption entries naming no registered module: {stale}"
    short = sorted(k for k, v in EXEMPT.items() if len(v) < 20)
    assert not short, f"Give every exemption a reason: {short}"
    dirs = {p.name for p in CANARY_DIR.iterdir() if p.is_dir()}
    assert dirs == set(CANARIES), f"Canary dirs and CANARIES disagree: {sorted(dirs ^ set(CANARIES))}"


def test_canary_exemptions_are_valid_and_still_needed() -> None:
    """35-F9: an EXEMPT gate must be a registry gate (not a typo), and none may have grown a corpus scan since it was exempted: a module that now calls ``scan_python``/``iter_files`` gets
    a canary instead. Gates that scan a corpus AND cannot be seeded as files must say so in ``CORPUS_SCAN_EXEMPT_REASONS`` below, so the choice is recorded and reviewed.
    """
    from py_ci_shared._core.gate_contract import scans_a_corpus

    names = {g.name for g in registry.GATES}
    unknown = sorted(k for k in EXEMPT if k not in names and k not in registry.discover_modules())
    assert not unknown, f"EXEMPT keys naming no module: {unknown}"
    scanning = sorted(
        k
        for k in EXEMPT
        if (_SRC_DIR / f"{k}.py").is_file() and scans_a_corpus((_SRC_DIR / f"{k}.py").read_text(encoding="utf-8-sig")) and k not in CORPUS_SCAN_EXEMPT_REASONS
    )
    assert not scanning, f"EXEMPT gates that now scan a corpus; give them a canary or record why not in CORPUS_SCAN_EXEMPT_REASONS: {scanning}"
    stale = sorted(k for k in CORPUS_SCAN_EXEMPT_REASONS if k not in EXEMPT)
    assert not stale, f"CORPUS_SCAN_EXEMPT_REASONS entries no longer in EXEMPT: {stale}"
    assert all(len(v) >= 20 for v in CORPUS_SCAN_EXEMPT_REASONS.values())


CORPUS_SCAN_EXEMPT_REASONS: dict[str, str] = {
    "config_getattr_default_parity": "its input is live pydantic schema classes; the corpus helper only locates the source files that read them",
    "docs_inventory_parity": "compares precomputed problem lists from pyproject and docs; the corpus helper only lists files, there is no per-file verdict to seed",
    "edge_function_hygiene": "checks a Supabase edge-function directory layout (TypeScript), which a .canary Python fixture cannot model",
    "function_complexity": "a compatibility API over complexity_ratchet, whose canary seeds the same measurement",
    "inert_patch_targets": "works from a precomputed module-facts index; the corpus helper builds that index and is exercised by its own tests",
    "prompt_field_parity": "compares prompt templates with schema fields; the corpus helper only locates the template files",
    "repo_hygiene": "tracked-file and layout rules that need a repository work tree; covered by tests/test_repo_hygiene.py on real repositories",
    "stale_comment_age": "ages comments through blame, which needs commit history a fixture directory does not have",
}
_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
