"""52-T4 / 52-S15: the helpers that were copied, with identical bodies, into several scanners now live once in ``_core``.

Each case names the shared function in ``py_ci_shared._core.shared_helpers`` and every scanner that imported it under the old
private name. The identity check fails while a scanner still carries its own copy (or while the shared module is missing)."""

from __future__ import annotations

import importlib

import pytest

CORE = "py_ci_shared._core.shared_helpers"

#: old private name -> (function in CORE, scanners that must bind the very same object under the old name)
CASES = {
    "_final": ("final_name", ["state_before_work", "verified_then_reopened"]),
    "_head": ("head_name", ["cross_package_private_names", "patch_target_read_at_call"]),
    "_is_test_path": ("is_test_path", ["optional_collaborator_wired", "systemexit_in_library"]),
    "_is_type_checking": ("is_type_checking_test", ["import_cycles", "unexecuted_function_bodies"]),
    "_line_of": ("line_of_index", ["edge_function_hygiene"]),
    "_module_statements": ("module_statements", ["id_keyed_cache_validates_identity", "module_cache_thread_safety"]),
    "_norm": ("normalize_distribution_name", ["sibling_floor_skew", "uv_lock_parity"]),
    "_tokens": ("shell_tokens", ["ci_install_covers_conftest", "coverage_config_parity"]),
    "_toml_str": ("toml_quoted", ["_consumers", "_scaffold"]),
}


@pytest.mark.parametrize("local", sorted(CASES))
def test_a_duplicated_helper_is_one_function_in_core(local: str):
    core_name, scanners = CASES[local]
    shared = getattr(importlib.import_module(CORE), core_name)
    for scanner in scanners:
        module = importlib.import_module(f"py_ci_shared.{scanner}")
        assert getattr(module, local) is shared, f"{scanner}.{local} is still its own copy"
