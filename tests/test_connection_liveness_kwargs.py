"""connection_liveness_kwargs: every network DB connection opener sets TCP keepalives (a tunnel that drops silently hung a test 7,210 s)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import connection_liveness_kwargs as gate
from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.connection_liveness_kwargs import (
    KEEPALIVES,
    assert_every_connection_has_liveness_kwargs,
    find_connections_without_liveness_kwargs,
)

BOM = b"\xef\xbb\xbf"
NL = chr(10)
LITERAL = "keepalives=1, keepalives_idle=60, keepalives_interval=10, keepalives_count=3"
SHARED = 'KEEPALIVES = {"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 3}'


def _write(root: Path, rel: str, lines: list) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((NL.join(lines) + NL).encode("utf-8"))
    return path


def _find(tmp_path: Path, lines: list, **kwargs) -> list:
    _write(tmp_path, "mod.py", lines)
    return find_connections_without_liveness_kwargs(tmp_path, use_git=False, **kwargs)


def test_the_two_canary_openers_are_reported_by_name_and_line(tmp_path):
    """The incident: `psycopg2.connect(dsn)` and a pool built from a bare DSN, each missing all four keywords."""
    found = _find(
        tmp_path,
        [
            "import psycopg2",
            "from psycopg2.pool import ThreadedConnectionPool",
            "",
            "def a(dsn):",
            "    return psycopg2.connect(dsn)",
            "",
            "def b(dsn):",
            "    return ThreadedConnectionPool(1, 8, dsn)",
        ],
    )
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 5, gate.RULE), ("mod.py", 8, gate.RULE)]
    assert "psycopg2.connect(...) in a is missing keepalives, keepalives_idle, keepalives_interval, keepalives_count" in found[0].message
    assert "psycopg2.pool.ThreadedConnectionPool(...) in b" in found[1].message


def test_negative_control_the_same_openers_spreading_a_shared_mapping_are_clean(tmp_path):
    lines = [
        "import psycopg2",
        "from psycopg2.pool import ThreadedConnectionPool",
        SHARED,
        "def a(dsn):",
        "    return psycopg2.connect(dsn, **KEEPALIVES)",
        "def b(dsn):",
        "    return ThreadedConnectionPool(1, 8, dsn, **KEEPALIVES)",
    ]
    assert _find(tmp_path, lines) == []


@pytest.mark.parametrize(
    "opener_lines, qualified",
    [
        (["import psycopg2 as pg", "x = pg.connect(d)"], "psycopg2.connect"),
        (["from psycopg2 import connect", "x = connect(d)"], "psycopg2.connect"),
        (["from psycopg2 import pool", "x = pool.SimpleConnectionPool(1, 2, d)"], "psycopg2.pool.SimpleConnectionPool"),
        (["import psycopg2.pool", "x = psycopg2.pool.ThreadedConnectionPool(1, 2, d)"], "psycopg2.pool.ThreadedConnectionPool"),
        (["import psycopg", "x = psycopg.connect(d)"], "psycopg.connect"),
        (["import psycopg", "x = psycopg.AsyncConnection.connect(d)"], "psycopg.AsyncConnection.connect"),
        (["from psycopg_pool import ConnectionPool", "x = ConnectionPool(d)"], "psycopg_pool.ConnectionPool"),
        (["import psycopg2", "connect = psycopg2.connect", "x = connect(d)"], "psycopg2.connect"),
    ],
)
def test_every_spelling_of_an_opener_resolves(tmp_path, opener_lines, qualified):
    found = _find(tmp_path, opener_lines)
    assert len(found) == 1 and found[0].message.startswith(f"{qualified}(...)")


def test_a_call_that_is_not_an_opener_is_ignored(tmp_path):
    lines = [
        "import sqlite3, asyncpg, socket",
        "a = sqlite3.connect(p)",
        "b = socket.create_connection(h)",
        "def f(c):",
        "    return asyncpg.connect(c)",
        "c = obj.connect(d)",
    ]
    assert _find(tmp_path, lines) == []


def test_asyncpg_can_be_added_to_the_openers(tmp_path):
    found = _find(tmp_path, ["import asyncpg", "x = asyncpg.connect(d)"], openers=gate.DEFAULT_OPENERS | {"asyncpg.connect"})
    assert len(found) == 1


def test_literal_keywords_pass_and_each_missing_one_is_named(tmp_path):
    assert _find(tmp_path, ["import psycopg2", f"x = psycopg2.connect(d, {LITERAL})"]) == []
    found = _find(tmp_path, ["import psycopg2", "x = psycopg2.connect(d, keepalives=1, keepalives_idle=60, keepalives_count=3)"])
    assert len(found) == 1 and found[0].message.split("is missing ")[1].startswith("keepalives_interval.")


def test_keepalives_switched_off_is_not_a_fix(tmp_path):
    found = _find(tmp_path, ["import psycopg2", f"x = psycopg2.connect(d, {LITERAL.replace('keepalives=1', 'keepalives=0')})"])
    assert len(found) == 1 and "falsy value" in found[0].message
    off = _find(tmp_path, ["import psycopg2", SHARED.replace('"keepalives": 1', '"keepalives": 0'), "x = psycopg2.connect(d, **KEEPALIVES)"])
    assert len(off) == 1 and "falsy value" in off[0].message


@pytest.mark.parametrize(
    "dsn",
    [
        '"host=h dbname=d ' + LITERAL.replace(", ", " ") + '"',
        '"postgresql://u@h/d?keepalives=1&keepalives_idle=60&keepalives_interval=10&keepalives_count=3"',
        'f"host={h} keepalives=1 keepalives_idle=60 " + "keepalives_interval=10 keepalives_count=3"',
    ],
)
def test_a_dsn_literal_that_names_every_key_passes(tmp_path, dsn):
    assert _find(tmp_path, ["import psycopg2", f"x = psycopg2.connect({dsn})"]) == []


def test_a_dsn_constant_and_the_dsn_keyword_are_read(tmp_path):
    text = "host=h keepalives=1 keepalives_idle=60 keepalives_interval=10 keepalives_count=3"
    assert _find(tmp_path, ["import psycopg2", f'DSN = "{text}"', "x = psycopg2.connect(DSN)", f'y = psycopg2.connect(dsn="{text}")']) == []


def test_a_dsn_naming_only_some_keys_still_reports_the_rest(tmp_path):
    found = _find(tmp_path, ["import psycopg2", 'x = psycopg2.connect("host=h keepalives=1 keepalives_idle=60")'])
    assert len(found) == 1 and "is missing keepalives_interval, keepalives_count" in found[0].message


def test_a_dsn_with_keepalives_zero_is_reported(tmp_path):
    found = _find(tmp_path, ["import psycopg2", 'x = psycopg2.connect("host=h keepalives=0 keepalives_idle=60 keepalives_interval=10 keepalives_count=3")'])
    assert len(found) == 1 and "keepalives=0" in found[0].message


def test_an_unknown_splat_is_a_finding_even_beside_literal_keys(tmp_path):
    found = _find(tmp_path, ["import psycopg2", "def f(d, opts):", "    return psycopg2.connect(d, **opts)"])
    assert len(found) == 1 and "**opts is a mapping of unknown origin" in found[0].message


def test_an_unknown_splat_beside_all_literal_keys_is_still_a_finding(tmp_path):
    """The unknown mapping may carry keepalives=0 and override the literal, so the call cannot be vouched for."""
    found = _find(tmp_path, ["import psycopg2", "def f(d, opts):", f"    return psycopg2.connect(d, {LITERAL}, **opts)"])
    assert len(found) == 1 and "**opts" in found[0].message


def test_a_shared_name_with_no_definition_in_the_scan_is_a_finding(tmp_path):
    found = _find(tmp_path, ["import psycopg2", "def f(d):", "    return psycopg2.connect(d, **KEEPALIVES)"])
    assert len(found) == 1 and "no module-level literal definition" in found[0].message


def test_a_definition_missing_a_key_is_a_finding_that_names_the_file(tmp_path):
    short = 'KEEPALIVES = {"keepalives": 1, "keepalives_idle": 60}'
    found = _find(tmp_path, ["import psycopg2", short, "x = psycopg2.connect(d, **KEEPALIVES)"])
    assert len(found) == 1 and "as defined in mod.py lacks keepalives_interval, keepalives_count" in found[0].message


def test_the_mapping_is_found_in_the_module_it_is_imported_from(tmp_path):
    _write(tmp_path, "pkg/pg_keepalives.py", ["from __future__ import annotations", SHARED.replace("KEEPALIVES =", "KEEPALIVES: dict[str, int] =")])
    _write(
        tmp_path,
        "pkg/use.py",
        [
            "import psycopg2",
            "from pg_keepalives import KEEPALIVES",
            "x = psycopg2.connect(d, **KEEPALIVES)",
            "import pg_keepalives",
            "y = psycopg2.connect(d, **pg_keepalives.KEEPALIVES)",
        ],
    )
    assert find_connections_without_liveness_kwargs(tmp_path, use_git=False) == []


def test_an_import_does_not_borrow_a_same_named_mapping_from_an_unrelated_module(tmp_path):
    """verify_client_backoff_rule imported pg_keepalives while only an unrelated script defined KEEPALIVES."""
    _write(tmp_path, "other.py", [SHARED])
    _write(tmp_path, "use.py", ["import psycopg2", "from pg_keepalives import KEEPALIVES", "x = psycopg2.connect(d, **KEEPALIVES)"])
    found = find_connections_without_liveness_kwargs(tmp_path, use_git=False)
    assert len(found) == 1 and "imported from pg_keepalives" in found[0].message


def test_trusted_mappings_cover_a_definition_outside_the_scanned_root(tmp_path):
    lines = ["import psycopg2", "from pg_keepalives import KEEPALIVES", "x = psycopg2.connect(d, **KEEPALIVES)"]
    assert len(_find(tmp_path, lines)) == 1
    assert _find(tmp_path, lines, trusted_mappings={"pg_keepalives.KEEPALIVES": KEEPALIVES}) == []


def test_the_library_constant_can_be_imported_and_spread(tmp_path):
    lines = ["import psycopg2", "from py_ci_shared.connection_liveness_kwargs import KEEPALIVES", "x = psycopg2.connect(d, **KEEPALIVES)"]
    assert _find(tmp_path, lines) == []
    assert set(KEEPALIVES) == set(gate.DEFAULT_REQUIRED) and KEEPALIVES["keepalives"] == 1


def test_psycopg_pool_reads_kwargs_not_direct_keywords(tmp_path):
    direct = _find(tmp_path, ["from psycopg_pool import ConnectionPool", f"x = ConnectionPool(d, {LITERAL})"])
    assert len(direct) == 1
    via = [
        "from psycopg_pool import ConnectionPool",
        SHARED,
        "x = ConnectionPool(d, kwargs=KEEPALIVES)",
        "y = ConnectionPool(d, kwargs={**KEEPALIVES, 'autocommit': True})",
    ]
    assert _find(tmp_path, via) == []
    literal = [
        "from psycopg_pool import ConnectionPool",
        'x = ConnectionPool(d, kwargs={"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 3})',
    ]
    assert _find(tmp_path, literal) == []


def test_the_required_set_is_configurable(tmp_path):
    lines = ["import psycopg2", "x = psycopg2.connect(d, keepalives=1)"]
    assert _find(tmp_path, lines, required=["keepalives"]) == []
    assert len(_find(tmp_path, lines)) == 1


def test_tests_are_skipped_unless_asked_for(tmp_path):
    _write(tmp_path, "src/mod.py", ["x = 1"])
    _write(tmp_path, "tests/test_db.py", ["import psycopg2", "x = psycopg2.connect(d)"])
    assert find_connections_without_liveness_kwargs(tmp_path, use_git=False) == []
    assert len(find_connections_without_liveness_kwargs(tmp_path, use_git=False, include_tests=True)) == 1


def test_exempt_by_file_and_by_function_needs_a_reason_and_must_match(tmp_path):
    lines = ["import psycopg2", "def a(d):", "    return psycopg2.connect(d)", "def b(d):", "    return psycopg2.connect(d)"]
    assert len(_find(tmp_path, lines, exempt={"mod.py::a": "local socket"})) == 1
    assert _find(tmp_path, lines, exempt={"mod.py": "scratch script on localhost"}) == []
    with pytest.raises(ValueError, match="need a reason"):
        _find(tmp_path, lines, exempt={"mod.py": " "})
    stale = _find(tmp_path, ["x = 1"], exempt={"mod.py::gone": "was a probe"})
    assert len(stale) == 1 and "matches no connection opener" in stale[0].message


def test_the_marker_needs_a_reason_and_works_on_the_line_above(tmp_path):
    ok = ["import psycopg2", "# liveness-ok: unix socket, no tunnel", "x = psycopg2.connect(d)", "y = psycopg2.connect(d)  # liveness-ok: localhost"]
    assert _find(tmp_path, ok) == []
    bare = _find(tmp_path, ["import psycopg2", "x = psycopg2.connect(d)  # liveness-ok"])
    assert len(bare) == 1 and "needs a reason" in bare[0].message


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _write(tmp_path, "plain/mod.py", ["import psycopg2", "x = psycopg2.connect(d)"]).read_bytes()
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / "mod.py").write_bytes(BOM + plain)
    assert len(find_connections_without_liveness_kwargs(tmp_path / "bom", use_git=False)) == 1
    assert len(find_connections_without_liveness_kwargs(tmp_path / "plain", use_git=False)) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, "ok.py", ["x = 1"])
    _write(tmp_path, "broken.py", ["def broken(:"])
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_connections_without_liveness_kwargs(tmp_path, use_git=False)
    assert find_connections_without_liveness_kwargs(tmp_path, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_connections_without_liveness_kwargs(tmp_path, use_git=False)
    _write(tmp_path, "a.py", ["x = 1"])
    with pytest.raises(EmptyScanError):
        find_connections_without_liveness_kwargs(tmp_path, use_git=False, min_files=2)


def test_the_assert_entry_fails_with_the_findings_and_a_baseline_accepts_them(tmp_path):
    _write(tmp_path, "mod.py", ["import psycopg2", "x = psycopg2.connect(d)"])
    with pytest.raises(AssertionError, match=r"(?s)1 connection-liveness-kwargs finding\(s\).*mod.py:2"):
        assert_every_connection_has_liveness_kwargs(tmp_path, use_git=False)
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):  # a refresh run skips by design
        assert_every_connection_has_liveness_kwargs(tmp_path, baseline_path=base, refresh=True, grow=True, use_git=False)
    assert_every_connection_has_liveness_kwargs(tmp_path, baseline_path=base, use_git=False)


def test_a_file_that_defines_the_mapping_itself_is_judged_by_its_own_definition(tmp_path):
    """production_scrapers defines KEEPALIVES in one script; an unrelated, incomplete one elsewhere must not fail it."""
    _write(tmp_path, "weak.py", ['KEEPALIVES = {"keepalives": 1}'])
    _write(tmp_path, "own.py", ["import psycopg2", SHARED, "x = psycopg2.connect(d, **KEEPALIVES)"])
    assert find_connections_without_liveness_kwargs(tmp_path, use_git=False) == []
