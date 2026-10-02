# Coordinator dispositions

### CANARY-12
**Disposition:** RESOLVED -- the cache is keyed on a blake2b digest of the file's bytes, so correctness no longer depends on mtime resolution; regression tests: test_same_size_edit_with_the_same_mtime_is_seen (fails on the old key), test_an_unchanged_file_with_a_touched_mtime_keeps_its_tree

### CANARY-13
**Disposition:** RESOLVED -- imports tomllib through py_ci_shared._toml_compat; regression test: tests/test_python_floor_compatibility.py tomllib ban

### CANARY-14
**Disposition:** RESOLVED -- the helpers are public (tracked_files, table_cells) with the private names kept as aliases for existing importers; regression test: tests/test_self_gates.py private_imports

### CANARY-15
**Disposition:** RESOLVED -- an explicit isinstance check raising TypeError; regression test: tests/test_self_gates.py value_bearing_asserts
