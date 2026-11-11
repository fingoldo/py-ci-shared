"""An include target that cannot be read is reported by name, not dropped with its location headers (audit 40-F3)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.nginx_location_headers import find_nginx_header_problems


def test_an_unreadable_include_is_reported_by_name(tmp_path: Path) -> None:
    """Negative control (40-F3): the old code skipped the include silently; no finding named it."""
    (tmp_path / "main.conf").write_text("include inc.conf;\n", encoding="utf-8")
    (tmp_path / "inc.conf").write_bytes(b"\x80\x81 location / { }\n")  # not UTF-8, no BOM
    findings = find_nginx_header_problems(tmp_path / "main.conf", resolve_includes=True, use_git=False)
    assert [(f.rule, f.path.endswith("inc.conf")) for f in findings] == [("unparsed-file", True)], findings


def test_a_readable_include_raises_no_finding(tmp_path: Path) -> None:
    (tmp_path / "main.conf").write_text("include inc.conf;\n", encoding="utf-8")
    (tmp_path / "inc.conf").write_text("location / { }\n", encoding="utf-8")
    assert find_nginx_header_problems(tmp_path / "main.conf", resolve_includes=True, use_git=False) == []
