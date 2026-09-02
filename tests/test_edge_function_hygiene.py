"""Unit tests for the edge-function hygiene check. Real scratch TypeScript sources, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.edge_function_hygiene import (
    assert_edge_functions_are_sound,
    find_edge_function_problems,
)


def _functions(tmp_path: Path, **bodies: str) -> Path:
    root = tmp_path / "functions"
    for name, body in bodies.items():
        d = root / name.replace("_", "-")
        d.mkdir(parents=True)
        (d / "index.ts").write_text(body, encoding="utf-8")
    return root


class TestCatchStatus:
    def test_catch_answering_success_is_flagged(self, tmp_path):
        body = 'try { await x(); } catch (e) { return new Response("{}", { status: 200 }); }'
        root = _functions(tmp_path, log_login=body)
        problems = find_edge_function_problems(root)
        assert len(problems) == 1
        assert "succeeded when it failed" in problems[0]

    def test_catch_with_no_status_is_flagged(self, tmp_path):
        body = 'try { await x(); } catch (e) { return new Response("{}"); }'
        root = _functions(tmp_path, log_login=body)
        assert len(find_edge_function_problems(root)) == 1

    def test_catch_answering_500_passes(self, tmp_path):
        body = 'try { await x(); } catch (e) { return new Response("{}", { status: 500 }); }'
        root = _functions(tmp_path, log_login=body)
        assert find_edge_function_problems(root) == []


class TestBodyCaps:
    def test_uncapped_insert_is_flagged(self, tmp_path):
        body = "const d = await req.json();\nawait client.from('t').insert(d);"
        root = _functions(tmp_path, log_login=body)
        problems = find_edge_function_problems(root)
        assert any("no size cap" in p for p in problems)

    def test_capped_insert_passes(self, tmp_path):
        body = "const MAX_TEXT = 512;\nconst d = await req.json();\nawait client.from('t').insert({v: d.v.slice(0, MAX_TEXT)});"
        root = _functions(tmp_path, log_login=body)
        assert find_edge_function_problems(root) == []

    def test_public_function_without_array_cap_is_flagged(self, tmp_path):
        body = "const MAX_TEXT = 512;\nconst d = await req.json();\nawait client.from('t').insert(d.reports.map(r => r));"
        root = _functions(tmp_path, csp_report=body)
        problems = find_edge_function_problems(root, public_functions=["csp-report"])
        assert any("array-length cap" in p for p in problems)

    def test_public_function_with_array_cap_passes(self, tmp_path):
        body = "const MAX_TEXT = 512;\nconst d = await req.json();\n" "if (d.reports.length > 20) return bad();\nawait client.from('t').insert(d.reports);"
        root = _functions(tmp_path, csp_report=body)
        assert find_edge_function_problems(root, public_functions=["csp-report"]) == []


class TestSecretsAndLogging:
    def test_string_secret_comparison_is_flagged(self, tmp_path):
        body = 'if (header === Deno.env.get("SERVICE_ROLE_KEY")) { ok(); }'
        root = _functions(tmp_path, enrich=body)
        problems = find_edge_function_problems(root)
        assert any("string equality" in p for p in problems)

    def test_digest_comparison_passes(self, tmp_path):
        body = (
            'const a = await crypto.subtle.digest("SHA-256", enc(header));\n'
            'const b = await crypto.subtle.digest("SHA-256", enc(Deno.env.get("SERVICE_ROLE_KEY")));\n'
            "if (digestsEqual(a, b)) ok();"
        )
        root = _functions(tmp_path, enrich=body)
        assert find_edge_function_problems(root) == []

    def test_logging_a_raw_ip_is_flagged(self, tmp_path):
        body = "console.log(`resolved ${ip} to ${country}`);"
        root = _functions(tmp_path, enrich=body)
        problems = find_edge_function_problems(root)
        assert any("raw IP" in p for p in problems)

    def test_logging_a_redacted_ip_passes(self, tmp_path):
        body = "console.log(`resolved ${redactIp(addr)} to ${country}`);"
        root = _functions(tmp_path, enrich=body)
        assert find_edge_function_problems(root) == []

    def test_redacting_the_variable_named_ip_also_passes(self, tmp_path):
        # The fix keeps the variable name; matching on the name alone made the rule impossible
        # to satisfy, which is how it reported thirteen already-fixed lines.
        body = "console.log(`Skipping malformed IP: ${redactIp(ip)}`);"
        root = _functions(tmp_path, enrich=body)
        assert find_edge_function_problems(root) == []

    def test_first_forwarded_hop_is_flagged(self, tmp_path):
        body = 'const ip = req.headers.get("x-forwarded-for")?.split(",")[0];'
        root = _functions(tmp_path, log_login=body)
        problems = find_edge_function_problems(root)
        assert any("FIRST x-forwarded-for" in p for p in problems)

    def test_last_forwarded_hop_passes(self, tmp_path):
        body = 'const hops = req.headers.get("x-forwarded-for")?.split(","); const ip = hops?.at(-1);'
        root = _functions(tmp_path, log_login=body)
        assert find_edge_function_problems(root) == []


class TestAssert:
    def test_missing_directory_is_a_no_op(self, tmp_path):
        assert find_edge_function_problems(tmp_path / "nope") == []

    def test_assert_fails(self, tmp_path):
        body = 'try { x(); } catch (e) { return new Response("{}", { status: 200 }); }'
        root = _functions(tmp_path, log_login=body)
        with pytest.raises(pytest.fail.Exception, match="succeeded when it failed"):
            assert_edge_functions_are_sound(root)
