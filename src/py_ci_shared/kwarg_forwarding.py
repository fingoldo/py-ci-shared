"""Optional arguments that a wrapper drops on the way to the function it wraps.

Three shapes, each found in one audit with a live defect behind it:

- **A variant drops its base's options.** ``fit_stacked`` and ``fit_with_stability_check`` call ``fit`` but neither accepted
  nor forwarded ``time_ordering`` / ``val_df`` / ``val_y``, so the variants silently ran the base without them.
  :func:`find_dropped_variant_params` flags every optional parameter of the base that the variant neither forwards nor passes
  through ``**kwargs``. A variant is a function whose name starts with ``<base>_`` and calls it, or one named in ``delegates``.
- **An argument is in scope but not passed.** The cross-target builder had ``sample_weight`` in scope and called
  ``from_nnls_stack`` without it. :func:`find_available_but_not_passed` flags a call to a known function that omits an
  optional parameter ``p`` while the caller has a parameter named ``p``.
- **A delegate loses private state.** A method built ``obj = SameClass(cfg)`` and called ``obj.fit(...)`` without copying the
  ``self._x`` attributes ``fit`` reads but never sets, so per-group delegates ran without their group ids.
  :func:`find_delegate_state_loss` flags each such attribute not assigned on the delegate before the call.

Functions are resolved within one file, or across files through ``delegates``: ``{"path::variant": "path::base"}``, and
``methods``: ``{"ClassName.method": "path::function"}`` for methods bound onto a class from another module.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

__all__ = ["ForwardingFinding", "find_available_but_not_passed", "find_delegate_state_loss", "find_dropped_variant_params"]


class ForwardingFinding:
    """One dropped argument: where, which function it should reach, and which parameter or attribute."""

    __slots__ = ("callee", "key", "kind", "lineno", "name", "path", "scope")

    def __init__(self, kind: str, path: str, scope: str, lineno: int, callee: str, name: str) -> None:
        self.kind = kind
        self.path = path
        self.scope = scope
        self.lineno = lineno
        self.callee = callee
        self.name = name
        self.key = f"{kind}:{path}::{scope}->{callee}:{name}"

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.scope} -> {self.callee}: {self.kind} '{self.name}'"


_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parse_one(path: Path, root: Path) -> tuple[str, ast.Module] | None:
    """``(repo-relative posix path, module)``, or None when the file does not parse."""
    try:
        return Path(path).resolve().relative_to(root).as_posix(), ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def _parse(files: Iterable[Path], repo_root: Path) -> dict[str, ast.Module]:
    """``{repo-relative posix path: parsed module}``, skipping files that do not parse."""
    root = Path(repo_root).resolve()
    return dict(filter(None, (_parse_one(p, root) for p in files)))


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """``{name or Class.name: def}`` for module-level functions and class methods."""
    out: dict[str, ast.FunctionDef] = {n.name: n for n in tree.body if isinstance(n, _FUNC_TYPES)}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        out.update({f"{cls.name}.{i.name}": i for i in cls.body if isinstance(i, _FUNC_TYPES)})
    return out


def _positional(fn: ast.FunctionDef) -> list[str]:
    """Positional parameter names, without ``self`` / ``cls``."""
    return [p.arg for p in fn.args.posonlyargs + fn.args.args if p.arg not in ("self", "cls")]


def _defaulted(fn: ast.FunctionDef) -> list[tuple[str, ast.expr]]:
    """``(name, default)`` for every parameter with a default, positional and keyword-only, without ``self`` / ``cls``."""
    a = fn.args
    pos_all = a.posonlyargs + a.args
    pos = list(zip(pos_all[len(pos_all) - len(a.defaults):], a.defaults))
    kwo = [(p, d) for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
    return [(p.arg, d) for p, d in pos + kwo if p.arg not in ("self", "cls")]


def _optional_params(fn: ast.FunctionDef) -> list[str]:
    """Parameters with a default."""
    return [p for p, _ in _defaulted(fn)]


def _none_default_params(fn: ast.FunctionDef) -> list[str]:
    """Optional parameters whose default is ``None``."""
    return [p for p, d in _defaulted(fn) if isinstance(d, ast.Constant) and d.value is None]


def _params(fn: ast.FunctionDef) -> set[str]:
    """Every named parameter of ``fn``."""
    a = fn.args
    return {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}


def _is_self_call(f: ast.expr, name: str | None = None) -> bool:
    """``self.name(...)`` / ``cls.name(...)`` (any name when ``name`` is None)."""
    return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("self", "cls") and (name is None or f.attr == name)


def _calls_to(fn: ast.FunctionDef, name: str) -> list[ast.Call]:
    """Calls inside ``fn`` to ``name(...)``, ``self.name(...)`` or ``cls.name(...)``."""
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == name) or _is_self_call(n.func, name))]


def _passed(call: ast.Call, callee: ast.FunctionDef) -> tuple[set[str], bool]:
    """``(parameter names the call supplies by keyword or position, whether it forwards *args / **kwargs)``."""
    names = {k.arg for k in call.keywords if k.arg is not None} | set(_positional(callee)[: len(call.args)])
    star = any(k.arg is None for k in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args)
    return names, star


def _class_bases(tree: ast.Module) -> dict[str, list[str]]:
    """``{class name: [base class names defined in the same file]}``."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    names = {c.name for c in classes}
    return {c.name: [b.id for b in c.bases if isinstance(b, ast.Name) and b.id in names] for c in classes}


def _resolve_call(call: ast.Call, owner: str, tree: ast.Module, funcs: dict[str, ast.FunctionDef]) -> tuple[str, ast.FunctionDef] | None:
    """The same-file def a call reaches: ``self.m()`` / ``cls.m()`` through the owner class and its same-file bases, a bare
    ``f()`` to a module-level function."""
    f = call.func
    if isinstance(f, ast.Name):
        return (f.id, funcs[f.id]) if f.id in funcs else None
    if not (_is_self_call(f) and owner):
        return None
    bases = _class_bases(tree)
    todo, seen = [owner], set()
    while todo:
        c = todo.pop(0)
        if c not in seen:
            seen.add(c)
            if f"{c}.{f.attr}" in funcs:
                return f"{c}.{f.attr}", funcs[f"{c}.{f.attr}"]
            todo.extend(bases.get(c, []))
    return None


def _resolve(trees: dict[str, ast.Module], ref: str) -> ast.FunctionDef | None:
    """The def named by ``path::name``."""
    path, _, name = ref.partition("::")
    tree = trees.get(path)
    return None if tree is None else _functions(tree).get(name)


def _short(ref: str) -> str:
    """The bare function name of ``path::Class.name``."""
    return ref.rpartition("::")[2].rpartition(".")[2]


# ---------------------------------------------------------------------------
# Variants that drop their base's options
# ---------------------------------------------------------------------------


def _variant_pairs(trees: dict[str, ast.Module], delegates: Mapping[str, str]):
    """``(path, variant name, variant def, base ref, base def)``: same-file ``<base>_...`` callers, then the registered ones."""
    for path, tree in trees.items():
        funcs = _functions(tree)
        for fname, fn in funcs.items():
            owner, _, short = fname.rpartition(".")
            for node in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                hit = _resolve_call(node, owner, tree, funcs)
                if hit is not None and hit[1] is not fn and short.startswith(hit[0].rpartition(".")[2] + "_"):
                    yield path, fname, fn, f"{path}::{hit[0]}", hit[1]
                    break
    for variant, base in delegates.items():
        fn, g = _resolve(trees, variant), _resolve(trees, base)
        if fn is not None and g is not None:
            yield variant.partition("::")[0], variant.partition("::")[2], fn, base, g


def find_dropped_variant_params(files: Iterable[Path], repo_root: Path, delegates: Mapping[str, str] | None = None) -> list[ForwardingFinding]:
    """Optional parameters of a base function its variant neither forwards nor passes through ``**kwargs``."""
    trees = _parse(files, repo_root)
    out: list[ForwardingFinding] = []
    for path, fname, fn, base_ref, g in _variant_pairs(trees, delegates or {}):
        calls = _calls_to(fn, _short(base_ref))
        if not calls:
            continue
        passed = [_passed(c, g) for c in calls]
        if any(star for _, star in passed):
            continue
        supplied = set().union(*(names for names, _ in passed))
        out.extend(ForwardingFinding("dropped_variant_param", path, fname, calls[0].lineno, base_ref, p)
                   for p in _optional_params(g) if p not in supplied)
    return out


# ---------------------------------------------------------------------------
# Arguments in scope but not passed
# ---------------------------------------------------------------------------


def _call_target(node: ast.Call, path: str, fname: str, owner: str, tree, funcs, trees, delegates) -> tuple[str, ast.FunctionDef] | None:
    """The def a call reaches: a same-file function, or a registered variant's base through ``self.<base>(...)``."""
    hit = _resolve_call(node, owner, tree, funcs)
    if hit is not None:
        return f"{path}::{hit[0]}", hit[1]
    base_ref = delegates.get(f"{path}::{fname}")
    if base_ref and _is_self_call(node.func, _short(base_ref)):
        base = _resolve(trees, base_ref)
        return (base_ref, base) if base is not None else None
    return None


def find_available_but_not_passed(files: Iterable[Path], repo_root: Path, delegates: Mapping[str, str] | None = None) -> list[ForwardingFinding]:
    """Calls to a same-file function (or a registered variant's base) that omit an optional ``None``-default parameter ``p``
    while the caller has a parameter named ``p``."""
    trees = _parse(files, repo_root)
    out: list[ForwardingFinding] = []
    for path, tree in trees.items():
        funcs = _functions(tree)
        for fname, fn in funcs.items():
            have = _params(fn)
            for node in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                target = _call_target(node, path, fname, fname.rpartition(".")[0], tree, funcs, trees, delegates or {})
                if target is None or target[1] is fn:
                    continue
                names, star = _passed(node, target[1])
                if not star:
                    out.extend(ForwardingFinding("available_not_passed", path, fname, node.lineno, target[0], p)
                               for p in _none_default_params(target[1]) if p in have and p not in names)
    return out


# ---------------------------------------------------------------------------
# Delegates that lose injected state
# ---------------------------------------------------------------------------


def _self_attrs(fn: ast.FunctionDef, *, stored: bool) -> set[str]:
    """Private ``self._x`` attributes ``fn`` stores (``stored=True``) or reads, including ``getattr(self, "_x", ...)``."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr.startswith("_")
                and not node.attr.startswith("__") and isinstance(node.ctx, ast.Store) == stored):
            out.add(node.attr)
        elif not stored and isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr" and len(node.args) >= 2:
            obj, attr = node.args[0], node.args[1]
            if isinstance(obj, ast.Name) and obj.id == "self" and isinstance(attr, ast.Constant) and str(attr.value).startswith("_"):
                out.add(str(attr.value))
    return out


class _Index:
    """Cross-file lookups the delegate check needs: module functions by name, import aliases, methods bound after the class
    body (``Cls.m = alias``), functions written as methods (``def f(self: "Cls")``), and private attributes injected from
    outside an object (``obj._x = ...`` or ``setattr(obj, "_x", ...)`` on anything but ``self``)."""

    def __init__(self, trees: dict[str, ast.Module], methods: Mapping[str, str]) -> None:
        self.trees = trees
        self.methods = methods
        self.top: dict[str, list[ast.FunctionDef]] = {}
        for t in trees.values():
            for node in (n for n in t.body if isinstance(n, _FUNC_TYPES)):
                self.top.setdefault(node.name, []).append(node)
        self.aliases = {a.asname: a.name for t in trees.values() for n in ast.walk(t) if isinstance(n, ast.ImportFrom) for a in n.names if a.asname}
        self.bound = dict(self._bound_methods())
        self.injected = self._injected()
        self.fn_path = {id(n): p for p, t in trees.items() for n in ast.walk(t) if isinstance(n, _FUNC_TYPES)}

    def unique(self, name: str) -> ast.FunctionDef | None:
        """The one module-level function called ``name`` (through its import alias), or None when absent or ambiguous."""
        cands = self.top.get(self.aliases.get(name, name), [])
        return cands[0] if len(cands) == 1 else None

    def _bound_methods(self):
        """``(Cls.m, def)`` for every module-level ``Cls.m = f``."""
        for t in self.trees.values():
            for node in t.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute)
                        and isinstance(node.targets[0].value, ast.Name) and isinstance(node.value, ast.Name)):
                    fn = self.unique(node.value.id)
                    if fn is not None:
                        yield f"{node.targets[0].value.id}.{node.targets[0].attr}", fn

    def _injected(self) -> set[str]:
        """Private attribute names assigned on some object other than ``self``."""
        out: set[str] = set()
        for t in self.trees.values():
            for n in ast.walk(t):
                if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and n.attr.startswith("_") and not n.attr.startswith("__")
                        and not (isinstance(n.value, ast.Name) and n.value.id == "self")):
                    out.add(n.attr)
                elif (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "setattr" and len(n.args) >= 2
                      and isinstance(n.args[1], ast.Constant) and str(n.args[1].value).startswith("_")):
                    out.add(str(n.args[1].value))
        return out

    def method(self, cls: str, name: str, tree: ast.Module) -> ast.FunctionDef | None:
        """``cls.name`` through ``methods``, the class body, or a post-hoc binding."""
        ref = self.methods.get(f"{cls}.{name}")
        if ref:
            return _resolve(self.trees, ref)
        return _functions(tree).get(f"{cls}.{name}") or self.bound.get(f"{cls}.{name}")

    def class_methods(self, cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
        """Every def that runs as a method of ``cls``: body, ``methods``, post-hoc bindings and ``self: "Cls"`` functions."""
        out = {f"{cls.name}.{i.name}": i for i in cls.body if isinstance(i, _FUNC_TYPES)}
        for k, v in self.methods.items():
            if k.startswith(cls.name + ".") and _resolve(self.trees, v) is not None:
                out[k] = _resolve(self.trees, v)
        out.update({k: v for k, v in self.bound.items() if k.startswith(cls.name + ".")})
        for fns in self.top.values():
            for node in fns:
                ann = node.args.args[0].annotation if node.args.args and node.args.args[0].arg == "self" else None
                if (ann.value if isinstance(ann, ast.Constant) else getattr(ann, "id", None)) == cls.name:
                    out.setdefault(f"{cls.name}.{node.name}", node)
        return out

    def reachable(self, entry: ast.FunctionDef, cls: str, tree: ast.Module, depth: int = 6) -> list[ast.FunctionDef]:
        """``entry`` plus what it reaches with the same object (``self.m()``, ``f(self, ...)``), up to ``depth`` calls deep."""
        seen: list[ast.FunctionDef] = []
        frontier = [entry]
        for _ in range(depth):
            fresh = [fn for fn in frontier if not any(fn is s for s in seen)]
            seen.extend(fresh)
            frontier = [nxt for fn in fresh for nxt in self._callees(fn, cls, tree)]
        return seen

    def _callees(self, fn: ast.FunctionDef, cls: str, tree: ast.Module):
        """Defs ``fn`` calls with its own object."""
        for node in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            f = node.func
            if isinstance(f, ast.Attribute) and getattr(f.value, "id", None) == "self":
                target = self.method(cls, f.attr, tree)
            elif isinstance(f, ast.Name) and node.args and getattr(node.args[0], "id", None) == "self":
                target = self.unique(f.id)
            else:
                target = None
            if target is not None:
                yield target


def _loop_constants(fn: ast.FunctionDef) -> dict[str, set[str]]:
    """Names a for-loop binds to a tuple / list of string constants: ``for a in ("_x", "_y"): ...``."""
    out: dict[str, set[str]] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and isinstance(n.iter, (ast.Tuple, ast.List)):
            out.setdefault(n.target.id, set()).update(str(e.value) for e in n.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return out


def _delegate_vars(fn: ast.FunctionDef, cls: str) -> list[str]:
    """Local names bound to a new instance of ``cls`` (directly or through an import alias) inside ``fn``."""
    names = {cls} | {a.asname for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names if a.name == cls and a.asname}
    return [n.targets[0].id for n in ast.walk(fn) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) in names]


def _set_on(fn: ast.FunctionDef, var: str) -> set[str]:
    """Attributes ``fn`` sets on ``var``: ``var._x = ...``, ``setattr(var, "_x", ...)`` and a copy loop over constant names."""
    loops = _loop_constants(fn)
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and getattr(node.value, "id", None) == var and isinstance(node.ctx, ast.Store):
            out.add(node.attr)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr" and len(node.args) >= 2 and getattr(node.args[0], "id", None) == var:
            key = node.args[1]
            out |= {str(key.value)} if isinstance(key, ast.Constant) else loops.get(getattr(key, "id", ""), set())
    return out


def _lost_state(idx: _Index, cls: ast.ClassDef, tree: ast.Module, fname: str, fn: ast.FunctionDef, method_names: set[str]):
    """Findings for each delegate of ``cls`` created in ``fn`` whose called method needs injected state it was not given."""
    init = idx.method(cls.name, "__init__", tree)
    init_set = _self_attrs(init, stored=True) if init else set()
    for var in _delegate_vars(fn, cls.name):
        given = _set_on(fn, var)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and getattr(node.func.value, "id", None) == var):
                continue
            m = idx.method(cls.name, node.func.attr, tree)
            if m is None:
                continue
            reach = idx.reachable(m, cls.name, tree)
            external = set().union(*(_self_attrs(r, stored=False) for r in reach)) - set().union(*(_self_attrs(r, stored=True) for r in reach)) - init_set
            # Only state injected from outside counts: an attribute nothing sets is an optional lookup, not state to lose.
            for attr in sorted((external & idx.injected) - given - method_names):
                yield ForwardingFinding("delegate_state_loss", idx.fn_path.get(id(fn), ""), fname, node.lineno, f"{cls.name}.{node.func.attr}", attr)


def find_delegate_state_loss(files: Iterable[Path], repo_root: Path, methods: Mapping[str, str] | None = None) -> list[ForwardingFinding]:
    """Inside a method of class ``C``: ``obj = C(...)`` then ``obj.m(...)`` without first setting each private attribute that
    ``C.m`` (or anything it calls with the object) reads, that nothing on its path or in ``__init__`` sets, and that some code
    injects onto the object from outside."""
    trees = _parse(files, repo_root)
    idx = _Index(trees, methods or {})
    out: list[ForwardingFinding] = []
    for tree in trees.values():
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            funcs = idx.class_methods(cls)
            method_names = {k.rpartition(".")[2] for k in funcs}
            for fname, fn in funcs.items():
                out.extend(_lost_state(idx, cls, tree, fname, fn, method_names))
    return out
