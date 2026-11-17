"""41-T1: the gates must not die with RecursionError on one very long operator chain.

A generated module can hold an expression with 30000 operands (``1 + 1 + ... + 1``). Each gate below is run through its own
canary entry over its clean corpus plus two copies of that module; the chain holds no defect, so the correct result is a pass.
A recursive walker raised RecursionError on this input before the walkers were made iterative.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _gate_teeth_canaries_more import CANARIES
from test_gate_teeth import _materialize, _outcome

GATES_41_T1 = (
    "alembic_concurrently",
    "connection_liveness_kwargs",
    "connect_error_echo",
    "constant_fallback_cache_key",
    "env_write_restore",
    "hardcoded_seed_in_library",
    "hardcoded_token_ceilings",
    "identity_comparisons",
    "lf_file_writes",
    "machine_specific_paths",
    "numba_seed_range",
    "order_losing_filters",
    "printed_advice",
    "psycopg2_param_arity",
    "predictable_tmp_path",
    "reiterated_iterable_params",
    "rollback_then_continue",
    "runtime_registry_mutation",
    "secret_assertion_operands",
    "sql_verifier_coverage",
    "statement_columns_exist_in_ddl",
    "statement_real_engine_coverage",
    "unread_function_params",
)

CHAIN_OPERANDS = 30000


@pytest.mark.parametrize("gate", GATES_41_T1)
def test_a_30000_operand_chain_is_a_pass_not_a_recursion_error(gate: str, tmp_path: Path) -> None:
    chain = "x = " + " + ".join(["1"] * CHAIN_OPERANDS) + "\n"
    d = _materialize(gate, "clean", tmp_path)
    (d / "deep_chain.py").write_text(chain, encoding="utf-8")
    (d / "test_deep_chain.py").write_text(chain, encoding="utf-8")
    failure = _outcome(lambda: CANARIES[gate].run(d))
    if failure is not None and "during ast construction" in failure:
        pytest.skip("this interpreter's parser hits its recursion limit on a 30000-operand chain at this stack depth (41-T1)")
    assert failure is None, f"{gate} on a {CHAIN_OPERANDS}-operand chain: {failure}"
