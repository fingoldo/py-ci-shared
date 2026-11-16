"""Three CUDA device idioms that are each one line, each with a documented replacement, and each a regression to catch the moment it is written.

* ``cuda-literal-device-id`` (hard): ``gpu_capability_summary(0)``, ``cp.cuda.Device(0)``, ``getDeviceProperties(0)``, ``setDevice(0)``, ``select_device(0)`` with an integer literal as the device.
  The launch geometry is derived from the device the kernel runs on; a literal 0 reads another card's limits on a multi-GPU host. A name, a call (``launch_device_id()``) or
  ``get_current_device()`` is not reported.

* ``cuda-cupy-small-power`` (hard): ``cp.power(x, e)`` / ``xp.power(x, e)`` with an exponent of 2, 3, -1, -2 or -3 (literal, positional or keyword). CUDA.md 19.7: the product
  (``x * x``) is the same value with fewer instructions and an exact rounding; ``power`` is kept for every other exponent. ``x ** 2`` on a cupy array is not statically typed and is not
  reported.

* ``cuda-direct-device-sort`` (hard): ``cp.sort(a, axis=0)`` / ``cp.argsort(a, axis=0)`` written directly. CUDA.md 16: above roughly 80000 rows the library sort along axis 0 is
  slower than the wrapper ``device_argsort`` / ``device_sort``. The row count is not known statically, so every explicit ``axis=0`` device sort is reported unless it is marked; the bodies of
  the wrappers themselves are exempt. A 1-D sort (no ``axis``) and ``axis=1`` (the contiguous direction) are not reported.

Suppression: ``# <word>-ok: <reason>`` on the first or last line of the call, with ``device-ok`` for the device id, ``power-ok`` for the power, ``sort-ok`` for the sort. A marker with an
empty reason does not suppress. The gate is hard: a finding fails the build.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_device_idioms import assert_cuda_device_idioms

    def test_cuda_device_idioms():
        assert_cuda_device_idioms("src", min_files=50)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, nodes_of, scan_python

__all__ = [
    "RULE_DEVICE_ID",
    "RULE_DIRECT_SORT",
    "RULE_SMALL_POWER",
    "assert_cuda_device_idioms",
    "find_cuda_device_idioms",
]

RULE_DEVICE_ID = "cuda-literal-device-id"
RULE_SMALL_POWER = "cuda-cupy-small-power"
RULE_DIRECT_SORT = "cuda-direct-device-sort"

_DEVICE_CALLS = frozenset({"gpu_capability_summary", "Device", "getDeviceProperties", "setDevice", "set_device", "select_device"})
_SMALL_POWERS = frozenset({2, 3, -1, -2, -3})
_ARRAY_MODULES = frozenset({"cp", "cupy", "xp"})
_WRAPPERS = frozenset({"device_argsort", "device_sort"})
_MARKERS = {RULE_DEVICE_ID: "device-ok:", RULE_SMALL_POWER: "power-ok:", RULE_DIRECT_SORT: "sort-ok:"}
_GUIDANCE = {
    RULE_DEVICE_ID: "take the device from the launch (launch_device_id()) rather than a literal, or mark `# device-ok: <reason>`",
    RULE_SMALL_POWER: "write the product (x * x, x * x * x, 1.0 / x) as CUDA.md 19.7 says, or mark `# power-ok: <reason>`",
    RULE_DIRECT_SORT: "use device_argsort / device_sort (CUDA.md 16), or mark `# sort-ok: <reason>` with the row count it is bounded by",
}


def _int(node: ast.AST) -> Optional[int]:
    """The integer a literal node holds, including a negated literal (``-3``); ``None`` otherwise (a bool is not an integer here)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, int):
        return -node.operand.value if not isinstance(node.operand.value, bool) else None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _called(call: ast.Call) -> Optional[str]:
    """The last name of the callee (``cp.cuda.Device`` -> ``Device``), or ``None`` for a call through a subscript or a call result."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_of(call: ast.Call) -> Optional[str]:
    """The name at the head of an attribute call (``cp`` for ``cp.sort``), or ``None``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _device_id_hit(call: ast.Call) -> bool:
    return _called(call) in _DEVICE_CALLS and bool(call.args) and _int(call.args[0]) is not None


def _power_hit(call: ast.Call) -> bool:
    if _called(call) != "power" or _module_of(call) not in _ARRAY_MODULES:
        return False
    exponent = call.args[1] if len(call.args) >= 2 else next((k.value for k in call.keywords if k.arg == "exponent"), None)
    return exponent is not None and _int(exponent) in _SMALL_POWERS


def _sort_hit(call: ast.Call) -> bool:
    if _called(call) not in {"sort", "argsort"} or _module_of(call) not in _ARRAY_MODULES:
        return False
    return any(k.arg == "axis" and _int(k.value) == 0 for k in call.keywords)


_RULES = (
    (RULE_DEVICE_ID, _device_id_hit),
    (RULE_SMALL_POWER, _power_hit),
    (RULE_DIRECT_SORT, _sort_hit),
)


def _marked(rule: str, call: ast.Call, lines: "list[str]") -> bool:
    """A ``# <word>-ok: <reason>`` marker with a non-empty reason on the first or last line of the call."""
    marker = re.compile(re.escape(_MARKERS[rule]) + r"\s*[^\s#]")
    for number in {call.lineno, getattr(call, "end_lineno", None) or call.lineno}:
        if 0 < number <= len(lines):
            line = lines[number - 1]
            if "#" in line and marker.search(line[line.index("#") :]):
                return True
    return False


def _findings_in(tree: ast.Module, rel: str, lines: "list[str]") -> "list[Finding]":
    wrapper_calls: "set[int]" = set()
    for fn in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        if fn.name in _WRAPPERS:
            wrapper_calls.update(id(c) for c in nodes_of(fn, ast.Call))
    out: "list[Finding]" = []
    for call in nodes_of(tree, ast.Call):
        for rule, hit in _RULES:
            if rule == RULE_DIRECT_SORT and id(call) in wrapper_calls:
                continue
            if hit(call) and not _marked(rule, call, lines):
                out.append(Finding(rel, call.lineno, rule, f"{_describe(call, rule)}: {_GUIDANCE[rule]}"))
    return out


def _describe(call: ast.Call, rule: str) -> str:
    name = _called(call) or "call"
    return {
        RULE_DEVICE_ID: f"{name}(<literal device>)",
        RULE_SMALL_POWER: f"power with a small integer exponent ({name})",
        RULE_DIRECT_SORT: f"direct axis-0 device {name}",
    }[rule]


def find_cuda_device_idioms(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every literal device id, small-integer cupy power and direct axis-0 device sort under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*).
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_device_idioms(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding: this gate is hard, there is no baseline and no advisory mode."""
    found = find_cuda_device_idioms(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(f"{len(found)} CUDA device idiom(s) to fix or mark:\n  " + "\n  ".join(f.render() for f in found))
