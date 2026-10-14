"""Tests for py_ci_shared.nginx_location_headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.nginx_location_headers import (
    NginxParseError,
    assert_nginx_location_headers,
    find_nginx_header_problems,
    parse_nginx,
)

BOM = b"\xef\xbb\xbf"
SERVER = """server {
    listen ${PORT};
    # a comment with a ; and a { brace
    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:" always;
    add_header X-Content-Type-Options nosniff always;
    location / {
        try_files $uri /index.html;
    }
    location /robots.txt {
        add_header Cache-Control no-cache;
    }
}
"""


def _write(tmp_path: Path, text: str, name: str = "nginx.conf.template") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _found(path, **kw) -> list[tuple[int, str, str]]:
    return [(f.line, f.rule, f.message) for f in find_nginx_header_problems([path], **kw)]


def test_a_location_with_its_own_add_header_drops_the_server_headers(tmp_path):
    path = _write(tmp_path, SERVER)
    assert _found(path) == [
        (
            9,
            "dropped-header",
            (
                "`location /robots.txt` (line 9) sets add_header Cache-Control, so it no longer inherits "
                "Content-Security-Policy, X-Content-Type-Options from `server` (line 1); repeat them in this block"
            ),
        )
    ]
    with pytest.raises(BaseException, match=r"robots.txt"):
        assert_nginx_location_headers([path])


def test_repeating_the_inherited_headers_is_clean_case_insensitively(tmp_path):
    text = SERVER.replace(
        "add_header Cache-Control no-cache;",
        "add_header Cache-Control no-cache;\n add_header content-security-policy \"default-src 'self'\";\n add_header X-CONTENT-TYPE-OPTIONS nosniff;",
    )
    path = _write(tmp_path, text)
    assert _found(path) == []
    assert_nginx_location_headers([path])


def test_a_location_without_add_header_inherits_and_is_clean(tmp_path):
    path = _write(tmp_path, SERVER.replace("        add_header Cache-Control no-cache;\n", "        expires 1d;\n"))
    assert _found(path) == []


def test_nested_if_and_http_levels(tmp_path):
    text = """http {
    add_header X-Frame-Options DENY;
    server {
        location /api {
            if ($request_method = OPTIONS) {
                add_header Access-Control-Allow-Origin "*";
                return 204;
            }
        }
    }
}
"""
    found = _found(_write(tmp_path, text, "a.conf"))
    assert [(line, rule) for line, rule, _ in found] == [(5, "dropped-header")]
    assert "X-Frame-Options from `http` (line 1)" in found[0][2]


def test_the_nearest_declaring_level_is_what_is_inherited(tmp_path):
    text = """server {
    add_header A 1;
    location / {
        add_header A 1;
        add_header B 2;
        location /x {
            add_header B 2;
        }
    }
}
"""
    found = _found(_write(tmp_path, text, "a.conf"))
    assert [(line, rule) for line, rule, _ in found] == [(6, "dropped-header")]
    assert "inherits A from `location /` (line 3)" in found[0][2]


def test_required_headers(tmp_path):
    path = _write(tmp_path, SERVER)
    found = _found(path, required=("Content-Security-Policy", "Strict-Transport-Security"))
    required = [(line, msg) for line, rule, msg in found if rule == "missing-required"]
    assert required == [
        (1, "`server` (line 1) serves responses without Strict-Transport-Security"),
        (6, "`location /` (line 6) serves responses without Strict-Transport-Security"),
        (9, "`location /robots.txt` (line 9) serves responses without Content-Security-Policy, Strict-Transport-Security"),
    ]
    assert [r for _, r, _ in _found(path, required=("x-content-type-options",)) if r == "missing-required"] == ["missing-required"]


def test_an_include_is_unknown_unless_resolved(tmp_path):
    text = """server {
    add_header X-Frame-Options DENY;
    location / {
        include headers.conf;
        add_header Cache-Control no-cache;
    }
}
"""
    path = _write(tmp_path, text, "site.conf")
    rules = [rule for _, rule, _ in _found(path)]
    assert rules == ["dropped-header", "unresolved-include"]
    (tmp_path / "headers.conf").write_text("add_header X-Frame-Options DENY;\n", encoding="utf-8")
    assert _found(path, resolve_includes=True) == []


def test_an_include_where_no_header_is_set_is_not_reported(tmp_path):
    path = _write(tmp_path, "http {\n    include /etc/nginx/mime.types;\n    server { listen 80; }\n}\n", "a.conf")
    assert _found(path) == []


def test_free_form_blocks_are_not_descended_into(tmp_path):
    text = "map $http_x $y {\n    default 0;\n    ~^a{2}$ 1;\n}\ntypes { text/html html; }\nserver { add_header A 1; }\n"
    assert _found(_write(tmp_path, text.replace("~^a{2}$", '"~^a{2}$"'), "a.conf")) == []


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("server {\n    location / {\n}\n", 1),
        ("server {\n}\n}\n", 3),
        ("server {\n    listen 80\n}\n", 2),
        ("listen 80", 1),
        ('add_header X "unterminated;\n', 1),
        ("server {\n    ;\n}\n", 2),
    ],
)
def test_unbalanced_or_unterminated_configs_do_not_parse(text, line):
    with pytest.raises(NginxParseError) as err:
        parse_nginx(text)
    assert err.value.line == line


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    good = _write(tmp_path, SERVER.replace("        add_header Cache-Control no-cache;\n", ""), "good.conf")
    bad = _write(tmp_path, "server {\n    location / {\n}\n", "broken.conf")
    found = find_nginx_header_problems([good, bad])
    assert [(Path(f.path).name, f.rule) for f in found] == [("broken.conf", "unparsed-file")]
    with pytest.raises(BaseException, match=r"broken.conf"):
        assert_nginx_location_headers([good, bad])
    assert_nginx_location_headers([good, bad], allow_unparsed=True)


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _found(_write(tmp_path, SERVER, "plain.conf"))
    (tmp_path / "bom.conf").write_bytes(BOM + SERVER.encode())
    assert _found(tmp_path / "bom.conf") == plain and len(plain) == 1


def test_a_directory_is_searched_and_an_empty_one_fails_the_floor(tmp_path):
    _write(tmp_path, SERVER, "site.conf.template")
    (tmp_path / "notes.txt").write_text("server { add_header A 1; location / { add_header B 1; } }", encoding="utf-8")
    assert [f.path for f in find_nginx_header_problems(tmp_path, use_git=False)] == ["site.conf.template"]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BaseException, match="expected at least 1"):
        assert_nginx_location_headers(empty, use_git=False)


EXEMPT_CONF = """server {
    add_header Content-Security-Policy "default-src 'self'" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    location / {
        try_files $uri /index.html;
    }
    location = /healthz {
        add_header Content-Security-Policy "default-src 'self'" always;
        add_header X-Content-Type-Options nosniff always;
        return 200;
    }
    location = /other {
        add_header Cache-Control no-cache;
    }
}
"""
DOC = ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options")
IN_EXEMPT = ("Content-Security-Policy", "X-Content-Type-Options")


def _exempt_found(tmp_path, text=EXEMPT_CONF, exempt=None):
    path = _write(tmp_path, text, "site.conf")
    exempt = {"= /healthz": "a probe, not a document", "= /other": "asset"} if exempt is None else exempt
    return _found(path, required=DOC, exempt=exempt, required_in_exempt=IN_EXEMPT)


def test_an_exempt_block_carrying_the_exempt_headers_is_clean(tmp_path):
    assert [f for f in _exempt_found(tmp_path) if "healthz" in f[2]] == []


def test_an_exempt_block_missing_an_exempt_header_is_a_finding(tmp_path):
    found = _exempt_found(tmp_path)
    assert found == [
        (
            13,
            "missing-required",
            "`location = /other` (line 13) serves responses without Content-Security-Policy, X-Content-Type-Options (required even in an exempt block)",
        )
    ]


def test_an_empty_reason_and_a_stale_key_are_findings(tmp_path):
    found = _exempt_found(tmp_path, exempt={"= /healthz": "  ", "= /other": "asset", "= /gone": "was removed"})
    exemption = [(rule, msg) for _, rule, msg in found if rule == "exemption"]
    assert exemption == [
        ("exemption", "exempt location `= /gone` matches no location block (stale exemption)"),
        ("exemption", "exempt location `= /healthz` gives no reason"),
    ]


def test_a_block_that_is_not_exempt_is_still_flagged(tmp_path):
    rules = sorted({(line, rule) for line, rule, _ in _exempt_found(tmp_path, exempt={"= /healthz": "probe"})})
    assert rules == [(13, "dropped-header"), (13, "missing-required")]


def test_exempt_is_threaded_through_the_assert(tmp_path):
    path = _write(
        tmp_path,
        EXEMPT_CONF.replace(
            "add_header Cache-Control no-cache;", "add_header Cache-Control no-cache;\n" + "\n".join(f"        add_header {h} x;" for h in IN_EXEMPT)
        ),
        "site.conf",
    )
    assert_nginx_location_headers([path], required=DOC, exempt={"= /healthz": "probe", "= /other": "asset"}, required_in_exempt=IN_EXEMPT)
    with pytest.raises(BaseException, match=r"stale exemption"):
        assert_nginx_location_headers([path], required=DOC, exempt={"= /healthz": "probe", "= /other": "asset", "= /x": "y"}, required_in_exempt=IN_EXEMPT)
