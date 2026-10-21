Consumer inventory, Phase 0 of the code-audit consolidation plan (2026-10-21).

Method: every repository of the fingoldo account (51 listed; 50 scanned, one has no tree) was searched by its file tree for
the names `code_audit_meta`, `pyutilz.dev.code_audit`, `py_ci_shared`, `py-ci-shared`, `pyutilz`. Python files, pyproject.toml,
requirements, conftest and test_meta files were read in full; workflow files were read for the py-ci-shared references.
GitHub code search does not index the private repositories, so the scan reads the trees directly.

Uses `code_audit` (product or test code imports the audit names):
- fingoldo/pyutilz (the library itself; the detectors)
- fingoldo/mlframe (public): tests/test_meta/conftest.py imports code_audit_meta
- fingoldo/autopsia (private): tests/test_meta/_shared.py imports pyutilz.dev.code_audit
- fingoldo/glossum_backend_scripts (private): tests/test_meta/conftest.py imports code_audit_meta
- fingoldo/llm_bench (public): tests/test_meta/conftest.py and test_code_audit_baseline.py import code_audit_meta
- fingoldo/dash_app_core (private): tests/test_meta/test_code_audit_baseline.py imports code_audit_meta
- fingoldo/algopacksimple (private): conftest.py imports code_audit_meta; pyproject pins py-ci-shared at 9d0cc27 (v1.22.4)

Uses py-ci-shared gates only (no code_audit yet; to be wired per the owner's decision that code_audit is used everywhere):
- fingoldo/social (private): workflows use py-ci-shared's reusable workflows; no Python tests tree at the root
- fingoldo/claude-usage-notifier (public): pyproject pins py-ci-shared at fd2f017; tests/test_audit_rounds_are_countable.py
- fingoldo/flutter_uptime_monitor (private): pyproject uses py-ci-shared's ruff base config; tests/test_audit_rounds_are_countable.py

Not a consumer: fingoldo/fazwaz_scraper (requirements mention pyutilz for runtime code only).
Not scanned: fingoldo/webproducts (no tree returned).

Corrections to earlier statements:
- algopacksimple is pinned to one commit everywhere (pyproject and workflows). It is not floating. It is behind: v1.22.4, while the
  current release is 1.23.1.
- The number of code_audit consumers is six (plus pyutilz itself), not four, and the seven in the docstring is not confirmed.
