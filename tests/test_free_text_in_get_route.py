"""Tests for py_ci_shared.free_text_in_get_route: free text in a GET route's path or query string."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.free_text_in_get_route import RULE, assert_free_text_in_get_route, find_free_text_in_get_route

BOM = b"\xef\xbb\xbf"

VIOLATION = b"""from typing import Optional

from fastapi import FastAPI, Query

app = FastAPI()


@app.get("/lookup/{term}")
def lookup(term: str):
    return {"term": term}


@app.get("/rates")
def rates(chief_complaint: str = Query(max_length=200), limit: int = 10):
    return {"complaint": chief_complaint, "limit": limit}


@app.get("/search")
def search(phrase: Optional[str] = Query(default=None, max_length=300)):
    return {"phrase": phrase}
"""


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, source: str, **kwargs):
    """The findings for one module holding *source*."""
    root = _corpus(tmp_path, {"routes.py": source.encode("utf-8")})
    return find_free_text_in_get_route(root, use_git=False, **kwargs)


def test_reports_the_seeded_violation(tmp_path):
    """A path placeholder, a long-bounded query parameter with a free-text name and one with only a long bound are each reported once, at the route decorator."""
    found = find_free_text_in_get_route(_corpus(tmp_path, {"seed.py": VIOLATION}), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 8, RULE), ("seed.py", 13, RULE), ("seed.py", 18, RULE)]
    assert "GET /lookup/{term}: parameter 'term' is named like free text and travels in the path" in found[0].message
    assert "GET /rates: parameter 'chief_complaint' is named like free text and travels in the query string" in found[1].message
    assert "GET /search: parameter 'phrase' accepts up to 300 characters and travels in the query string" in found[2].message
    assert "limit" not in " ".join(f.message for f in found)


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """The same lookups as POST with a body model, plus a GET that takes an identifier, a short-bounded token and a number."""
    clean = """from typing import Optional

from fastapi import FastAPI, Header, Query
from pydantic import BaseModel

app = FastAPI()


class Lookup(BaseModel):
    term: str


@app.post("/lookup")
def lookup(body: Lookup):
    return {"term": body.term}


@app.get("/items/{item_id}")
def item(item_id: str, curie: Optional[str] = Query(default=None, max_length=256), page: int = 1, x_user: str = Header(default="")):
    return {"id": item_id, "curie": curie, "page": page}
"""
    assert _found(tmp_path, clean) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """A byte-order mark in front of the module changes neither the findings nor their lines."""
    plain = find_free_text_in_get_route(_corpus(tmp_path / "a", {"seed.py": VIOLATION}), use_git=False)
    bommed = find_free_text_in_get_route(_corpus(tmp_path / "b", {"seed.py": BOM + VIOLATION}), use_git=False)
    assert [(f.line, f.message) for f in bommed] == [(f.line, f.message) for f in plain]
    assert len(bommed) == 3


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A route module that cannot be parsed is an error that names it; allow_unparsed skips it and scans the rest."""
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "seed.py": VIOLATION})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_free_text_in_get_route(root, use_git=False)
    assert len(find_free_text_in_get_route(root, allow_unparsed=True, use_git=False)) == 3


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python file at all raises EmptyScanError rather than reporting a clean pass."""
    with pytest.raises(EmptyScanError):
        find_free_text_in_get_route(tmp_path, use_git=False)


def test_an_identifier_with_a_long_bound_is_not_free_text(tmp_path):
    """A 256-character bound on an identifier-named parameter is a length cap, not a sentence."""
    source = """from fastapi import FastAPI, Query

app = FastAPI()


@app.get("/a")
def a(disease_id: str = Query(max_length=256), session_token: str = Query(max_length=500), uuid: str = Query(max_length=300)):
    return {}
"""
    assert _found(tmp_path, source) == []


def test_a_short_bound_on_an_unnamed_parameter_is_not_free_text(tmp_path):
    """A 64-character bound on a parameter with an ordinary name stays below the default 100, and min_free_text_length moves that line."""
    source = """from fastapi import FastAPI, Query

app = FastAPI()


@app.get("/a")
def a(label: str = Query(max_length=64)):
    return {}
"""
    assert _found(tmp_path, source) == []
    assert len(_found(tmp_path, source, min_free_text_length=50)) == 1


def test_annotated_bounds_are_read(tmp_path):
    """Annotated[str, Query(max_length=N)] carries the bound just like a Query default."""
    source = """from typing import Annotated

from fastapi import FastAPI, Query

app = FastAPI()


@app.get("/a")
async def a(phrase: Annotated[str, Query(max_length=400)]):
    return {}
"""
    found = _found(tmp_path, source)
    assert len(found) == 1 and "'phrase' accepts up to 400 characters" in found[0].message


def test_only_get_routes_are_looked_at(tmp_path):
    """A POST, a PUT and a non-route .get call (a cache lookup with a key that is not a path) are ignored."""
    source = """from fastapi import FastAPI

app = FastAPI()


@app.post("/p/{term}")
def p(term: str):
    return {}


@app.put("/q/{term}")
def q(term: str):
    return {}


@cache.get("term")
def r(term: str):
    return {}
"""
    assert _found(tmp_path, source) == []


def test_a_parameter_read_from_the_body_a_header_or_a_dependency_is_not_in_the_url(tmp_path):
    """Body(), Header(), Cookie() and Depends() sources are not the query string, whatever the parameter is called."""
    source = """from fastapi import Body, Cookie, Depends, FastAPI, Header

app = FastAPI()


@app.get("/a")
def a(query: str = Header(default=""), text: str = Cookie(default=""), note: str = Body(default=""), message: str = Depends(lambda: "")):
    return {}
"""
    assert _found(tmp_path, source) == []


def test_a_non_text_parameter_with_a_free_text_name_is_not_reported(tmp_path):
    """`query: int` and `search: bool` are flags or numbers; only text can carry a typed phrase."""
    source = """from fastapi import FastAPI

app = FastAPI()


@app.get("/a")
def a(query: int = 0, search: bool = False):
    return {}
"""
    assert _found(tmp_path, source) == []


def test_a_path_placeholder_without_an_annotation_is_text_by_default(tmp_path):
    """FastAPI reads an unannotated path parameter as str, so `{condition}` with a bare `condition` is reported."""
    source = """from fastapi import FastAPI

app = FastAPI()


@app.get("/c/{condition}")
def c(condition):
    return {}
"""
    assert len(_found(tmp_path, source)) == 1


def test_the_route_path_can_be_given_by_keyword(tmp_path):
    """`@router.get(path="/x/{term}")` is a route like `@router.get("/x/{term}")`."""
    source = """from fastapi import APIRouter

router = APIRouter()


@router.get(path="/x/{term}")
def x(term: str):
    return {}
"""
    assert len(_found(tmp_path, source)) == 1


def test_the_free_text_names_are_configurable(tmp_path):
    """A project can name its own free-text parameters, and the defaults can be replaced rather than extended."""
    source = """from fastapi import FastAPI

app = FastAPI()


@app.get("/a")
def a(diagnosis: str, term: str):
    return {}
"""
    only_diagnosis = _found(tmp_path, source, free_text_names=["diagnosis"])
    assert [f.message.split("'")[1] for f in only_diagnosis] == ["diagnosis"]


def test_a_baseline_accepts_known_findings_and_fails_a_new_one(tmp_path):
    """A route that must stay GET is baselined; the baseline is a ratchet, so one more finding fails."""
    root = _corpus(tmp_path / "src", {"routes.py": VIOLATION})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_free_text_in_get_route(root, baseline_path=baseline, refresh=True, grow=True, use_git=False)
    assert_free_text_in_get_route(root, baseline_path=baseline, use_git=False)
    (root / "more.py").write_bytes(b'from fastapi import FastAPI\napp = FastAPI()\n\n\n@app.get("/z/{text}")\ndef z(text: str):\n    return {}\n')
    with pytest.raises(pytest.fail.Exception, match=r"more\.py"):
        assert_free_text_in_get_route(root, baseline_path=baseline, use_git=False)


def test_without_a_baseline_any_finding_fails_with_the_fix(tmp_path):
    """The default mode raises one AssertionError listing every finding and saying where the text should go."""
    root = _corpus(tmp_path, {"seed.py": VIOLATION})
    with pytest.raises(AssertionError, match=r"3 free-text-in-get-route finding.*POST body"):
        assert_free_text_in_get_route(root, use_git=False)
