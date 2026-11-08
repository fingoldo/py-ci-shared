"""Canary table of tests/test_gate_teeth.py, part 2: the CANARIES.update blocks (text ratchets, CI configuration, repo layout)."""

from __future__ import annotations

from pathlib import Path

from _gate_teeth_canaries import CANARIES, Canary, _gate, _py

# Text-ratchet gates (opt-in): each reads a file's text line by line, so there is no syntax to break except where it parses a file.
# Their corpora hold no floor: an empty corpus is a clean one (empty=False); dup_baseline_percent_key reads one named file, so it fails without it.
CANARIES.update(
    {
        "skip_as_pass": Canary(
            "skip_as_pass",
            lambda d: _gate("skip_as_pass").assert_no_new_skip_as_pass(d),
            parses=False,
            empty=False,
            tokens=("seed_docker.sh", "seed_probe.py"),  # the shell half and the Python half of the gate
        ),
        "wallclock_test_waits": Canary(
            "wallclock_test_waits",
            lambda d: _gate("wallclock_test_waits").assert_no_new_wallclock_test_waits(d),
            parses=False,
            empty=False,
            tokens=("test_seed_poll.py", "test_seed_async.py"),  # time.sleep and the async sleep family
        ),
        "guard_without_fixture": Canary(
            "guard_without_fixture", lambda d: _gate("guard_without_fixture").assert_every_guard_named_by_a_test(d), parses=False, empty=False
        ),
        "git_child_env": Canary("git_child_env", lambda d: _gate("git_child_env").assert_no_new_unscrubbed_git_spawns(d), empty=False),
        "dup_baseline_percent_key": Canary(
            "dup_baseline_percent_key",
            lambda d: _gate("dup_baseline_percent_key").assert_no_percent_only_duplicate_entries(d, duplicate_baseline_path="seed_duplicate_bodies.json"),
            parses=False,
        ),
    }
)


# CI-configuration gates: workflows, hooks and pyproject are files too, so they get canaries (audit 2026-10-03 WF-11).
# Regex/line-level readers have no unparsable input (parses=False).
CANARIES.update(
    {
        c.gate: c
        for c in [
            Canary(
                "ci_workflow_paths",
                lambda d: _gate("ci_workflow_paths").assert_workflow_paths_exist(d / ".github" / "workflows", d, require_permissions=True, require_sha_pins=True),
                tokens=(  # a missing script, a missing working-directory, a mutable action tag, a mutable docker tag, and a workflow with no permissions
                    "runs `tools/seed_gone.py`, which does not exist",
                    "working-directory `seed_nodir` does not exist",
                    "`uses: someorg/seed-action@v1` follows a mutable tag",
                    "`uses: docker://alpine:3.19` names a docker image by a mutable tag",
                    "seed_noperm.yml: no top-level `permissions:` block",
                ),
                parses=False,
            ),
            Canary(
                "ci_test_dir_reachability",
                lambda d: _gate("ci_test_dir_reachability").assert_every_test_subdir_reachable(d, d / ".github" / "workflows"),
                parses=False,
            ),
            Canary(
                "coverage_config_parity",
                lambda d: _gate("coverage_config_parity").assert_coverage_config_parity(d),
                tokens=("[narrow-run-inherits-fail-under]", "[numba-blind-coverage]"),
            ),
            Canary(
                "gate_config_honesty",
                lambda d: _gate("gate_config_honesty").assert_gates_honest(
                    d / ".pre-commit-config.yaml", sorted((d / ".github" / "workflows").glob("*.yml")), d / "pyproject.toml"
                ),
                tokens=(  # a tool run without its config (hook and workflow), and each way of always exiting 0
                    "seed-bandit: runs bandit without -c/--configfile",
                    "seed_wf.yml::seed_job::Seed security: runs bandit without",
                    "always exits 0 (--exit-zero)",
                    "always exits 0 (|| true)",
                    "always exits 0 (set +e)",
                ),
                parses=False,
            ),
            Canary(
                "pytest_addopts_path_runs",
                lambda d: _gate("pytest_addopts_path_runs").assert_path_runs_select_tests(
                    d, repo_root=d, workflows=sorted((d / ".github" / "workflows").glob("*.yml"))
                ),
                tokens=(  # a bare path run, one that repeats the deselecting -m, one that adds a -k
                    "test_seed_db.py: `tests/test_seed_db.py` reaches",
                    'test_seed_db.py -m "not integration": `tests/test_seed_db.py` reaches',
                    'test_seed_db.py -k "nothing_matches": `tests/test_seed_db.py` reaches',
                ),
            ),
            Canary(
                "marker_runner_coverage",
                lambda d: _gate("marker_runner_coverage").assert_every_marked_test_is_selected(
                    d / "tests",
                    d,
                    marker="integration",
                    commands=[("ci::integration", "python -m pytest tests -m 'integration' -k seed_db_run")],
                    addopts="-m 'not integration'",
                ),
            ),
            Canary(
                "gate_integrity",
                lambda d: _gate("gate_integrity").assert_narrowings_declared(d / ".pre-commit-config.yaml", None, {}),
                tokens=("pre-commit::<top-level>::exclude=^vendor/", "pre-commit::seed-ruff::--ignore=C901", "pre-commit::seed-scoped::files=^src/", "pre-commit::seed-bandit::-s=B101", "pre-commit::seed-pytest::-k=fast", "pre-commit::seed-args::--select=E501"),
            ),
        ]
    }
)


def _effect_assertion_canary(d: Path) -> None:
    mod = _gate("effect_assertion_parity")
    mod.assert_effects_are_asserted(d, mod.build_import_map(d, package_name="seedpkg"))


def _phantom_code_references_canary(d: Path) -> None:
    mod, files = _gate("phantom_code_references"), _py(d)
    mod.assert_no_phantom_code_references(files, d, mod.python_declarations([f for f in files if "broken" not in f.name]))


# The rest of the WF-11 list (audit 2026-10-03): repo-layout cross-references, hooks, partitions and the ruff-backed
# timezone check all read files, so they are seeded as files too. Markdown and shell readers have no unparsable input.
CANARIES.update(
    {
        c.gate: c
        for c in [
            Canary("hook_hygiene", lambda d: _gate("hook_hygiene").assert_hooks_are_honest(d / ".githooks"), token="git add -u", parses=False),
            Canary(
                "test_partition_reachability",
                lambda d: _gate("test_partition_reachability").assert_partitions_reachable(
                    runner_texts=[d / ".github" / "workflows"], declared_tags=d / "dart_test.yaml"
                ),
            ),
            Canary(
                "doc_identifier_parity",
                lambda d: _gate("doc_identifier_parity").assert_doc_identifiers_exist(d, doc_files=sorted(d.glob("*.md")), corpus_files=_py(d)),
                parses=False,
            ),
            Canary("phantom_code_references", _phantom_code_references_canary, token="SeedRunner"),
            Canary("effect_assertion_parity", _effect_assertion_canary, token="seedpkg/store.py"),
            Canary(
                "sql_verifier_coverage",
                lambda d: _gate("sql_verifier_coverage").assert_verifier_covers_statements(d, d / "tools" / "verify_sql.py", exclude_top_dirs=["tools"]),
                token="SEED_DELETE",
            ),
            Canary(
                "audit_path_references",
                lambda d: _gate("audit_path_references").assert_no_open_round_paths(_py(d / "src"), [d / "audits"], root=d),
            ),
            Canary(
                "audit_disposition_parity",
                lambda d: _gate("audit_disposition_parity").assert_dispositions_name_real_artefacts(d / "audits" / "round", d),
                parses=False,
            ),
            Canary(
                "disposition_test_references",
                lambda d: _gate("disposition_test_references").assert_disposition_tests_exist(sorted((d / "audits").rglob("*.md")), d),
            ),
            Canary("timezone_honest", lambda d: _gate("timezone_honest").assert_timezone_honest(d), token="seed_job.py"),
            Canary(
                "import_layering",
                lambda d: _gate("import_layering").assert_layering(
                    d, [_gate("import_layering").LayerRule("seedpkg/core/*", ["seedpkg/product/*"], reason="core is product-neutral")]
                ),
                token="seed_core.py",
            ),
        ]
    }
)
