"""Unit tests for the test-partition reachability check. Real scratch config files, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.test_partition_reachability import (
    assert_partitions_reachable,
    find_permanent_skips,
    find_unreachable_tags,
    find_unreferenced_scripts,
    find_unselected_projects,
)

_DART_TEST_YAML = """\
tags:
  golden:
    # PNG baselines are host-generated
  benchmark:
    # wall-clock
"""

_PLAYWRIGHT = """\
export default defineConfig({
  projects: [
    { name: 'chromium-desktop', use: {} },
    { name: 'webkit-mobile-390', use: {} },
  ],
});
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


class TestTags:
    def test_tag_excluded_everywhere_and_included_nowhere_is_flagged(self, tmp_path):
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        runner = "flutter test --exclude-tags golden --exclude-tags benchmark"
        unreachable = find_unreachable_tags(cfg, runner)
        assert set(unreachable) == {"golden", "benchmark"}

    def test_tag_selected_by_some_runner_is_reachable(self, tmp_path):
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        runner = "flutter test --exclude-tags golden\nflutter test --tags golden\nflutter test --tags benchmark"
        assert find_unreachable_tags(cfg, runner) == []

    def test_tag_nobody_mentions_is_not_flagged(self, tmp_path):
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        assert find_unreachable_tags(cfg, "flutter test") == []


class TestProjects:
    def test_unselected_project_is_flagged_when_others_are_named(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", _PLAYWRIGHT)
        assert find_unselected_projects(cfg, "npx playwright test --project=chromium-desktop") == ["webkit-mobile-390"]

    def test_no_project_flag_anywhere_means_all_run(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", _PLAYWRIGHT)
        assert find_unselected_projects(cfg, "npx playwright test") == []

    def test_all_projects_selected_passes(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", _PLAYWRIGHT)
        runner = "--project=chromium-desktop --project=webkit-mobile-390"
        assert find_unselected_projects(cfg, runner) == []


class TestScriptsAndSkips:
    def test_unreferenced_script_is_flagged(self, tmp_path):
        _write(tmp_path, "e2e/probe.mjs", "// probe\n")
        assert find_unreferenced_scripts([tmp_path / "e2e"], "") == ["e2e/probe.mjs"]

    def test_referenced_script_passes(self, tmp_path):
        _write(tmp_path, "e2e/probe.mjs", "// probe\n")
        assert find_unreferenced_scripts([tmp_path / "e2e"], "node probe.mjs") == []

    def test_spec_dir_is_skipped(self, tmp_path):
        _write(tmp_path, "e2e/tests/smoke.spec.ts", "// spec\n")
        assert find_unreferenced_scripts([tmp_path / "e2e"], "") == []

    def test_permanent_skip_is_flagged(self, tmp_path):
        _write(tmp_path, "e2e/tests/offline.spec.ts", "test.skip('flaky', async () => {});\n")
        skips = find_permanent_skips([tmp_path / "e2e"])
        assert len(skips) == 1
        assert "offline.spec.ts:1" in skips[0]

    def test_conditional_skip_is_a_platform_guard_not_a_disabled_test(self, tmp_path):
        # `test.skip(browserName !== 'chromium', ...)` is how a Chromium-only capability is
        # guarded. Flagging those buried the genuinely disabled ones under seven lines of noise.
        _write(
            tmp_path,
            "e2e/tests/x.spec.ts",
            "test.skip(browserName !== 'chromium', 'CDP is Chromium-only');\n",
        )
        assert find_permanent_skips([tmp_path / "e2e"]) == []

    def test_named_skip_is_a_disabled_test(self, tmp_path):
        _write(tmp_path, "e2e/tests/y.spec.ts", "test.skip('boots offline', async () => {});\n")
        assert len(find_permanent_skips([tmp_path / "e2e"])) == 1


class TestAssert:
    def test_assert_passes(self, tmp_path):
        runner = _write(tmp_path, "ci.yml", "run: flutter test --tags golden --exclude-tags benchmark\n")
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        assert_partitions_reachable(runner_texts=[runner], declared_tags=cfg, allowed={"benchmark": "scheduled job"})

    def test_assert_fails_and_names_the_tag(self, tmp_path):
        runner = _write(tmp_path, "ci.yml", "run: flutter test --exclude-tags benchmark\n")
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        with pytest.raises(pytest.fail.Exception, match="benchmark"):
            assert_partitions_reachable(runner_texts=[runner], declared_tags=cfg)


class TestAuditRegressions:
    @pytest.mark.parametrize(
        "line",
        [
            "test.describe.skip('suite', () => {});",
            "test.fixme('broken flow', async () => {});",
            "xit('old', () => {});",
            "xdescribe('old suite', () => {});",
            "test.describe.fixme('suite', () => {});",
        ],
    )
    def test_every_disabled_test_spelling_is_found(self, tmp_path, line):
        _write(tmp_path, "e2e/tests/a.spec.ts", line + "\n")
        assert len(find_permanent_skips([tmp_path / "e2e"])) == 1

    def test_a_conditional_fixme_is_still_a_guard(self, tmp_path):
        _write(tmp_path, "e2e/tests/a.spec.ts", "test.fixme(browserName === 'webkit', 'bug 123');\n")
        assert find_permanent_skips([tmp_path / "e2e"]) == []

    def test_a_quoted_project_name_with_a_space_is_read_whole(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", "projects: [{ name: 'Mobile Chrome' }, { name: 'Mobile Safari' }]\n")
        assert find_unselected_projects(cfg, 'npx playwright test --project="Mobile Chrome"') == ["Mobile Safari"]
        assert find_unselected_projects(cfg, "npx playwright test --project 'Mobile Chrome' --project='Mobile Safari'") == []

    def test_an_unquoted_name_stops_at_a_shell_metacharacter(self, tmp_path):
        """polyvocab_app ci.yml: the subshell's `)` is not part of the project name (CANARY-25)."""
        cfg = _write(tmp_path, "e2e/playwright.config.ts", "projects: [{ name: 'chromium-desktop' }]\n")
        assert find_unselected_projects(cfg, "          (cd e2e && npx playwright test --project=chromium-desktop)\n") == []
        for tail in (";", " | tee log", "&& echo ok", "&"):
            assert find_unselected_projects(cfg, f"npx playwright test --project=chromium-desktop{tail}\n") == [], tail

    def test_a_subshell_selection_still_reports_a_project_nobody_runs(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", _PLAYWRIGHT)
        runner = "          (cd e2e && npx playwright test --project=chromium-desktop)\n"
        assert find_unselected_projects(cfg, runner) == ["webkit-mobile-390"]

    def test_a_continuation_or_a_comment_does_not_disable_the_check(self, tmp_path):
        cfg = _write(tmp_path, "e2e/playwright.config.ts", _PLAYWRIGHT)
        continued = "run: npx playwright test \\\n  --project=chromium-desktop\n"
        assert find_unselected_projects(cfg, continued) == ["webkit-mobile-390"]
        commented = "# npx playwright test\nrun: npx playwright test --project=chromium-desktop\n"
        assert find_unselected_projects(cfg, commented) == ["webkit-mobile-390"]
        assert find_unselected_projects(cfg, "npx playwright test --project=chromium-desktop\nnpx playwright test\n") == []

    def test_skip_dir_names_are_matched_below_the_script_dir_only(self, tmp_path):
        _write(tmp_path, "tests/e2e/probe.mjs", "// probe\n")
        assert find_unreferenced_scripts([tmp_path / "tests" / "e2e"], "") == ["e2e/probe.mjs"]
        _write(tmp_path, "tests/e2e/tests/helper.mjs", "// inside a tests dir under the script dir\n")
        assert find_unreferenced_scripts([tmp_path / "tests" / "e2e"], "") == ["e2e/probe.mjs"]

    def test_missing_paths_fail_instead_of_passing_clean(self, tmp_path):
        runner = _write(tmp_path, "ci.yml", "run: flutter test\n")
        with pytest.raises(pytest.fail.Exception, match="do not exist"):
            assert_partitions_reachable(runner_texts=[runner], declared_tags=tmp_path / "dart_tset.yaml")
        with pytest.raises(pytest.fail.Exception, match="do not exist"):
            assert_partitions_reachable(runner_texts=[tmp_path / "missing.yml"])
        with pytest.raises(FileNotFoundError):
            find_unreachable_tags(tmp_path / "nope.yaml", "")

    def test_quoted_and_short_tag_flags_and_deeper_indentation(self, tmp_path):
        cfg = _write(tmp_path, "dart_test.yaml", "tags:\n    golden:\n    benchmark:\n    slow:\n")
        runner = 'flutter test --exclude-tags "benchmark" -x golden\ndart test -x slow\ndocker build -t golden .\n'
        assert set(find_unreachable_tags(cfg, runner)) == {"benchmark", "golden", "slow"}
        assert find_unreachable_tags(cfg, runner + "flutter test -t 'golden || slow'\n") == ["benchmark"]

    def test_a_script_name_inside_a_longer_name_is_not_a_reference(self, tmp_path):
        _write(tmp_path, "e2e/run.py", "# run\n")
        assert find_unreferenced_scripts([tmp_path / "e2e"], "python e2e/prerun.py") == ["e2e/run.py"]
        assert find_unreferenced_scripts([tmp_path / "e2e"], "python e2e/run.py --fast") == []

    def test_empty_and_stale_allowlist_reasons_fail(self, tmp_path):
        runner = _write(tmp_path, "ci.yml", "run: flutter test --exclude-tags benchmark --exclude-tags golden\n")
        cfg = _write(tmp_path, "dart_test.yaml", _DART_TEST_YAML)
        with pytest.raises(pytest.fail.Exception, match="has no reason"):
            assert_partitions_reachable(runner_texts=[runner], declared_tags=cfg, allowed={"benchmark": "", "golden": "host PNGs"})
        with pytest.raises(pytest.fail.Exception, match="no longer excuses"):
            assert_partitions_reachable(runner_texts=[runner], declared_tags=cfg, allowed={"benchmark": "a", "golden": "b", "gone": "c"})
        assert_partitions_reachable(runner_texts=[runner], declared_tags=cfg, allowed={"benchmark": "a", "golden": "b"})
