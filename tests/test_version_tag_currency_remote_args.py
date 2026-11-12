"""The remote URL reaches ``git ls-remote`` after ``--``, so a URL that starts with a dash is never read as an option (audit 51-F6)."""

from __future__ import annotations

from py_ci_shared import version_tag_currency


def test_the_url_follows_a_double_dash_so_it_is_never_an_option(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []

    class _Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run_git(repo, *args, **kwargs):
        seen.append(args)
        return _Proc()

    monkeypatch.setattr(version_tag_currency, "run_git", fake_run_git)
    version_tag_currency._remote_commit("--upload-pack=echo pwned", "v1.0.0")
    (args,) = seen
    assert args[0] == "ls-remote"
    assert args[1] == "--", f"the URL must follow --, got {args[:3]}"
    assert args[2] == "--upload-pack=echo pwned"
