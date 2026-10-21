"""Shared base of the code-audit detectors: the Finding record, the file walk, the exemption marker and the parse cache.

Moved here from pyutilz.dev.code_audit so that the rules and the gates that run them live in one package. pyutilz keeps
a re-exporting shim at the old path. The module is stdlib-only, so py-ci-shared stays dependency-free.
"""
