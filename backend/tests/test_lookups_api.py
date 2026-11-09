"""GET /courses, /courses/match and /concepts against a stubbed Elasticsearch (nothing in curriculum/)."""
import pytest
from fastapi.testclient import TestClient

from app.es import ESError
from app.main import app
from app.search import get_es

COURSES = [
    {"slug": "fx-web", "course": "web Design", "state": "Utah", "teaches": ["networking-basics"]},
    {"slug": "fx-prog-1", "course": "Computer Programming 1", "state": "Utah",
     "teaches": ["programming-basics", "linux-command-line"]},
    {"slug": "fx-principles", "course": "Computer Science Principles", "state": "Utah",
     "teaches": ["programming-basics", "networking-basics", "data-and-databases"]},
    {"slug": "fx-exploring", "course": "Exploring Computer Science", "state": "Utah",
     "teaches": ["programming-basics", "networking-basics"]},
    {"slug": "fx-ohio", "course": "Ohio Programming", "state": "Ohio", "teaches": ["programming-basics"]},
]
CONCEPTS = [
    {"slug": "programming-basics", "concept": "Programming basics", "definition": "Telling a computer what to do."},
    {"slug": "data-and-databases", "concept": "Data and Databases", "definition": "Storing information."},
    {"slug": "containers", "concept": "containers", "definition": "Packing an app with what it needs."},
]


class StubES:
    """Answers the queries the lookup endpoints make. `closest` is what the semantic search returns."""

    def __init__(self, closest=(), fail_on=None):
        self.closest, self.fail_on, self.bodies = list(closest), fail_on, []

    def search(self, index, body):
        self.bodies.append((index, body))
        if self.fail_on in (index, "all"):
            raise ESError("connection refused")
        if index == "courses":
            term = body["query"]["term"]["state"]
            assert term["case_insensitive"] is True
            hits = [{"_source": c} for c in COURSES if c["state"].lower() == term["value"].lower()]
        elif "semantic" in body["query"].get("match", {}):
            hits = [{"_score": score, "_source": {"slug": slug, "concept": slug, "technical_words": []}}
                    for slug, score in self.closest]
        else:
            hits = [{"_source": c} for c in CONCEPTS]
        return {"hits": {"hits": hits}}


@pytest.fixture
def get():
    def _get(stub, path, **params):
        app.dependency_overrides[get_es] = lambda: stub
        try:
            return TestClient(app).get(path, params=params)
        finally:
            app.dependency_overrides.clear()
    return _get


# --- GET /courses -------------------------------------------------------------------------------

def test_courses_for_a_state_sorted_by_name(get):
    body = get(StubES(), "/courses", state=" utah ").json()
    assert body["state"] == "Utah"
    assert [c["course"] for c in body["courses"]] == [
        "Computer Programming 1", "Computer Science Principles", "Exploring Computer Science", "web Design"]
    assert body["courses"][0] == {"slug": "fx-prog-1", "course": "Computer Programming 1",
                                  "teaches": ["programming-basics", "linux-command-line"]}


def test_state_with_no_courses_is_an_empty_list(get):
    assert get(StubES(), "/courses", state="Nevada").json() == {"state": "Nevada", "courses": []}


def test_courses_needs_a_state(get):
    assert get(StubES(), "/courses").status_code == 422


def test_courses_elasticsearch_down_is_a_502_in_plain_words(get):
    r = get(StubES(fail_on="courses"), "/courses", state="Utah")
    assert r.status_code == 502
    assert r.json() == {"detail": "The course list is unavailable right now."}


# --- GET /courses/match -------------------------------------------------------------------------

def test_match_ranks_courses_by_the_concept_weight_they_teach(get):
    # networking 6 + data 3 + programming 1: Principles teaches all three, Exploring 7/10, web 6/10.
    closest = [("networking-basics", 6.0), ("data-and-databases", 3.0), ("programming-basics", 1.0)]
    body = get(StubES(closest), "/courses/match", state="Utah", q="the internet and databases").json()
    assert body == {"matches": [
        {"slug": "fx-principles", "course": "Computer Science Principles", "score": 1.0},
        {"slug": "fx-exploring", "course": "Exploring Computer Science", "score": 0.7},
        {"slug": "fx-web", "course": "web Design", "score": 0.6},
    ]}


def test_match_counts_concepts_below_the_translate_cutoff(get):
    # translate() would keep only parallel-programming here; programming-basics still counts.
    closest = [("parallel-programming", 12.4), ("computation-models", 10.7), ("programming-basics", 10.1)]
    body = get(StubES(closest), "/courses/match", state="Utah", q="Computer Programming 1").json()
    assert [m["slug"] for m in body["matches"]] == ["fx-prog-1", "fx-principles", "fx-exploring"]
    assert {m["score"] for m in body["matches"]} == {0.30}


def test_match_ties_go_to_the_course_whose_name_shares_more_words(get):
    closest = [("programming-basics", 5.0)]
    body = get(StubES(closest), "/courses/match", state="Utah", q="exploring computer science").json()
    assert [m["course"] for m in body["matches"]] == [
        "Exploring Computer Science", "Computer Science Principles", "Computer Programming 1"]


def test_match_leaves_out_courses_that_share_no_concept(get):
    body = get(StubES([("containers", 4.0)]), "/courses/match", state="Utah", q="docker").json()
    assert body == {"matches": []}


def test_match_only_looks_at_the_state(get):
    body = get(StubES([("programming-basics", 5.0)]), "/courses/match", state="Ohio", q="coding").json()
    assert body == {"matches": [{"slug": "fx-ohio", "course": "Ohio Programming", "score": 1.0}]}


def test_match_with_no_courses_for_the_state_skips_the_concept_search(get):
    stub = StubES([("programming-basics", 5.0)])
    assert get(stub, "/courses/match", state="Nevada", q="coding").json() == {"matches": []}
    assert [index for index, _ in stub.bodies] == ["courses"]


def test_match_with_no_concept_scores_is_empty(get):
    assert get(StubES([]), "/courses/match", state="Utah", q="art").json() == {"matches": []}
    assert get(StubES([("programming-basics", 0)]), "/courses/match", state="Utah", q="art").json() == {"matches": []}


def test_match_needs_state_and_text(get):
    assert get(StubES(), "/courses/match", state="Utah").status_code == 422
    assert get(StubES(), "/courses/match", q="coding").status_code == 422
    assert get(StubES(), "/courses/match", state="Utah", q="").status_code == 422


@pytest.mark.parametrize("fail_on", ["courses", "concepts"])
def test_match_elasticsearch_down_is_a_502_in_plain_words(get, fail_on):
    r = get(StubES([("programming-basics", 5.0)], fail_on=fail_on), "/courses/match", state="Utah", q="coding")
    assert r.status_code == 502
    assert r.json() == {"detail": "Finding your course is unavailable right now."}


# --- GET /concepts ------------------------------------------------------------------------------

def test_concepts_with_definitions_sorted_by_name(get):
    body = get(StubES(), "/concepts").json()
    assert [c["concept"] for c in body["concepts"]] == ["containers", "Data and Databases", "Programming basics"]
    assert body["concepts"][1] == {"slug": "data-and-databases", "concept": "Data and Databases",
                                   "definition": "Storing information."}


def test_concepts_elasticsearch_down_is_a_502_in_plain_words(get):
    r = get(StubES(fail_on="concepts"), "/concepts")
    assert r.status_code == 502
    assert r.json() == {"detail": "The concept list is unavailable right now."}
