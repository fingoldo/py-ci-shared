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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pytest

from py_ci_shared import registry

CANARY_DIR = Path(__file__).resolve().parent / "canary"
SUFFIX = ".canary"
BOM = b"\xef\xbb\xbf"


def _py(d: Path, pattern: str = "*.py") -> list[Path]:
    return sorted(d.rglob(pattern))


def _gate(name: str):
    import importlib

    return importlib.import_module(f"py_ci_shared.{name}")


@dataclass(frozen=True)
class Canary:
    """How to run one gate over a materialized canary directory ``d``."""

    gate: str
    run: Callable[[Path], object]
    token: str = "seed"  # must appear in the failure the violation (and the BOM variant) produces
    parses: bool = True  # False: the gate reads text by regex, so there is no "unparsable" input to feed it
    empty: bool = True  # False: the gate's contract has no floor on this input (documented per entry)
    xfail: dict = field(default_factory=dict)  # case -> gate-defect reason (strict)


def _baseline(d: Path) -> Path:
    return d / "baseline.json"


def _fatal(marker: str) -> bool:
    return marker.startswith("fatal_failed")


CANARIES: dict[str, Canary] = {
    c.gate: c
    for c in [
        Canary("import_cycles", lambda d: _gate("import_cycles").assert_no_import_cycles(d / "seedpkg", use_git=False), token="seedpkg"),
        Canary(
            "module_cache_thread_safety",
            lambda d: _gate("module_cache_thread_safety").assert_thread_safe_module_caches(d, use_git=False),
            token="_SEED_CACHE",
        ),
        Canary(
            "local_copy_report",
            lambda d: _gate("local_copy_report").assert_local_copies_do_not_grow(d, baseline_path=None, central=["import_cycles"], use_git=False),
            token="import_cycles",
        ),
        Canary(
            "hardcoded_token_ceilings",
            lambda d: _gate("hardcoded_token_ceilings").assert_no_hardcoded_token_ceilings(d, use_git=False),
            token="max_tokens",
        ),
        Canary("lf_file_writes", lambda d: _gate("lf_file_writes").assert_no_crlf_writes(d, use_git=False), token="seed.sh"),
        Canary("machine_specific_paths", lambda d: _gate("machine_specific_paths").assert_no_machine_specific_paths(d, use_git=False), token="alice"),
        Canary("swallowed_exceptions", lambda d: _gate("swallowed_exceptions").assert_no_swallowed_exceptions(d, use_git=False), token="OSError"),
        Canary("alembic_concurrently", lambda d: _gate("alembic_concurrently").assert_concurrently_is_in_autocommit_blocks(d)),
        Canary(
            "dataclass_case_completeness", lambda d: _gate("dataclass_case_completeness").assert_every_dataclass_has_a_case(_py(d), ()), token="SeedVerdict"
        ),
        Canary(
            "db_transaction_completeness",
            lambda d: _gate("db_transaction_completeness").assert_no_new_incomplete_transaction(_py(d), d, _baseline(d), refresh=False),
            token="seed_load",
        ),
        Canary("discarded_model_copy", lambda d: _gate("discarded_model_copy").assert_no_discarded_model_copy(_py(d), d, {}), token="seed_copy"),
        Canary("drifted_duplicate_functions", lambda d: _gate("drifted_duplicate_functions").assert_no_drifted_duplicate_functions([d]), token="seed_fn"),
        Canary(
            "env_flag_parsing",
            lambda d: _gate("env_flag_parsing").assert_env_flags_use_one_parser(_py(d), d, ("SEED_",), {}),
            token="SEED_FLAG",
        ),
        Canary("epsilon_padded_denominators", lambda d: _gate("epsilon_padded_denominators").assert_no_epsilon_padded_power_denominators([d])),
        Canary(
            "fail_open_handlers",
            lambda d: _gate("fail_open_handlers").assert_no_new_fail_open_handlers(_py(d), d, _baseline(d), refresh=False),
            token="seed_check",
        ),
        Canary("gpu_timing_sync", lambda d: _gate("gpu_timing_sync").assert_no_unsynchronized_gpu_timings(_py(d), d), token="seed_measure"),
        Canary("hash_fed_by_array_copy", lambda d: _gate("hash_fed_by_array_copy").assert_no_hash_fed_by_array_copy([d])),
        Canary("identity_comparisons", lambda d: _gate("identity_comparisons").assert_no_identity_comparisons(_py(d), root=d), token="SEED_SQL"),
        Canary(
            "latched_availability_flags", lambda d: _gate("latched_availability_flags").assert_no_latched_availability_flags([d]), token="_SEED_GPU_AVAILABLE"
        ),
        Canary("module_reload_safety", lambda d: _gate("module_reload_safety").assert_no_reloads_in_code([d], d)),
        Canary("naive_utcnow", lambda d: _gate("naive_utcnow").assert_no_naive_utcnow(d, use_git=False), token="utcnow"),
        Canary("optional_truthiness", lambda d: _gate("optional_truthiness").assert_optionals_test_for_none(files=_py(d), repo_root=d), token="limit"),
        Canary(
            "readme_env_var_parity",
            lambda d: _gate("readme_env_var_parity").assert_readme_documents_every_env_var(_py(d), d / "README.md"),
            token="SEED_UNDOCUMENTED_VAR",
        ),
        Canary(
            "resource_release_paths",
            lambda d: _gate("resource_release_paths").assert_released_on_every_path(
                files=_py(d), constructors=("create_async_engine",), release="dispose", repo_root=d
            ),
        ),
        Canary("runtime_registry_mutation", lambda d: _gate("runtime_registry_mutation").assert_writes_have_replay(_py(d), d, {}), token="SEED_REGISTRY"),
        Canary("save_failure_markers", lambda d: _gate("save_failure_markers").assert_markers_are_fatal(d, _fatal), token="seed_failed"),
        Canary("source_text_claims", lambda d: _gate("source_text_claims").assert_no_new_source_text_claims(_py(d), d), token="test_seed"),
        Canary(
            "spec_bound_doubles",
            lambda d: _gate("spec_bound_doubles").assert_doubles_are_spec_bound(
                files=_py(d), entry_points=("claim_lock",), name_hints=("session",), repo_root=d
            ),
        ),
        Canary("statement_compilation", lambda d: _gate("statement_compilation").assert_no_mocked_statement_constructors(_py(d), d)),
        Canary("survivorship_scoring", lambda d: _gate("survivorship_scoring").assert_no_survivorship_scoring(_py(d), d, {}), token="seed_score"),
        Canary(
            "uncalled_functions",
            lambda d: _gate("uncalled_functions").assert_no_new_uncalled_function(_py(d), d, _baseline(d), refresh=False),
            token="seed_unused",
        ),
        Canary("unread_init_params", lambda d: _gate("unread_init_params").assert_no_unread_init_params(_py(d), d), token="seed_alpha"),
        Canary(
            "vacuous_loop_assertions",
            lambda d: _gate("vacuous_loop_assertions").assert_no_new_floorless_loop(_py(d), d, _baseline(d)),
            token="test_seed",
        ),
        Canary("value_bearing_asserts", lambda d: _gate("value_bearing_asserts").assert_no_value_bearing_asserts(d, use_git=False), token="x > 0"),
        Canary(
            "conceded_defect_pins",
            lambda d: _assert_empty(_gate("conceded_defect_pins").find_conceded_defect_pins(_py(d), d)),
            token="test_seed_mean",
            empty=False,  # a library find_* with no assert: the caller's test owns the floor
        ),
        Canary(
            "import_side_effects",
            lambda d: _gate("import_side_effects").assert_no_new_import_time_env_mutations(d, _baseline(d), refresh=False),
            token="SEED_X",
        ),
        Canary(
            "private_imports",
            lambda d: _gate("private_imports").assert_no_private_cross_package_imports(d / "pkg", "pkg", d, use_git=False),
            token="pkg.metrics._core",
        ),
        Canary(
            "config_call_site_parity",
            lambda d: _gate("config_call_site_parity").assert_no_divergent_cfg_get_call_site_defaults(d, _py(d)),
            token="max_results",
        ),
        Canary(
            "llm_call_archive_gate",
            lambda d: _gate("llm_call_archive_gate").assert_every_llm_call_is_archived(
                d, ["src"], provider_classes=["ClaudeProvider"], wrapping_factories=["src/factory.py"]
            ),
        ),
        Canary("sqlalchemy_text_binds", lambda d: _gate("sqlalchemy_text_binds").assert_no_colon_cast_binds(d, ("pkg",))),
        Canary("meta_private_imports", lambda d: _gate("meta_private_imports").assert_no_private_meta_imports(d, ("seedpkg",)), token="_seed_helper"),
        Canary("fail_message_quality", lambda d: _gate("fail_message_quality").assert_fail_messages_actionable(d), token="seed value"),
        Canary("pytest_markers", lambda d: _gate("pytest_markers").assert_markers_registered(d, tests_dir=d / "tests"), token="seedunregistered"),
        Canary(
            "unresolved_imports",
            lambda d: _gate("unresolved_imports").assert_all_from_imports_resolve([d], [d], resolvable_prefixes=("seedpkg",)),
            token="seed_removed",
        ),
        Canary(
            "function_length",
            lambda d: _gate("function_length").assert_functions_do_not_grow(_py(d), d, _baseline(d), limit=5, min_functions=1, refresh=False),
            token="seed_long",
        ),
        Canary(
            "loc_budget",
            lambda d: _gate("loc_budget").assert_no_new_oversized_file(_py(d), d, _baseline(d), limit=5, refresh=False),
            parses=False,  # counts lines; a syntax error is not its subject
        ),
        Canary(
            "audit_wave_filenames",
            lambda d: _gate("audit_wave_filenames").assert_no_new_audit_wave_filenames(d),
            token="test_wave7_seed",
            parses=False,  # judges file NAMES, never contents
        ),
        Canary(
            "ci_workflow_gate",
            lambda d: _gate("ci_workflow_gate").assert_continue_on_error_is_reviewed(d / "seed.yml", set()),
            token="seed step",
            parses=False,  # a line scanner by design; GitHub itself rejects a workflow that is not YAML
        ),
        Canary("ci_workflow_timeout_gate", lambda d: _gate("ci_workflow_timeout_gate").assert_all_jobs_have_timeout(d / "seed.yml"), token="seed_job"),
        Canary(
            "git_dependency_pins",
            lambda d: _gate("git_dependency_pins").assert_all_git_dependencies_pinned(d / "pyproject.toml"),
            token="main",  # the finding is the unpinned ref
        ),
        Canary("entry_points_resolvable", lambda d: _gate("entry_points_resolvable").assert_all_entry_points_resolvable(d / "pyproject.toml"), token="seed"),
        Canary(
            "sql_function_privileges",
            lambda d: _gate("sql_function_privileges").assert_definer_functions_are_locked_down(d),
            token="seed_fn",
            parses=False,  # regex over SQL text; there is no parser to defeat
        ),
        Canary(
            "phantom_markdown_links",
            lambda d: _gate("phantom_markdown_links").assert_no_phantom_markdown_links(_py(d, "*.md"), d),
            token="seed_missing",
            parses=False,  # Markdown has no invalid syntax
        ),
        Canary("numba_seed_range", lambda d: _gate("numba_seed_range").assert_numba_seeds_fit_int64(d, use_git=False)),
        Canary("plotly_annotation_loop", lambda d: _gate("plotly_annotation_loop").assert_no_plotly_annotation_loops(d, use_git=False)),
        Canary(
            "polars_null_equality",
            # advisory by default (warns, never fails); the teeth are what it reports once a repo makes it blocking
            lambda d: _gate("polars_null_equality").assert_polars_null_equality(d, advisory=False, use_git=False),
        ),
        Canary("reiterated_iterable_params", lambda d: _gate("reiterated_iterable_params").assert_no_reiterated_iterable_params(d, use_git=False)),
        Canary("rollback_then_continue", lambda d: _gate("rollback_then_continue").assert_no_rollback_then_continue(d, use_git=False)),
        Canary("stdlib_json_ban", lambda d: _gate("stdlib_json_ban").assert_no_stdlib_json(d, use_git=False), token="seed"),
        Canary("atomic_write_staging", lambda d: _gate("atomic_write_staging").assert_atomic_write_staging(d, use_git=False)),
        Canary("clock_day_boundary", lambda d: _gate("clock_day_boundary").assert_no_clock_day_boundary(d, use_git=False)),
        Canary("hash_key_determinism", lambda d: _gate("hash_key_determinism").assert_hash_keys_are_deterministic(d, use_git=False)),
        Canary("no_xfail_to_defer", lambda d: _gate("no_xfail_to_defer").assert_no_xfail_to_defer(d / "tests", repo_root=d, use_git=False)),
        Canary("sentinel_or_fallback", lambda d: _gate("sentinel_or_fallback").assert_no_sentinel_or_fallback(d, use_git=False)),
        Canary("printed_advice", lambda d: _gate("printed_advice").assert_printed_advice_registered(sorted(d.rglob("*.py")), d, {})),
        Canary("stale_source_citations", lambda d: _gate("stale_source_citations").assert_no_stale_source_citations(d, use_git=False)),
        Canary("pickle_state_completeness", lambda d: _gate("pickle_state_completeness").assert_no_pickle_state_gaps(d, use_git=False), token="_seed_cache"),
    ]
}

# Registered gates and libraries that are not corpus scanners, or whose subject cannot be seeded as a file corpus.
EXEMPT: dict[str, str] = {
    "arb_checks": "reads Flutter .arb JSON catalogues keyed by locale; its subject is catalogue parity, covered by test_arb_checks.py",
    "audit_disposition_parity": "cross-references audit prose against the repo tree; no seedable code shape",
    "audit_path_references": "cross-references code literals against audit round directories; subject is repo layout",
    "audit_round_format": "Markdown tracker/round bookkeeping, not a code scanner",
    "baseline_hygiene": "audits a baseline file's own notes; subject is a JSON document, not a corpus",
    "baseline_ratchet": "library of baseline helpers, no scanning entry",
    "changelog_promise_parity": "regex over a CHANGELOG with caller-supplied trigger patterns",
    "checkout_resolution": "imports modules / runs pytest in a copy; runtime check, not a scanner",
    "checkpoint_isolation": "single path predicate, no corpus",
    "ci_test_dir_reachability": "compares a tests/ tree against workflow commands; subject is repo layout",
    "ci_workflow_paths": "checks that workflow-referenced paths exist in a repo; subject is repo layout",
    "code_audit_meta": "wraps pyutilz code_audit checks behind a baseline; the checks live in pyutilz",
    "coverage_config_parity": "compares [tool.coverage] config against workflow coverage commands; CI configuration",
    "pytest_addopts_path_runs": "compares hook/workflow pytest commands against addopts; CI configuration",
    "config_getattr_default_parity": "needs live pydantic schema classes as input, not a file corpus",
    "content_hash_version_bump_gate": "hashes files against a version baseline; no violation shape in code",
    "dart_scanners": "Dart/Flutter source scanners driven by a caller-supplied reader; covered by test_dart_scanners.py",
    "deferred_drift": "counts DEFERRED list entries against a baseline; bookkeeping",
    "deletion_gates": "library of git-diff deletion checks; needs git history",
    "disposition_test_references": "cross-references audit dispositions against test files; subject is repo layout",
    "doc_identifier_parity": "cross-references doc identifiers against a whole repo; subject is repo layout",
    "docs_inventory_parity": "takes a precomputed problem list; the find_* helpers compare pyproject and docs",
    "edge_function_hygiene": "Supabase edge-function directory layout checks (TypeScript)",
    "effect_assertion_parity": "maps production modules to their tests through an import map; subject is repo layout",
    "env_example_round_trip": "loads a .env example into a live settings class; runtime check",
    "gate_config_honesty": "compares pre-commit and workflow commands against config; CI configuration",
    "gate_integrity": "compares pre-commit, workflows and pyproject; CI configuration",
    "gate_population_canary": "itself a population/canary gate over meta tests",
    "git_changed_lines": "library for git diff line ranges; needs git history",
    "guard_population": "runs shell guard scripts; runtime check",
    "hook_hygiene": "audits git hook scripts and their wiring; CI configuration",
    "ignore_ratchet": "takes precomputed counts; no corpus",
    "import_layering": "rules are caller-supplied layer maps; covered by test_import_layering.py",
    "index_coverage": "compares SQL index definitions against a live catalogue",
    "inert_patch_targets": "needs a precomputed module-facts index; library entry",
    "marker_runner_coverage": "compares marked tests against runner commands; subject is CI wiring",
    "mutation_teeth": "runs mutants against tests; runtime check",
    "nondiscriminating_shapes": "library helpers for test-shape predicates",
    "package_doctests": "runs doctests of an importable package; runtime check",
    "phantom_code_references": "cross-references prose against a declared-name set; subject is repo layout",
    "prompt_field_parity": "library comparing prompt templates against schema fields",
    "prose_numeric_claims": "takes caller-built claims; no corpus",
    "pydantic_field_bounds": "needs live pydantic model classes as input",
    "resource_leak_guard": "a pytest plugin checking live processes, threads, sockets and env at teardown; covered by test_resource_leak_guard.py",
    "repo_hygiene": "tracked-file and layout hygiene; needs a git work tree",
    "source_text_ban": "library of banned-substring helpers configured by the caller",
    "sql_verifier_coverage": "compares SQL constants against a verifier module; subject is repo layout",
    "sql_verify": "runs statements against a live database connection",
    "stale_comment_age": "ages comments through git blame; needs git history",
    "test_partition_reachability": "compares runner scripts, tags and playwright configs; CI configuration",
    "timezone_honest": "delegates the scan to ruff (DTZ rules) in a subprocess; covered by test_timezone_honest.py",
    "tool_versions": "library reading installed tool versions",
    "tracker_summary_parity": "Markdown tracker bookkeeping, not a code scanner",
    "version_consistency": "compares version strings across manifests; no violation shape in code",
    "version_tag_currency": "compares a manifest version against git tags; needs git history",
}


def _assert_empty(found) -> None:
    """Library scanners without an assert: any finding is a failure."""
    if found:
        raise AssertionError("\n".join(str(f) for f in found))


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
    except pytest.skip.Exception:
        return None
    except (Exception, pytest.fail.Exception) as exc:
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
    elif case == "bom":
        assert failure is not None, f"{gate} passed a BOM-prefixed copy of its violation"
        assert c.token in failure, f"{gate} failed on the BOM variant without naming the seed {c.token!r}:\n{failure}"
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
        elif name.endswith(".toml"):
            from py_ci_shared._toml_compat import tomllib

            tomllib.loads(text)
        elif name.endswith((".yml", ".yaml")):
            yaml = pytest.importorskip("yaml")
            yaml.safe_load(text)
    except Exception as exc:
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
