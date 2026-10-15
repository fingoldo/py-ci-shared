"""Tests for py_ci_shared.validation_errors_echo_input."""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.validation_errors_echo_input import (
    RULE,
    SHAPE_HANDLER,
    SHAPE_RAISE,
    assert_validation_errors_echo_input,
    find_validation_errors_echo_input,
)

BOM = b"\xef\xbb\xbf"
HEAD = "from pydantic import BaseModel, field_validator, model_validator, validator, root_validator\n"
HANDLER_HEAD = (
    "from fastapi import FastAPI\nfrom fastapi.exceptions import RequestValidationError\nfrom fastapi.responses import JSONResponse\napp = FastAPI()\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _src(body: str, head: str = HEAD) -> bytes:
    return (head + textwrap.dedent(body)).encode("utf-8")


def _find(tmp_path: Path, body: str, head: str = HEAD) -> list:
    return find_validation_errors_echo_input(_corpus(tmp_path, {"m.py": _src(body, head)}), use_git=False)


def _keys(findings: list) -> list[str]:
    return [f.key for f in findings]


VIOLATION = """
def bounded(value):
    too_long = next((item for item in value if len(item) > 200), None)
    if too_long is not None:
        raise ValueError(f"entry too long: {too_long[:40]!r}")
    return value


class Req(BaseModel):
    @field_validator("symptoms")
    @classmethod
    def _bounded(cls, value):
        return bounded(value)
"""


def test_reports_the_seeded_violation(tmp_path):
    (finding,) = _find(tmp_path, VIOLATION)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 6, RULE)
    assert (
        finding.message
        == "bounded: raise ValueError(...) in a validator helper puts the validated value `value` into the validation error (FastAPI's 422 detail)"
    )
    assert finding.key == f"m.py::bounded::{SHAPE_RAISE}"


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _find(tmp_path, VIOLATION.replace("{too_long[:40]!r}", "{len(too_long)} chars")) == []


@pytest.mark.parametrize(
    "message",
    [
        'f"bad value {v!r}"',
        '"bad value %r" % (v,)',
        '"bad value {}".format(v.strip())',
        '"bad value: " + v',
        "v",
        'f"bad {v[:10]}"',
    ],
)
def test_every_way_of_formatting_the_value_is_reported(tmp_path, message):
    body = f"""
    class Req(BaseModel):
        @field_validator("name")
        @classmethod
        def check(cls, v, info):
            if v.startswith("x"):
                raise ValueError({message})
            return v
    """
    assert _keys(_find(tmp_path, body)) == [f"m.py::Req.check::{SHAPE_RAISE}"]


@pytest.mark.parametrize(
    "message",
    [
        'f"too long ({len(v)} chars)"',
        'f"{info.field_name} must not start with x"',
        'f"expected str, got {type(v).__name__}"',
        '"must not start with x"',
    ],
)
def test_lengths_types_and_field_names_are_not_the_value(tmp_path, message):
    body = f"""
    class Req(BaseModel):
        @field_validator("name")
        @classmethod
        def check(cls, v, info):
            if v.startswith("x"):
                raise ValueError({message})
            return v
    """
    assert _find(tmp_path, body) == []


def test_a_dict_key_or_an_index_is_a_location_but_the_item_is_the_value(tmp_path):
    body = """
    class Req(BaseModel):
        @field_validator("context")
        @classmethod
        def keys(cls, value):
            for key, val in value.items():
                if val is None:
                    raise ValueError(f"context[{key!r}] must be set")
            for i, item in enumerate(value):
                if not item:
                    raise ValueError(f"entry {i} is empty")
            return value

        @field_validator("context")
        @classmethod
        def vals(cls, value):
            for key, val in value.items():
                number = float(val)
                if number > 130:
                    raise ValueError(f"context[{key!r}] got {number}")
            return value
    """
    assert [(f.line, f.key) for f in _find(tmp_path, body)] == [(21, f"m.py::Req.vals::{SHAPE_RAISE}")]


def test_v1_validators_model_validators_custom_errors_and_asserts(tmp_path):
    body = """
    from pydantic_core import PydanticCustomError

    class Req(BaseModel):
        @validator("a")
        def v1(cls, v, values):
            raise TypeError(f"bad {v}")

        @root_validator(pre=True)
        def root(cls, values):
            raise ValueError(f"bad {values['a']}")

        @model_validator(mode="after")
        def after(self):
            assert self.a < 3, f"a={self.a}"
            return self

        @model_validator(mode="before")
        @classmethod
        def before(cls, data):
            raise PydanticCustomError("bad", "bad {x}", {"x": data})
    """
    assert _keys(_find(tmp_path, body)) == [
        f"m.py::Req.v1::{SHAPE_RAISE}",
        f"m.py::Req.root::{SHAPE_RAISE}",
        f"m.py::Req.before::{SHAPE_RAISE}",
    ]


def test_a_function_that_is_not_a_pydantic_validator_is_out_of_scope(tmp_path):
    body = """
    import re
    from pydantic import BaseModel, field_validator

    class Splitter:
        def split(self, text):
            raise ValueError(f"cannot split {text}")

    class Req(BaseModel):
        @field_validator("a")
        @classmethod
        def check(cls, v):
            return re.split(",", v)

    def validate_path(path):
        raise ValueError(f"no such file: {path}")

    def field_validator_lookalike(v):
        raise ValueError(f"bad {v}")
    """
    assert _find(tmp_path, body, head="") == []


def test_helpers_are_followed_across_files_by_keyword_and_through_other_helpers(tmp_path):
    root = _corpus(
        tmp_path,
        {
            "models.py": _src("""
                from helpers import outer

                class Req(BaseModel):
                    @field_validator("x")
                    @classmethod
                    def check(cls, v):
                        return outer(limit=3, entries=v)
                """),
            "helpers.py": _src(
                """
                def outer(limit, entries):
                    return inner(entries[0])

                def inner(entry):
                    raise ValueError(f"bad entry {entry!r}")

                def unrelated(entry):
                    raise ValueError(f"bad entry {entry!r}")
                """,
                head="",
            ),
        },
    )
    assert _keys(find_validation_errors_echo_input(root, use_git=False)) == [f"helpers.py::inner::{SHAPE_RAISE}"]


HANDLER_DROPS_SOME = """
@app.exception_handler(RequestValidationError)
async def handler(request, exc):
    cleaned = [{k: v for k, v in e.items() if k != "input"} if e["type"].endswith("too_long") else e for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": cleaned})
"""


@pytest.mark.parametrize(
    "body, expected",
    [
        (HANDLER_DROPS_SOME, "drops `input` only for some errors"),
        (
            """
            @app.exception_handler(RequestValidationError)
            async def handler(request, exc):
                return JSONResponse(status_code=422, content={"detail": exc.errors()})
            """,
            "returns exc.errors() with each error's `input`",
        ),
        (
            """
            async def handler(request, exc):
                errors = exc.errors()
                for e in errors:
                    if e["type"] == "string_too_long":
                        e.pop("input", None)
                return JSONResponse(status_code=422, content={"detail": errors})

            app.add_exception_handler(RequestValidationError, handler)
            """,
            "drops `input` only for some errors",
        ),
    ],
)
def test_a_handler_that_keeps_input_for_any_error_is_reported(tmp_path, body, expected):
    (finding,) = _find(tmp_path, body, head=HANDLER_HEAD)
    assert finding.key == f"m.py::handler::{SHAPE_HANDLER}"
    assert expected in finding.message


@pytest.mark.parametrize(
    "body",
    [
        HANDLER_DROPS_SOME.replace(' if e["type"].endswith("too_long") else e', ""),
        """
        @app.exception_handler(RequestValidationError)
        async def handler(request, exc):
            errors = exc.errors()
            for e in errors:
                e.pop("input", None)
            return JSONResponse(status_code=422, content={"detail": errors})
        """,
        """
        @app.exception_handler(RequestValidationError)
        async def handler(request, exc):
            out = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
            return JSONResponse(status_code=422, content={"detail": out})
        """,
        """
        @app.exception_handler(RequestValidationError)
        async def handler(request, exc):
            return JSONResponse(status_code=422, content={"detail": "invalid request"})
        """,
        """
        @app.exception_handler(ValueError)
        async def handler(request, exc):
            return JSONResponse(status_code=422, content={"detail": exc.errors()})
        """,
    ],
)
def test_a_handler_that_drops_input_for_every_error_is_not_reported(tmp_path, body):
    assert _find(tmp_path, body, head=HANDLER_HEAD) == []


def test_the_key_is_line_free(tmp_path):
    first = _find(tmp_path / "a", VIOLATION)
    second = _find(tmp_path / "b", "\n\n# moved down\n" + VIOLATION)
    assert _keys(first) == _keys(second) and first[0].line != second[0].line


def test_the_baseline_accepts_known_findings_refuses_new_ones_and_only_shrinks(tmp_path, monkeypatch):
    root = _corpus(tmp_path / "src", {"m.py": _src(VIOLATION)})
    baseline = tmp_path / "baseline.json"
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    with pytest.raises(pytest.skip.Exception):
        assert_validation_errors_echo_input(root, baseline_path=baseline, refresh=True, use_git=False)
    monkeypatch.delenv("PY_CI_SHARED_REFRESH_ALLOW_GROW")
    assert list(json.loads(baseline.read_text(encoding="utf-8"))["entries"]) == [f"m.py::bounded::{SHAPE_RAISE}"]
    assert_validation_errors_echo_input(root, baseline_path=baseline, use_git=False)
    (root / "h.py").write_bytes(_src(HANDLER_DROPS_SOME, head=HANDLER_HEAD))
    with pytest.raises(pytest.fail.Exception, match=r"h.py:7: .*RequestValidationError handler drops"):
        assert_validation_errors_echo_input(root, baseline_path=baseline, use_git=False)
    with pytest.raises(pytest.fail.Exception, match=re.escape(f"h.py::handler::{SHAPE_HANDLER}  (new: 1)")):
        assert_validation_errors_echo_input(root, baseline_path=baseline, refresh=True, use_git=False)
    assert list(json.loads(baseline.read_text(encoding="utf-8"))["entries"]) == [f"m.py::bounded::{SHAPE_RAISE}"]


def test_assert_without_a_baseline_fails_on_any_finding(tmp_path):
    root = _corpus(tmp_path, {"m.py": _src(VIOLATION)})
    with pytest.raises(AssertionError, match="1 validation-errors-echo-input finding"):
        assert_validation_errors_echo_input(root, use_git=False)


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_validation_errors_echo_input(_corpus(tmp_path / "a", {"m.py": _src(VIOLATION)}), use_git=False)
    bom = find_validation_errors_echo_input(_corpus(tmp_path / "b", {"m.py": BOM + _src(VIOLATION)}), use_git=False)
    assert [(f.line, f.key) for f in bom] == [(f.line, f.key) for f in plain] and plain


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"m.py": _src(VIOLATION), "broken.py": b"def broken(:\n    pass\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_validation_errors_echo_input(root, use_git=False)
    assert _keys(find_validation_errors_echo_input(root, allow_unparsed=True, use_git=False)) == [f"m.py::bounded::{SHAPE_RAISE}"]


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_validation_errors_echo_input(tmp_path, use_git=False)
