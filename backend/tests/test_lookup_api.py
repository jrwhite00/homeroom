"""GET /courses, POST /courses/match and GET /concepts against a stubbed Elasticsearch (fixtures only)."""
from fastapi.testclient import TestClient

from app.es import ESError
from app.main import app
from app.search import get_es

COURSES = [
    {"slug": "fx-programming-1", "course": "Computer Programming 1", "state": "Utah"},
    {"slug": "fx-exploring", "course": "Exploring Computer Science", "state": "Utah"},
    {"slug": "fx-ohio", "course": "Ohio CS", "state": "Ohio"},
]
CONCEPTS = [
    {"slug": "programming-basics", "concept": "Programming basics", "definition": "Telling a computer what to do"},
    {"slug": "containers", "concept": "Containers", "definition": "Packing an app with everything it needs"},
]
# What the semantic search returns, best first: one row per standard, several per course.
STANDARD_HITS = ["Web Development 1", "Web Development 1", "Computer Programming 1", "Exploring Computer Science",
                 "Computer Programming 2"]


class StubES:
    def __init__(self, fail=False):
        self.fail, self.bodies = fail, []

    def search(self, index, body):
        self.bodies.append((index, body))
        if self.fail:
            raise ESError("connection refused")
        if index == "courses":
            q = body["query"]
            docs = [c for c in COURSES if "match_all" in q or c["state"] == q["term"]["state"]]
        elif index == "concepts":
            docs = CONCEPTS
        else:
            docs = [{"course": c} for c in STANDARD_HITS]
        return {"hits": {"hits": [{"_source": d} for d in docs]}}


def call(stub, method, path, **kw):
    app.dependency_overrides[get_es] = lambda: stub
    try:
        return TestClient(app).request(method, path, **kw)
    finally:
        app.dependency_overrides.clear()


def test_courses_for_a_state_in_name_order():
    body = call(StubES(), "GET", "/courses", params={"state": " utah "}).json()
    assert [c["slug"] for c in body["courses"]] == ["fx-programming-1", "fx-exploring"]


def test_all_courses_without_a_state():
    body = call(StubES(), "GET", "/courses").json()
    assert {c["state"] for c in body["courses"]} == {"Utah", "Ohio"}


def test_match_groups_standards_by_course_and_says_which_we_know():
    stub = StubES()
    body = call(stub, "POST", "/courses/match", json={"state": "utah", "text": "making web pages"}).json()
    assert body["state"] == "Utah"
    assert body["matches"] == [
        {"course": "Web Development 1", "known": False, "slug": None, "standard": "Utah CTE Web Development 1 strands"},
        {"course": "Computer Programming 1", "known": True, "slug": "fx-programming-1",
         "standard": "Utah CTE Computer Programming 1 strands"},
        {"course": "Exploring Computer Science", "known": True, "slug": "fx-exploring",
         "standard": "Utah CTE Exploring Computer Science strands"},
    ]
    index, query = stub.bodies[-1]
    assert index == "standards"
    assert query["query"]["bool"]["must"] == [{"match": {"semantic": {"query": "making web pages"}}}]
    assert {"prefix": {"source": "utah-cte-"}} in query["query"]["bool"]["filter"]


def test_match_puts_the_exact_name_first():
    body = call(StubES(), "POST", "/courses/match", json={"state": "Utah", "text": "computer programming 2"}).json()
    assert body["matches"][0]["course"] == "Computer Programming 2"
    assert len(body["matches"]) == 3


def test_match_text_is_required():
    assert call(StubES(), "POST", "/courses/match", json={"state": "Utah", "text": ""}).status_code == 422


def test_concepts_for_the_checklist():
    body = call(StubES(), "GET", "/concepts").json()
    assert [c["concept"] for c in body["concepts"]] == ["Containers", "Programming basics"]
    assert body["concepts"][0]["definition"] == "Packing an app with everything it needs"


def test_elasticsearch_down_is_a_plain_502():
    for method, path, kw in [("GET", "/courses", {}), ("GET", "/concepts", {}),
                             ("POST", "/courses/match", {"json": {"state": "Utah", "text": "web"}})]:
        r = call(StubES(fail=True), method, path, **kw)
        assert r.status_code == 502
        assert "connection refused" not in r.text
