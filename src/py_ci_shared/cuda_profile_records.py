"""Every module that defines a CUDA kernel has a profile record with the fields the contract asks for, and a record may only get more complete.

The CUDA contract (point 5, "profile it twice and keep the numbers") says that a GPU module has a record ``<profiles_dir>/<module>.md`` with the host profile (``cProfile``), a GPU profiler run
(``nsys`` / ``ncu`` / ``nvprof``), the kernel launches per call (fusion), the occupancy and the whole-GPU throughput / utilization / bandwidth. Until this gate that was a regex over a hand-kept
module list inside one repository (mlframe ``fe_gpu_coverage_inventory.contract_violations``), so a repository that adopted the contract had to copy a 475-line inventory. This is the library form.

* The kernel-bearing modules are found by AST, never from a list: every file under *root* holding a kernel source (:func:`py_ci_shared.cuda_kernel_sources.find_cuda_kernel_sources`; ``_benchmarks``
  excluded by default). A CPU-only module is not asked for a record.
* ``cuda-profile-record-missing`` (advisory, then a ratchet): the module has no ``<profiles_dir>/<stem>.md``. ``family_map`` maps a carved module's stem to the stem whose record covers it.
* ``cuda-profile-record-incomplete:<field>`` (advisory, then a ratchet): the record exists but no ``required`` pattern ``<field>`` matches its text. One finding per record and field, so fixing one field
  shrinks a baseline by exactly one entry. The message names the record, the field and the pattern. ``required`` is a parameter; :data:`DEFAULT_REQUIRED` is mlframe's five fields,
  :data:`EXTENDED_REQUIRED` adds the occupancy limiter, SM throughput, DRAM bandwidth and the kernel-versus-copy share of the wall time (regex over prose: it checks that the topic is mentioned,
  never the numbers).
* ``exempt`` is ``{stem: reason}``; an empty reason raises, because an exemption without a review is a hole. There is no in-source marker: the exemption is an entry with a reason.
* Ratchet: ``baseline_path`` accepts the known-missing and known-incomplete records; the baseline may only shrink (``PY_CI_SHARED_REFRESH=cuda_profile_records`` refuses growth).

Measured and not: the presence of the record and of each field's wording are checked. The numbers in a record, and that they were taken on the current code, are not.
Document: CUDA.md contract point 5 and section 10 item 7 of the mlframe repository; audit 2026-11-16 findings 54-T5 and 55-T7 (in mlframe 48 of 91 kernel files had no record named after the
file, an upper bound because family records are not mapped, and 31 of 43 records lacked a field).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_profile_records import assert_cuda_profile_records

    def test_every_gpu_module_has_a_complete_profile_record():
        assert_cuda_profile_records("src/pkg", profiles_dir="audits/2026-10-22/profiles", baseline_path="the consumer's own baseline file", min_files=50)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, read_source
from .cuda_kernel_sources import find_cuda_kernel_sources

__all__ = [
    "DEFAULT_PROFILES_DIR",
    "DEFAULT_REQUIRED",
    "EXTENDED_REQUIRED",
    "RULE_INCOMPLETE",
    "RULE_MISSING",
    "assert_cuda_profile_records",
    "find_cuda_profile_records",
]

RULE_MISSING = "cuda-profile-record-missing"
RULE_INCOMPLETE = "cuda-profile-record-incomplete"
DEFAULT_PROFILES_DIR = "audits/2026-10-22/profiles"

#: mlframe's five fields, with the same patterns as its ``fe_gpu_coverage_inventory``.
DEFAULT_REQUIRED: "dict[str, str]" = {
    "cprofile": r"cProfile",
    "gpu-profiler": r"\bnsys\b|\bncu\b|\bnvprof\b",
    "launches-per-call": r"(?i)launches?\s+per",
    "occupancy": r"(?i)occupancy",
    "throughput": r"(?i)throughput|utilization|bandwidth",
}
#: The contract's full wording: the occupancy limiter, SM throughput, DRAM bandwidth and the kernel-versus-copy share of the wall time.
EXTENDED_REQUIRED: "dict[str, str]" = {
    **DEFAULT_REQUIRED,
    "occupancy-limiter": r"(?i)limit(?:er|ed by|ing)|bound by",
    "sm-throughput": r"(?i)\bSM\b[^\n]{0,60}(?:throughput|util)|sm__",
    "dram-bandwidth": r"(?i)\bDRAM\b|dram__|memory bandwidth",
    "kernel-copy-share": r"(?i)(?:copy|copies|memcpy|transfer)[^\n]{0,80}(?:share|fraction|%)|(?:share|fraction|%)[^\n]{0,80}(?:copy|copies|memcpy|transfer)",
}


def _resolve_profiles_dir(root: Path, profiles_dir: Union[str, Path]) -> Path:
    """An absolute directory as given; a relative one against *root* and then each of its ancestors (the repository root is usually above the source root), else against *root*."""
    given = Path(profiles_dir)
    if given.is_absolute():
        return given
    base = root.resolve()
    for candidate in (base, *base.parents):
        if (candidate / given).is_dir():
            return candidate / given
    return base / given


def _check_exempt(exempt: Optional[Mapping[str, str]]) -> "dict[str, str]":
    out = dict(exempt or {})
    bare = sorted(stem for stem, reason in out.items() if not str(reason).strip())
    if bare:
        raise ValueError(f"cuda_profile_records: exempt entries need a reason (an exemption without a review is a hole): {bare}")
    return out


def _compile(required: Mapping[str, Union[str, "re.Pattern[str]"]]) -> "dict[str, re.Pattern[str]]":
    return {name: re.compile(p) if isinstance(p, str) else p for name, p in required.items()}


def find_cuda_profile_records(
    root: Union[str, Path],
    *,
    profiles_dir: Union[str, Path] = DEFAULT_PROFILES_DIR,
    required: Optional[Mapping[str, Union[str, "re.Pattern[str]"]]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    family_map: Optional[Mapping[str, str]] = None,
    exclude: "Iterable[str]" = ("_benchmarks",),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """The missing and incomplete profile records of the kernel-bearing modules under *root*, sorted by path.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable module (unless *allow_unparsed*): a gate that cannot read the code must not report it kernel-free.
    """
    patterns = _compile(DEFAULT_REQUIRED if required is None else required)
    exempt_map = _check_exempt(exempt)
    families = dict(family_map or {})
    root_path = Path(root)
    sources = find_cuda_kernel_sources(root_path, exclude=exclude, key_base=root_path, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    modules = sorted({key.partition("::")[0] for key in sources})
    directory = _resolve_profiles_dir(root_path, profiles_dir)
    shown_dir = Path(profiles_dir).as_posix() if not Path(profiles_dir).is_absolute() else directory.name
    out: "list[Finding]" = []
    checked: "set[str]" = set()
    for module in modules:
        stem = Path(module).stem
        record_stem = families.get(stem, stem)
        if stem in exempt_map or record_stem in exempt_map:
            continue
        record = directory / f"{record_stem}.md"
        if not record.is_file():
            via = f" (family record of `{record_stem}`)" if record_stem != stem else ""
            out.append(
                Finding(
                    module,
                    1,
                    RULE_MISSING,
                    f"module `{stem}` defines a CUDA kernel but has no profile record `{shown_dir}/{record_stem}.md`{via}: profile it (cProfile plus nsys/ncu) and write the record, map it to a family record with family_map, or exempt it with a reason",
                )
            )
            continue
        if record_stem in checked:
            continue
        checked.add(record_stem)
        text = read_source(record)
        for field, pattern in patterns.items():
            if not pattern.search(text):
                out.append(
                    Finding(
                        f"{shown_dir}/{record_stem}.md",
                        1,
                        f"{RULE_INCOMPLETE}:{field}",
                        f"profile record `{record_stem}.md` lacks the field `{field}` (nothing matches /{pattern.pattern}/): add it to the record",
                    )
                )
    return sorted(out, key=lambda f: (f.path, f.rule))


def assert_cuda_profile_records(
    root: Union[str, Path],
    *,
    profiles_dir: Union[str, Path] = DEFAULT_PROFILES_DIR,
    required: Optional[Mapping[str, Union[str, "re.Pattern[str]"]]] = None,
    exempt: Optional[Mapping[str, str]] = None,
    family_map: Optional[Mapping[str, str]] = None,
    exclude: "Iterable[str]" = ("_benchmarks",),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a kernel-bearing module without a complete profile record, or with *baseline_path* on any finding the shrink-only baseline does not accept."""
    found = find_cuda_profile_records(
        root,
        profiles_dir=profiles_dir,
        required=required,
        exempt=exempt,
        family_map=family_map,
        exclude=exclude,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "write or complete the profile record named in each finding (CUDA contract point 5), map a carved module with family_map, or exempt it with a reason"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_profile_records", refresh_command="PY_CI_SHARED_REFRESH=cuda_profile_records")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA profile record problem(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
