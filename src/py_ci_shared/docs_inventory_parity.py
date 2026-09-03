"""Shared check: a documented inventory is computed from the thing it documents.

``readme_env_var_parity.py`` already does this for environment variables. This module is
the same idea applied to the three inventories that drifted hardest in one consuming repo's
2026-09-02 documentation audit -- eleven findings between them, every one mechanical:

* **(a) Extras groups.** A README install block listed three packages that had since moved
  to core and omitted the group's two heaviest members; another group's bullet omitted two
  of its seven members; eight of sixteen declared groups appeared in the install block not
  at all; and the group advertised as the "full install (recommended)" silently omitted
  four groups, so the recommended install could not import three shipped modules.
* **(b) Module coverage.** Twelve shipped modules appeared in no documentation at all,
  including a whole subpackage and a CHANGELOG headline feature.
* **(c) Named things that do not exist.** CONTRIBUTING told contributors to use a pytest
  marker that is a collection ERROR under ``--strict-markers``, and pointed at a benchmark
  path that had moved.

Rule (b) is the risky one and is reported separately so a consumer can warn on it while
blocking (a) and (c): a deliberately-undocumented private helper is indistinguishable from
an oversight without an explicit by-design list.

``pytest``/``tomllib`` are imported lazily, matching this package's other modules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

# Package names as they appear in a dependency spec, stripped of version/marker/extras.
_REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# A self-referential extras spec: `mypkg[a,b]`.
_SELF_EXTRA_RE = re.compile(r"^[A-Za-z0-9._-]+\[([^\]]+)\]$")
# Words in a prose description that could be package names.
_PROSE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,}")
# `@pytest.mark.<name>` mentioned anywhere in prose.
_MARKER_MENTION_RE = re.compile(r"@pytest\.mark\.(\w+)")
# A backtick-quoted token that looks like a repo path or glob (it contains a separator and
# no whitespace). Deliberately narrow: bare filename mentions in prose are far too noisy.
_BACKTICK_PATH_RE = re.compile(r"`([\w.*/\[\]{}-]*/[\w.*/\[\]{}-]*)`")
_PYTEST_BUILTIN_MARKERS = frozenset({"skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings", "tryfirst", "trylast", "no_cover"})


def _normalize(name: str) -> str:
    """PEP 503 name normalization, minus the variant suffixes wheels add."""
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    return normalized[: -len("-binary")] if normalized.endswith("-binary") else normalized


def _requirement_name(spec: str) -> str:
    match = _REQUIREMENT_NAME_RE.match(spec.strip())
    return match.group(1) if match else spec.strip()


def resolve_extras_group(optional_dependencies: dict[str, list[str]], group: str, _seen: set[str] | None = None) -> set[str]:
    """Return the fully-resolved, normalized package set for one extras group.

    Self-referential specs (``mypkg[a,b]``) are followed transitively, which is the whole
    point: a README calling ``[all,dev]`` the recommended install is making a claim about
    what ``[all]`` RESOLVES to, not about the one line that declares it.
    """
    seen = _seen if _seen is not None else set()
    if group in seen or group not in optional_dependencies:
        return set()
    seen.add(group)
    members: set[str] = set()
    for spec in optional_dependencies[group]:
        head = spec.split(";")[0].strip()
        self_extra = _SELF_EXTRA_RE.match(head)
        if self_extra:
            for referenced in self_extra.group(1).split(","):
                members |= resolve_extras_group(optional_dependencies, referenced.strip(), seen)
        else:
            members.add(_normalize(_requirement_name(head)))
    return members


def find_extras_documentation_drift(
    pyproject_path: Path,
    doc_path: Path,
    bullet_pattern: str,
    *,
    ignore_packages: Iterable[str] = (),
    undocumented_groups: Iterable[str] = (),
) -> list[str]:
    """Return one message per extras group that the docs describe wrongly or not at all.

    Args:
        pyproject_path: source of truth -- ``[project.optional-dependencies]``.
        doc_path: the prose file carrying the install block.
        bullet_pattern: a regex with two capture groups, ``(group_name)`` and
            ``(description)``, matching one documented extras bullet.
        ignore_packages: packages deliberately not enumerated in prose -- a shared
            transitive pin that appears in most groups and would clutter every bullet.
        undocumented_groups: groups deliberately absent from the install block, typically
            because a superset group documents them.

    Reports three drift shapes: a group with no bullet, a bullet naming a package the group
    does not contain (the "now core, still advertised" shape), and a bullet omitting a
    package the group does contain (the "two heaviest members missing" shape).
    """
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    optional_dependencies = data.get("project", {}).get("optional-dependencies", {})
    ignored = {_normalize(name) for name in ignore_packages}
    exempt_groups = set(undocumented_groups)

    documented: dict[str, str] = {}
    for match in re.finditer(bullet_pattern, doc_path.read_text(encoding="utf-8")):
        documented[match.group(1)] = match.group(2)

    problems: list[str] = []
    for group in sorted(optional_dependencies):
        if group in exempt_groups:
            continue
        if group not in documented:
            problems.append(f"{doc_path.name}: extras group [{group}] is declared in pyproject.toml but documented nowhere in the install block")
            continue
        actual = resolve_extras_group(optional_dependencies, group) - ignored
        prose_tokens = {_normalize(token) for token in _PROSE_TOKEN_RE.findall(documented[group])}
        missing = sorted(name for name in actual if name not in prose_tokens)
        # A prose token counts as a package claim only if it names a package declared
        # SOMEWHERE in the file's extras -- otherwise every ordinary English word in the
        # description would read as a phantom dependency.
        every_declared = {name for other in optional_dependencies for name in resolve_extras_group(optional_dependencies, other)}
        phantom = sorted(token for token in prose_tokens if token in every_declared and token not in actual and token not in ignored)
        if missing:
            problems.append(f"{doc_path.name}: [{group}] description omits {missing} (declared in pyproject.toml)")
        if phantom:
            problems.append(f"{doc_path.name}: [{group}] description names {phantom}, which the group does not contain")
    problems.extend(
        f"{doc_path.name}: install block documents extras group [{group}], which pyproject.toml does not declare"
        for group in sorted(documented)
        if group not in optional_dependencies
    )
    return problems


def find_aggregate_group_drift(pyproject_path: Path, doc_path: Path, pattern: str) -> list[str]:
    """Return one message if the docs' stated composition of an aggregate extras group
    disagrees with what the group actually references.

    An aggregate group (``all = ["mypkg[a,b,c]"]``) is documented by naming its member
    GROUPS, not its packages, so the package-level rule above cannot check it -- and it is
    the group a README calls the "full install (recommended)", which makes a silent omission
    there the most expensive documentation drift in the file.

    Args:
        pattern: a regex with two capture groups, ``(group_name)`` and a comma-separated
            ``(member_group_list)``, matching the prose that states the composition.
    """
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    optional_dependencies = data.get("project", {}).get("optional-dependencies", {})
    problems: list[str] = []
    for match in re.finditer(pattern, doc_path.read_text(encoding="utf-8")):
        group, stated_list = match.group(1), match.group(2)
        stated = {name.strip() for name in stated_list.split(",") if name.strip()}
        actual: set[str] = set()
        for spec in optional_dependencies.get(group, []):
            self_extra = _SELF_EXTRA_RE.match(spec.split(";")[0].strip())
            if self_extra:
                actual |= {name.strip() for name in self_extra.group(1).split(",")}
        if stated != actual:
            problems.append(f"{doc_path.name}: [{group}] is documented as {sorted(stated)} but pyproject.toml resolves it to {sorted(actual)}")
    return problems


def find_undocumented_modules(
    package_dir: Path,
    doc_paths: Sequence[Path],
    *,
    undocumented_by_design: Iterable[str] = (),
) -> list[str]:
    """Return the dotted name of every shipped module mentioned in none of ``doc_paths``.

    ``__init__.py`` files are represented by their package's dotted name. A module counts as
    documented if either its dotted name or its bare stem appears anywhere in any doc file --
    deliberately generous, because module-orientation docs write ``pythonlib.py`` and
    ``gpu_dispatch`` rather than fully-qualified paths, and the failure being caught is
    "documented NOWHERE", not "documented thinly". Private modules (a leading underscore on
    any path segment) and ``__main__`` are skipped: they are implementation detail of the
    package that documents them.
    """
    exempt = set(undocumented_by_design)
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in doc_paths if path.is_file())
    documented_words = set(re.findall(r"[A-Za-z_][\w.]*", corpus))
    package_name = package_dir.name
    undocumented: list[str] = []
    for module_path in sorted(package_dir.rglob("*.py")):
        relative = module_path.relative_to(package_dir)
        parts = list(relative.parts[:-1]) + ([] if relative.stem == "__init__" else [relative.stem])
        if not parts or any(part.startswith("_") for part in parts):
            continue
        dotted = ".".join([package_name, *parts])
        if dotted in exempt or dotted in corpus or parts[-1] in documented_words:
            continue
        # A module whose PARENT package is documented is covered at the granularity the docs
        # chose. Orientation docs describe a package and its purpose, not each of the files a
        # split produced; demanding a mention of every one of them turns this into noise
        # (measured: 121 findings instead of the 9 real gaps) and buries the module whose
        # WHOLE package is missing, which is the failure worth catching.
        parent = ".".join([package_name, *parts[:-1]])
        if len(parts) > 1 and (parent in corpus or parts[-2] in documented_words):
            continue
        undocumented.append(dotted)
    return undocumented


def find_phantom_doc_paths(
    doc_paths: Sequence[Path],
    repo_root: Path,
    *,
    search_roots: Sequence[Path] = (),
    ignore: Iterable[str] = (),
    recent_sections: Mapping[str, int] | None = None,
) -> list[str]:
    """Return one message per backtick-quoted repo path or glob in prose that matches nothing.

    Scoped to backticked tokens containing a ``/``: a bare filename mention in prose is
    normal English about a module, while an explicit path is a claim about the tree. A token
    additionally has to LOOK like a path -- carry a file extension, a trailing slash, or a
    glob character -- so that an ordinary slashed phrase (``try/except``, ``and/or``) is not
    read as a broken path.

    Args:
        search_roots: extra directories a documented path may be relative to, beyond
            ``repo_root``. Docs routinely write a path relative to the source package
            (``web/browser.py``) rather than to the repo, and both are legitimate.
        recent_sections: ``{filename: n}`` -- scan only the first ``n`` ``##`` sections of that
            document. For a CHANGELOG this is the difference between a useful check and an
            impossible one: an old entry names the tree AS IT WAS, so a refactor that moves a
            directory makes every entry that ever mentioned it fail, and the only way to go green
            is to rewrite history into something that did not happen. Measured on one consuming
            repo after its core extraction: 61 of 70 findings were changelog entries that were
            true on the day they were written. The newest section describes the tree that exists
            now and is worth checking; the ones below it are a record. Keyed by file NAME rather
            than applied to every doc, because a README's sections are all current and trimming
            them would quietly stop checking most of it.
    """
    ignored = set(ignore)
    roots = [repo_root, *search_roots]
    problems: list[str] = []
    for doc_path in doc_paths:
        if not doc_path.is_file():
            continue
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        keep = (recent_sections or {}).get(doc_path.name)
        if keep is not None:
            seen = 0
            for index, heading in enumerate(lines):
                if heading.startswith("## "):
                    seen += 1
                    if seen > keep:
                        lines = lines[:index]
                        break
        for line_number, line in enumerate(lines, start=1):
            for target in _BACKTICK_PATH_RE.findall(line):
                # An ignore entry ending in `/` reads as a directory and covers what is under it.
                # It was matched exactly, so `lib/shared/` in the list did not cover
                # `lib/shared/access/` and every path inside an ignored directory still reported.
                if (
                    target in ignored
                    or any(target.startswith(prefix) for prefix in ignored if prefix.endswith("/"))
                    or "://" in target
                    or target.startswith(("-", "/"))
                ):
                    continue
                if not (target.endswith("/") or "*" in target or re.search(r"\.\w+$", target)):
                    continue
                if any((root / target).exists() or any(root.glob(target)) for root in roots):
                    continue
                # Repo-relative when possible: a project with both `README.md` and
                # `lib/src/README.md` gets two findings labelled `README.md:5` otherwise, and the
                # reader cannot tell which file to open.
                try:
                    label = doc_path.resolve().relative_to(repo_root.resolve()).as_posix()
                except (ValueError, OSError):
                    label = doc_path.name
                problems.append(f"{label}:{line_number}: `{target}` does not exist in the repo")
    return problems


def find_undeclared_markers(doc_paths: Sequence[Path], pyproject_path: Path) -> list[str]:
    """Return one message per ``@pytest.mark.<name>`` named in prose but never declared.

    Under ``--strict-markers`` an undeclared marker is a collection ERROR, so a contributor
    following the documentation cannot run the suite at all.
    """
    import tomllib

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    declared = {entry.split(":")[0].strip() for entry in data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])}
    declared |= _PYTEST_BUILTIN_MARKERS
    problems: list[str] = []
    for doc_path in doc_paths:
        if not doc_path.is_file():
            continue
        for line_number, line in enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1):
            problems.extend(
                f"{doc_path.name}:{line_number}: @pytest.mark.{marker} is documented but not declared in [tool.pytest.ini_options] markers -- a collection ERROR under --strict-markers"
                for marker in _MARKER_MENTION_RE.findall(line)
                if marker not in declared
            )
    return problems


def assert_no_inventory_drift(problems: Sequence[str], what: str) -> None:
    """Fail with every problem listed, or do nothing. Shared by all four rules above."""
    import pytest

    if problems:
        pytest.fail(f"{len(problems)} {what}:\n  " + "\n  ".join(problems))
