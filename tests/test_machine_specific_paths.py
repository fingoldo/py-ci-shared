from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.machine_specific_paths import assert_no_machine_specific_paths, find_machine_specific_paths


def _justify_seeded(path: Path, where: str, branch: str) -> None:
    """Replace the refresh marker on each entry this test seeds on purpose; each reason names its own key."""
    from py_ci_shared._core.baseline import is_unjustified

    data = json.loads(path.read_text(encoding="utf-8"))
    for key, entry in data["entries"].items():
        if is_unjustified(entry["note"]):
            entry["note"] = f"{key} is seeded by {where} to exercise the {branch} of the gate, and is not a defect in repo code."
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path, **kw: object) -> list[tuple[str, int, str, str]]:
    return [(f.path, f.line, f.rule, f.message) for f in find_machine_specific_paths(root, use_git=False, **kw)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("literal", "rule", "shown"),
    [
        (r"C:\\Users\\alice\\data.csv", "user-home-path", "C:\\Users\\alice\\data.csv"),
        ("c:/users/Bob/x", "user-home-path", "c:/users/Bob/x"),
        ("/home/carol/work/x.txt", "user-home-path", "/home/carol/work/x.txt"),
        ("/Users/dave/Library/x", "user-home-path", "/Users/dave/Library/x"),
        ("D:/Temp/rows.csv", "drive-path", "D:/Temp/rows.csv"),
        (r"E:\\models\\m.bin", "drive-path", "E:\\models\\m.bin"),
        ("D:/.cache/stanza", "model-cache-path", ".cache/stanza"),
        ("~/.cache/huggingface/hub", "model-cache-path", ".cache/huggingface/hub"),
        ("/opt/stanza_resources", "model-cache-path", "/stanza_resources"),
        ("postgresql+asyncpg://postgres:postgres@localhost:5432/db", "db-url", "postgresql+asyncpg://postgres:postgres@"),
        ("mysql://app:s3cret@db/x", "db-url", "mysql://app:s3cret@"),
    ],
)
def test_each_rule_fires_on_a_python_literal(tmp_path: Path, literal: str, rule: str, shown: str) -> None:
    _write(tmp_path, "m.py", f'X = "{literal}"\n')
    assert _found(tmp_path) == [("m.py", 1, rule, shown)]


@pytest.mark.parametrize(
    "literal",
    [
        "C:/Users/<user>/x",
        "C:/Users/runneradmin/x",
        "/home/runner/work/x",
        "/home/{user}/x",
        "C:/Program Files/Tool/x.exe",
        "https://example.com/a",
        "STANZA_RESOURCES_DIR",
        "postgresql://user:${PASSWORD}@host/db",
        "postgresql://user:password@host/db",
        "postgresql://localhost/db",
        r"(\\d{2}):(\\d\\d|\\Z)d:\\d\\d",
        "D:/Temp/x  # machine-path-ok",
    ],
)
def test_placeholders_and_non_paths_are_not_flagged(tmp_path: Path, literal: str) -> None:
    if literal.endswith("# machine-path-ok"):
        _write(tmp_path, "m.py", 'X = "D:/Temp/x"  # machine-path-ok: local profiling only\n')
    else:
        _write(tmp_path, "m.py", f'X = "{literal}"\n')
    assert _found(tmp_path) == []


def test_docstrings_and_comments_are_not_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", '"""Reads D:/Temp/x."""\n# D:/Temp/y\ndef f():\n    """C:/Users/alice/z"""\n    return 1\n')
    assert _found(tmp_path) == []


def test_multiline_literal_reports_the_line_of_the_match(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", 'X = """first\nsecond D:/Temp/a.csv\n"""\n')
    assert _found(tmp_path) == [("m.py", 2, "drive-path", "D:/Temp/a.csv")]


def test_config_files_scanned_with_comments_stripped_and_workflow_dsn_exempt(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "x = 1\n")
    _write(tmp_path, "conf.toml", 'cache = "D:/cache"\n# old = "E:/old"\nurl = "postgres://a:b@h/d"  # note\n')
    _write(tmp_path, ".github/workflows/ci.yml", "env:\n  DATABASE_URL: postgresql://ci:ci@localhost/db\n  OUT: D:/out\n")
    assert _found(tmp_path) == [
        (".github/workflows/ci.yml", 3, "drive-path", "D:/out"),
        ("conf.toml", 1, "drive-path", "D:/cache"),
        ("conf.toml", 3, "db-url", "postgres://a:b@"),
    ]
    assert _found(tmp_path, config_patterns=()) == []


def test_allow_regexes_and_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", 'X = "D:/Temp/a"\nY = "E:/data/b"\n')
    _write(tmp_path, "tests/t.py", 'X = "D:/Temp/c"\n')
    assert [f[3] for f in _found(tmp_path, allow=[r"^D:/Temp"])] == ["E:/data/b"]
    assert len(_found(tmp_path, include_tests=True)) == 3


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", 'X = "D:/Temp/a"\n', bom=True)
    _write(tmp_path, "c.yaml", "p: D:/Temp/b\n", bom=True)
    assert [(f[0], f[3]) for f in _found(tmp_path)] == [("c.yaml", "D:/Temp/b"), ("m.py", "D:/Temp/a")]


def test_unparsable_and_undecodable_files_fail(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def (:\n")
    (tmp_path / "bad.yml").write_bytes(b"k: \xff\xfe\n")
    rules = sorted(f.rule for f in find_machine_specific_paths(tmp_path, use_git=False))
    assert rules == ["unparsed-file", "unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="2 file"):
        assert_no_machine_specific_paths(tmp_path, use_git=False)


def test_empty_corpus_fails(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_machine_specific_paths(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", 'X = "relative/path"\n')
    assert_no_machine_specific_paths(src, use_git=False)
    _write(src, "m.py", 'X = "D:/Temp/a"\n')
    with pytest.raises(pytest.fail.Exception, match=r"\[drive-path\] D:/Temp/a"):
        assert_no_machine_specific_paths(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=True, use_git=False)
    _justify_seeded(baseline, "tests/test_machine_specific_paths.py::test_assert_raw_and_baseline", "refresh-then-accept branch for a drive-letter path")
    assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", 'X = "D:/Temp/a"\nY = "D:/Temp/a"\n')
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=False, use_git=False)


def test_a_refreshed_baseline_carries_no_absolute_path_and_passes_baseline_hygiene(tmp_path: Path) -> None:
    import json

    from py_ci_shared.baseline_hygiene import find_baseline_problems

    src = tmp_path / "src"
    _write(src, "m.py", 'X = "C:/Users/alice/data"\nY = "/home/bob/cache/x"\n')
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.skip.Exception):
        assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=True, use_git=False)
    _justify_seeded(
        baseline,
        "tests/test_machine_specific_paths.py::test_a_refreshed_baseline_carries_no_absolute_path_and_passes_baseline_hygiene",
        "absolute-path hygiene branch for user-home paths in a refreshed baseline",
    )
    text = baseline.read_text(encoding="utf-8")
    assert "alice" not in text and "bob" not in text and "m.py" in text
    assert [p for p in find_baseline_problems(baseline, require_notes=False) if "absolute path" in p] == []
    assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", 'X = "C:/Users/alice/data"\nY = "/home/carol/cache/x"\n')
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_machine_specific_paths(src, baseline_path=baseline, refresh=False, use_git=False)
    assert json.loads(text)


def _kinds(tmp_path, rel, body):
    _write(tmp_path, rel, body)
    return [(f.path, f.line, f.rule) for f in find_machine_specific_paths(tmp_path, use_git=False)]


def test_a_dsn_of_any_scheme_with_a_real_password_is_reported(tmp_path):
    at = chr(64)  # built at run time so no credential-shaped literal sits in this repository
    body = f"A = 'amqp://svc:s3cr3tpw{at}mq.internal/'\nB = 'https://bot:ghp_realtoken{at}host/x'\nC = 'redis://u:password{at}h/0'\n"
    assert _kinds(tmp_path, "m.py", body) == [("m.py", 1, "db-url"), ("m.py", 2, "db-url")]


def test_a_concatenated_and_a_bytes_drive_path_are_reported_once_each(tmp_path):
    body = "A = 'D:' + '/data'\nB = b'D:/data'\n"
    assert _kinds(tmp_path, "m.py", body) == [("m.py", 1, "drive-path"), ("m.py", 2, "drive-path")]


@pytest.mark.parametrize("rel", [".env", "run.sh", "setup.ps1", "cfg/app.json"])
def test_env_shell_and_json_files_are_scanned_by_default(tmp_path, rel):
    assert _kinds(tmp_path, rel, "OUT=D:/data/out\n") == [(rel, 1, "drive-path")]


def test_a_sed_expression_is_not_a_db_url(tmp_path):
    _write(tmp_path, "tool/check.sh", 'sed -E "s://.*::root@:" file\n')
    assert [r for r in _found(tmp_path) if r[2] == "db-url"] == []


def test_a_real_dsn_with_inline_password_is_still_a_db_url(tmp_path):
    _write(tmp_path, "tool/check.sh", 'DSN="postgresql://app:hunter2@db:5432/x"\n')  # pragma: allowlist secret
    assert [r[2] for r in _found(tmp_path)] == ["db-url"]


def _prefilter_corpus() -> "list[str]":
    """Lines that hit each rule in several spellings, near misses, and ordinary config text."""
    bs = chr(92)
    return [
        "path: C:" + bs + "Users" + bs + "alice" + bs + "data",
        "home = /home/bob/x/",
        "root: /Users/carol/work/",
        "cache: D:/models/x",
        "drive = e:" + bs + "cache",
        "dir: ~/.cache/huggingface/hub",
        "dir: x/.CACHE/TORCH",
        "res: stanza_resources/en",
        "url: postgresql://u:p@host/db",
        "url: mysql+pymysql://root:" + "pw" + "@h:3306/x",  # joined so the secret scanner does not read the literal as a credential
        "time: 12:30:45",
        "link: https://example.com/a",
        "owner: C:/Windows/x",
        "runner: /home/runner/work",
        "/HOME/Dan/x",
        '  "id": "abc", "tags": ["a", "b"],',
        "key = value",
        "",
    ]


def test_the_candidate_prefilter_never_hides_a_hit(monkeypatch):
    """A text the prefilter rejects has no hit: the hits with the prefilter equal the hits of the five rule scans alone."""
    import re

    from py_ci_shared import machine_specific_paths as m

    corpus = _prefilter_corpus()
    with_filter = [list(m._hits(line, [], workflow=wf)) for line in corpus for wf in (False, True)]
    monkeypatch.setattr(m, "_CANDIDATE", re.compile(""))
    without_filter = [list(m._hits(line, [], workflow=wf)) for line in corpus for wf in (False, True)]
    assert with_filter == without_filter
    assert any(with_filter), "the corpus must contain real hits, or the comparison proves nothing"
