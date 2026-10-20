# Hits the default_via_or scan found that the four group reports did not list

Added by the parent after comparing the scan with the reports file by file and line. Each hit was classified by reading the code.

### DVO-180 (Low) -- _consumers.py:117 git error text: stderr then stdout, then a return-code message
**Disposition:** FALSE-POSITIVE -- git writes its failure to stderr or, when that is empty, to stdout; an empty message is the case where the return code is the only useful text. The default is the intended last resort.
**Fix:** none

### DVO-181 (Low) -- _core/config.py:93 `", ".join(gates) or "none"` in an error message
**Disposition:** FALSE-POSITIVE -- display default: an empty list of gate names is printed as none, which is the wording wanted.
**Fix:** none

### DVO-182 (Low) -- _core/config.py:103 `start or Path.cwd()` in find_repo_root
**Disposition:** FALSE-POSITIVE -- start is an optional Path; a Path object is always truthy, so only None takes the fallback, as intended.
**Fix:** none

### DVO-183 (Low) -- _core/source.py:103 `exc.msg or "invalid syntax"`
**Disposition:** FALSE-POSITIVE -- display default: a SyntaxError without a message is reported with the generic text.
**Fix:** none

### DVO-184 (Low) -- swallowed_exceptions.py:256 `".".join(self.scope) or "<module>"`
**Disposition:** FALSE-POSITIVE -- label: an empty scope is the module scope, and "<module>" is the name it is reported under.
**Fix:** none

### DVO-185 (Low) -- tracked_secret_shapes.py:158 `fnmatch(...) or startswith(...)`
**Disposition:** FALSE-POSITIVE -- a boolean disjunction of two match tests, not a default value.
**Fix:** none

### DVO-186 (Low) -- tracked_secret_shapes.py:211 `config_path or root / "pyproject.toml"`
**Disposition:** FALSE-POSITIVE -- config_path is an optional Path, always truthy when given, so only None takes the default file.
**Fix:** none

