def test_f02_both_reexport_patch_scanners_acknowledge_each_other():
    """Neither module used to mention the other; a triager could not tell one finding from two."""
    from py_ci_shared.code_audit import patch_target_is_a_reexport, reexport_patch_target

    assert "reexport_patch_target.py" in (patch_target_is_a_reexport.__doc__ or "")
    assert "patch_target_is_a_reexport.py" in (reexport_patch_target.__doc__ or "")


def test_f02_only_one_of_the_pair_runs_by_default():
    """Both stay registered (so the emitted check id is re-runnable), but only one is on by default."""
    from py_ci_shared.code_audit.registry import OPT_IN_ONLY, get_scanners

    scanners = get_scanners()
    assert "patch_target_is_a_reexport" in scanners
    assert "reexport_patch_target" in scanners
    assert "reexport_patch_target" in OPT_IN_ONLY
    assert "patch_target_is_a_reexport" not in OPT_IN_ONLY


def test_f02_dotted_helper_is_the_shared_one():
    """The scanner's private `_dotted` copy of `_base.dotted_name` is gone (duplicate_function_body)."""
    from py_ci_shared.code_audit import patch_target_is_a_reexport

    assert not hasattr(patch_target_is_a_reexport, "_dotted")
    assert patch_target_is_a_reexport.dotted_name.__module__.endswith("_base")
