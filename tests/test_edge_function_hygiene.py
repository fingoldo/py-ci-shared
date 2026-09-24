"""Unit tests for the edge-function hygiene check. Real scratch TypeScript sources, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import codecs
from pathlib import Path

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
    def test_missing_directory_is_a_problem_not_a_pass(self, tmp_path):
        problems = find_edge_function_problems(tmp_path / "nope")
        assert len(problems) == 1 and "examined nothing" in problems[0]
        with pytest.raises(pytest.fail.Exception, match="examined nothing"):
            assert_edge_functions_are_sound(tmp_path / "nope")

    def test_existing_clean_directory_passes(self, tmp_path):
        root = _functions(tmp_path, ok="export const x = 1;")
        assert find_edge_function_problems(root) == []

    def test_bom_source_is_read_and_checked(self, tmp_path):
        root = tmp_path / "functions" / "enrich"
        root.mkdir(parents=True)
        (root / "index.ts").write_bytes(codecs.BOM_UTF8 + b"console.log(`resolved ${ip}`);")
        assert any("raw IP" in p for p in find_edge_function_problems(tmp_path / "functions"))

    def test_assert_fails(self, tmp_path):
        body = 'try { x(); } catch (e) { return new Response("{}", { status: 200 }); }'
        root = _functions(tmp_path, log_login=body)
        with pytest.raises(pytest.fail.Exception, match="succeeded when it failed"):
            assert_edge_functions_are_sound(root)


class TestFunctionScopedRules:
    def test_nested_helper_belongs_to_its_public_function(self, tmp_path):
        root = tmp_path / "functions"
        (root / "pub" / "lib").mkdir(parents=True)
        (root / "pub" / "index.ts").write_text("const MAX_TEXT = 512;\nconst d = await req.json();\nawait h(d);", encoding="utf-8")
        (root / "pub" / "lib" / "h.ts").write_text("export const h = (d) => client.from('t').insert(d.reports.map(r => r));", encoding="utf-8")
        problems = find_edge_function_problems(root, public_functions=["pub"])
        assert any("array-length cap" in p for p in problems)
        assert find_edge_function_problems(root, public_functions=["other"]) == []

    def test_cap_in_a_helper_module_counts_for_the_function(self, tmp_path):
        root = tmp_path / "functions"
        (root / "f").mkdir(parents=True)
        (root / "f" / "index.ts").write_text("const d = await req.json();\nawait client.from('t').insert(clip(d));", encoding="utf-8")
        (root / "f" / "clip.ts").write_text("export const clip = (d) => ({v: d.v.slice(0, MAX_TEXT)});", encoding="utf-8")
        assert find_edge_function_problems(root) == []
        (root / "f" / "clip.ts").write_text("export const clip = (d) => d;", encoding="utf-8")
        assert any("no size cap" in p for p in find_edge_function_problems(root))


class TestIpLogging:
    def test_interpolation_after_a_nested_call_is_flagged(self, tmp_path):
        root = _functions(tmp_path, enrich="console.log(`${String(x)} ${ip}`);")
        assert any("raw IP" in p for p in find_edge_function_problems(root))

    def test_plain_ip_argument_is_flagged(self, tmp_path):
        root = _functions(tmp_path, enrich='console.log("ip", ip);')
        assert any("raw IP" in p for p in find_edge_function_problems(root))

    def test_ip_only_inside_a_string_literal_passes(self, tmp_path):
        root = _functions(tmp_path, enrich='console.log("ip lookup failed", count);')
        assert find_edge_function_problems(root) == []

    def test_plain_redacted_argument_passes(self, tmp_path):
        root = _functions(tmp_path, enrich='console.log("ip", redactIp(ip));')
        assert find_edge_function_problems(root) == []


class TestResponseJsonAndPresenceChecks:
    def test_response_json_in_a_catch_is_flagged(self, tmp_path):
        root = _functions(tmp_path, f="try { await x(); } catch (e) { return Response.json({ok: true}); }")
        assert any("succeeded when it failed" in p for p in find_edge_function_problems(root))

    def test_response_json_with_error_status_passes(self, tmp_path):
        root = _functions(tmp_path, f="try { await x(); } catch (e) { return Response.json({ok: false}, { status: 500 }); }")
        assert find_edge_function_problems(root) == []

    @pytest.mark.parametrize("cmp", ['apiKey === ""', "apiKey == null", "undefined !== apiKey", "apiKey === ''"])
    def test_emptiness_check_is_not_a_secret_comparison(self, tmp_path, cmp):
        root = _functions(tmp_path, f=f"if ({cmp}) {{ return bad(); }}")
        assert find_edge_function_problems(root) == []

    def test_real_secret_comparison_still_flagged(self, tmp_path):
        root = _functions(tmp_path, f="if (apiKey === provided) { ok(); }")
        assert any("string equality" in p for p in find_edge_function_problems(root))
