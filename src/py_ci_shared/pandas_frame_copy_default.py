"""``pd.DataFrame(data, ...)`` over an existing array or dict of arrays, without ``copy=``: pandas copies the data.

pandas 3 copies an ndarray (and consolidates a dict of arrays into blocks, about twice) unless ``copy=False`` is passed. On a frame of a few GB the copy doubles the peak memory of the step that builds it: an MRMR fit
at 1M x 1000 died of it at three different call sites, one after another. The gate lists every ``pandas.DataFrame(...)`` call that is given data (a first positional argument or ``data=``) and neither a ``copy=`` keyword
nor a literal of its own rows (a list/tuple/set display, a constant, a comprehension, a dict whose values are all such literals, ``None``).

ADVISORY by default: most frames are small and the copy is harmless, and ``copy=False`` shares memory with the source, which is wrong when the caller will write to the source afterwards. ``assert_pandas_frame_copy_default``
WARNS (``PandasFrameCopyWarning``) per finding; ``advisory=False`` fails, or with *baseline_path* ratchets, so a large code base can freeze its count and let it only fall. A reviewed exception is
``# copy-ok: <reason>`` on the line of the call.

Usage in a consumer's meta test::

    from py_ci_shared.pandas_frame_copy_default import assert_pandas_frame_copy_default

    def test_pandas_frame_copy_default():
        assert_pandas_frame_copy_default("src", advisory=False, baseline_path="pandas_frame_copy_baseline.json")
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Collection
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, nodes_of, scan_python

__all__ = ["DEFAULT_RECORD_NAMES", "RULE", "SUPPRESSION", "PandasFrameCopyWarning", "assert_pandas_frame_copy_default", "find_pandas_frame_copy_default"]

RULE = "pandas-frame-copy-default"
SUPPRESSION = "# copy-ok:"
_LITERALS = (ast.List, ast.Tuple, ast.Set, ast.Constant, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.JoinedStr)


class PandasFrameCopyWarning(UserWarning):
    """One ``pandas.DataFrame`` call over existing data without ``copy=``, reported in advisory mode."""


def _literal_rows(node: ast.expr) -> bool:
    """The argument is a display of its own values, not an existing array: nothing to share."""
    if isinstance(node, _LITERALS):
        return True
    return isinstance(node, ast.Dict) and all(v is not None and isinstance(v, _LITERALS) for v in node.values)


DEFAULT_RECORD_NAMES = ("rows", "records")
_RECORD_BUILDERS = frozenset({"list", "tuple", "sorted", "reversed"})


def _record_like(node: ast.expr, record_names: "Collection[str]") -> bool:
    """The argument holds records (a list of row dicts or tuples), not an array: a name from *record_names*, or ``list(...)`` / ``tuple(...)`` / ``sorted(...)`` of something. Nothing is shared with them."""
    if isinstance(node, ast.Name):
        return node.id in record_names
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _RECORD_BUILDERS


def _data_argument(call: ast.Call) -> "ast.expr | None":
    """The ``data`` argument of the call, ``None`` when it has none."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "data":
            return kw.value
    return None


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: list[str], record_names: "Collection[str]" = DEFAULT_RECORD_NAMES) -> list[Finding]:
    """The findings in one parsed file."""
    out: list[Finding] = []
    for node in nodes_of(tree, ast.Call):
        if aliases.qualified_name(node.func) != "pandas.DataFrame":
            continue
        data = _data_argument(node)
        if data is None or _literal_rows(data) or _record_like(data, record_names) or any(kw.arg == "copy" for kw in node.keywords):
            continue
        if any(kw.arg is None for kw in node.keywords):  # **kwargs may carry copy=
            continue
        if 1 <= node.lineno <= len(lines) and SUPPRESSION in lines[node.lineno - 1]:
            continue
        out.append(
            Finding(
                rel,
                node.lineno,
                RULE,
                "pandas.DataFrame(...) over existing data without copy=: pandas copies the data (pass copy=False when the source is not written to afterwards)",
            )
        )
    return out


def find_pandas_frame_copy_default(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    record_names: "Collection[str]" = DEFAULT_RECORD_NAMES,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line. A first argument that is a name in *record_names* (default ``rows``, ``records``) or ``list(...)`` / ``tuple(...)`` / ``sorted(...)`` is a list of
    records, not an array, and is not reported.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines(), record_names))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_pandas_frame_copy_default(
    root: Union[str, Path],
    *,
    advisory: bool = True,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    record_names: "Collection[str]" = DEFAULT_RECORD_NAMES,
) -> None:
    """Warn on each finding (``advisory=True``, the default); with ``advisory=False`` fail on any finding, or on any finding *baseline_path* does not accept."""
    found = find_pandas_frame_copy_default(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, record_names=record_names)
    if advisory:
        for f in found:
            warnings.warn(f.render(), PandasFrameCopyWarning, stacklevel=2)
        return
    guidance = "pass copy=False (the source must not be written to afterwards), build the frame from a literal, or `# copy-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="pandas_frame_copy_default", refresh_command="PY_CI_SHARED_REFRESH=pandas_frame_copy_default")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} pandas-frame-copy-default finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
