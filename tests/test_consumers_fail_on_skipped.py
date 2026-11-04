"""34-F4: the scheduled consumer-pins job fails when private consumers were skipped for want of a token."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared import _consumers

CONFIG = '[[consumer]]\nname = "pub"\nrepo = "o/pub"\nbranch = "main"\nprivate = false\n\n[[consumer]]\nname = "priv"\nrepo = "o/priv"\nbranch = "main"\n'


def _run(tmp_path: Path, monkeypatch, *flags: str) -> int:
    config = tmp_path / "c.toml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.delenv("CONSUMER_READ_TOKEN", raising=False)
    monkeypatch.setattr(_consumers, "_git_clone", lambda consumer, dest, token: None)
    return _consumers.main(["checkout", str(config), "--dest", str(tmp_path / "d"), "--repos-file", str(tmp_path / "r.toml"), *flags])


def test_skipped_private_consumer_fails_only_with_the_strict_flag(tmp_path, monkeypatch, capsys):
    assert _run(tmp_path, monkeypatch) == 0, "default behaviour is unchanged: skipped loudly, exit 0"
    assert _run(tmp_path, monkeypatch, "--fail-on-skipped-private") == 1
    assert "priv" in capsys.readouterr().err


def test_the_scheduled_job_uses_the_strict_flag_unless_allowed():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "consumer-pins.yml").read_text(encoding="utf-8")
    assert "--fail-on-skipped-private" in workflow and "allow-skipped-private" in workflow
