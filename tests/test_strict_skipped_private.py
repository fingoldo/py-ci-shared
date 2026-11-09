"""46-F8 / 47-F5: a private consumer skipped for want of CONSUMER_READ_TOKEN is red in ci-health and corpus-drift, not a warning."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from py_ci_shared import _consumers, ci_health

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


def _skipped() -> ci_health.RepoHealth:
    consumer = _consumers.Consumer(name="social", repo="o/social", branch="master", private=True)
    return ci_health.collect(consumer, lambda path: pytest.fail("no request may be made"), NOW, can_read_private=False)


def test_skipped_private_repo_is_red_only_when_strict():
    repo = _skipped()
    assert ci_health.exit_code([repo], 2) == 0
    assert ci_health.exit_code([repo], 2, fail_on_skipped_private=True) == 1
    lax = ci_health.render_markdown([repo], NOW, 2)
    strict = ci_health.render_markdown([repo], NOW, 2, fail_on_skipped_private=True)
    assert "| social | (every workflow) | RED |" in strict and "NOT CHECKED" in strict
    assert "| RED |" not in lax and "NOT CHECKED" not in lax


def test_annotation_is_an_error_when_strict_and_a_warning_otherwise():
    repo = _skipped()
    assert ci_health._annotations([repo], 2)[0].startswith("::warning title=consumer skipped::")
    assert ci_health._annotations([repo], 2, fail_on_skipped_private=True)[0].startswith("::error title=consumer skipped::")


def test_a_readable_public_repo_is_never_red_for_the_flag():
    public = ci_health.RepoHealth("pub", "o/pub", "main")
    assert ci_health.exit_code([public], 2, fail_on_skipped_private=True) == 0


def test_cli_flag_end_to_end(tmp_path, monkeypatch, capsys):
    config = tmp_path / "c.toml"
    config.write_text('[[consumer]]\nname = "social"\nrepo = "o/social"\nbranch = "master"\nprivate = true\n', encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for key in ("CONSUMER_READ_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ci_health, "default_api", lambda **kw: (lambda path: pytest.fail("no request"), False))
    assert ci_health.main([str(config), "--include-private"]) == 0
    assert ci_health.main([str(config), "--include-private", "--fail-on-skipped-private"]) == 1
    assert "RED" in capsys.readouterr().out


def _workflow_run(name: str, step: str) -> str:
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    return next(str(s["run"]) for job in workflow["jobs"].values() for s in job["steps"] if s.get("name") == step)


@pytest.mark.parametrize(
    ("name", "step"),
    [("ci-health.yml", "CI health report"), ("corpus-drift.yml", "Check out the corpus repos"), ("consumer-pins.yml", "Check out the consumers")],
)
def test_the_scheduled_workflows_are_strict_unless_allowed(name, step):
    run = _workflow_run(name, step)
    assert 'strict="--fail-on-skipped-private"' in run and "$strict" in run and 'ALLOW_SKIPPED_PRIVATE" = "true"' in run
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow[True]
    assert "allow-skipped-private" in triggers["workflow_dispatch"]["inputs"]


def test_corpus_includes_the_two_private_repos_and_every_non_corpus_consumer_says_why():
    text = (REPO / "configs" / "consumers.toml").read_text(encoding="utf-8")
    consumers = _consumers.load_consumers(REPO / "configs" / "consumers.toml")
    assert {c.name for c in consumers if c.corpus} == {"mlframe", "pyutilz", "llm_bench", "glossum_backend_scripts", "algopacksimple"}
    for consumer in consumers:
        if not consumer.corpus:
            pattern = r"# Not in the corpus: [^\n]+\n\[\[consumer\]\]\nname = " + re.escape('"' + consumer.name + '"')
            assert re.search(pattern, text), consumer.name


def test_corpus_checkout_without_token_keeps_the_public_repos_and_fails_strict_for_the_private_ones(tmp_path, monkeypatch):
    config = tmp_path / "c.toml"
    config.write_text(
        '[[consumer]]\nname = "pub"\nrepo = "o/pub"\nbranch = "main"\nprivate = false\ncorpus = true\n\n'
        '[[consumer]]\nname = "priv"\nrepo = "o/priv"\nbranch = "main"\nprivate = true\ncorpus = true\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CONSUMER_READ_TOKEN", raising=False)
    monkeypatch.setattr(_consumers, "_git_clone", lambda consumer, dest, token: None)

    def run(*flags: str) -> int:
        return _consumers.main(["checkout", str(config), "--corpus-only", *flags, "--dest", str(tmp_path / "d"), "--repos-file", str(tmp_path / "r.toml")])

    assert run() == 0
    assert 'name = "pub"' in (tmp_path / "r.toml").read_text(encoding="utf-8")
    assert 'name = "priv"' not in (tmp_path / "r.toml").read_text(encoding="utf-8")
    assert run("--fail-on-skipped-private") == 1
