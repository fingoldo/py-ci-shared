"""34-F3: the config drift report covers every consumer in configs/consumers.toml, not two hard-coded repos."""

from __future__ import annotations

from py_ci_shared._consumers import load_consumers
from py_ci_shared.config_drift_check import CONSUMERS_FILE, _diff_section, consumer_urls


def test_consumer_urls_follow_the_config_file_branch_and_privacy(tmp_path, monkeypatch):
    cfg = tmp_path / "consumers.toml"
    cfg.write_text(
        '[[consumer]]\nname = "pub"\nrepo = "o/pub"\nbranch = "main"\nprivate = false\n\n[[consumer]]\nname = "priv"\nrepo = "o/priv"\nbranch = "staging"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CONSUMER_READ_TOKEN", raising=False)
    urls, skipped = consumer_urls(cfg)
    assert urls == {"pub": "https://raw.githubusercontent.com/o/pub/main/pyproject.toml"}
    assert skipped == ["priv"], "a private consumer without a token is reported as skipped, not silently dropped"
    urls, skipped = consumer_urls(cfg, token="t")
    assert urls["priv"] == "https://api.github.com/repos/o/priv/contents/pyproject.toml?ref=staging"
    assert skipped == []


def test_every_real_consumer_is_covered():
    everything = {c.name for c in load_consumers(CONSUMERS_FILE)}
    assert len(everything) > 2, "the real file lists more than the two repos the report started with"
    urls, skipped = consumer_urls(CONSUMERS_FILE, token="t")
    assert set(urls) == everything and skipped == []


def test_a_consumer_without_the_tool_table_is_not_divergence():
    docs = {"a": {"tool": {"mypy": {"python_version": "3.10"}}}, "b": {"tool": {}}}
    assert _diff_section("mypy", ("python_version",), docs) == []
    control = {"a": docs["a"], "b": {"tool": {"mypy": {"python_version": "3.9"}}}}
    assert len(_diff_section("mypy", ("python_version",), control)) == 1
