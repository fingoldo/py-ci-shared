"""Shared core every py-ci-shared gate builds on: source reading, corpus, scan, baseline, refresh, aliases, findings.

See ``audits/implemented/2026-09-24/dispositions/core.md`` (section ``_core API``) for the idiom a gate should use.
"""

from __future__ import annotations

from .aliases import ImportAliases, module_of, package_of, resolve_relative
from .baseline import (
    UNJUSTIFIED_MARKER,
    Baseline,
    BaselineOutcome,
    atomic_write_text,
    dump_json,
    growth_message,
    is_unjustified,
    load_json,
    shrink_only,
    write_ratchet,
)
from .corpus import DEFAULT_EXCLUDE, git_listing, iter_files, relative_posix
from .errors import (
    BaselineError,
    BaselineGrowthError,
    CoreError,
    CorpusError,
    EmptyScanError,
    SourceError,
    SourceParseError,
    SourceReadError,
    UnparsedFilesError,
)
from .findings import UNPARSED_RULE, Finding
from .git import GitError, git_output, git_text, run_git
from .node_index import TreeVisitor, child_nodes, nodes_of, parent_map, parent_of, tree_cached, tree_memo, walk
from .refresh import ENV_VAR as REFRESH_ENV_VAR
from .refresh import GENERIC_OPTION as REFRESH_OPTION
from .refresh import GROW_ENV_VAR as REFRESH_GROW_ENV_VAR
from .refresh import GROW_OPTION as REFRESH_GROW_OPTION
from .refresh import grow_requested, refresh_requested, register_refresh_options
from .scan import ParsedFile, ScanResult, SourceProblem, scan_python
from .scopes import Scope, iter_scopes, qualified_functions, scope_nodes
from .source import clear_parse_cache, parse_file, parse_source, parse_text, read_source

__all__ = [
    "DEFAULT_EXCLUDE",
    "REFRESH_ENV_VAR",
    "REFRESH_GROW_ENV_VAR",
    "REFRESH_GROW_OPTION",
    "REFRESH_OPTION",
    "UNJUSTIFIED_MARKER",
    "UNPARSED_RULE",
    "Baseline",
    "BaselineError",
    "BaselineGrowthError",
    "BaselineOutcome",
    "CoreError",
    "CorpusError",
    "EmptyScanError",
    "Finding",
    "GitError",
    "ImportAliases",
    "ParsedFile",
    "ScanResult",
    "Scope",
    "SourceError",
    "SourceParseError",
    "SourceProblem",
    "SourceReadError",
    "TreeVisitor",
    "UnparsedFilesError",
    "atomic_write_text",
    "child_nodes",
    "clear_parse_cache",
    "nodes_of",
    "tree_cached",
    "tree_memo",
    "walk",
    "dump_json",
    "git_listing",
    "git_output",
    "git_text",
    "grow_requested",
    "growth_message",
    "is_unjustified",
    "iter_files",
    "iter_scopes",
    "load_json",
    "module_of",
    "package_of",
    "parent_map",
    "parent_of",
    "parse_file",
    "parse_source",
    "parse_text",
    "read_source",
    "refresh_requested",
    "qualified_functions",
    "register_refresh_options",
    "scope_nodes",
    "relative_posix",
    "resolve_relative",
    "run_git",
    "scan_python",
    "shrink_only",
    "write_ratchet",
]
