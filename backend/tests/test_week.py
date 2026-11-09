"""GET /week against a fixture concept table and a stubbed Elasticsearch (nothing in curriculum/)."""
import pytest
from fastapi.testclient import TestClient

from app.es import ESError
from app.main import app
from app.search import get_es

CONCEPTS = [
    {"slug": "programming-basics", "concept": "Programming basics", "technical_words": ["variable", "loop"]},
    {"slug": "networking-basics", "concept": "Networking basics", "technical_words": ["IP address", "DNS"]},
    {"slug": "containers", "concept": "Containers", "technical_words": ["Docker", "container image"]},
    {"slug": "scaling-and-reliability", "concept": "Scaling and reliability",
     "technical_words": ["autoscaling", "load balancing"]},
]
COURSES = [
    {"slug": "fx-intro-cs", "course": "Intro CS", "state": "Utah", "teaches": ["programming-basics"]},
    {"slug": "fx-networks", "course": "Networks", "state": "Utah", "teaches": ["networking-basics"]},
    {"slug": "fx-elsewhere", "course": "Elsewhere CS", "state": "Ohio", "teaches": ["containers"]},
]


def talk(id_, title, assumes=None, level="Any Level", starts="2026-11-10T11:00:00-07:00", abstract=""):
    t = {"id": id_, "title": title, "abstract": abstract, "level": level, "starts_at": starts, "room": "Room " + id_}
    if assumes is not None:
        t.update(assumes=assumes, assumes_status="tagged")
    return t


READY_TALK = talk("1", "Writing your first operator", ["programming-basics", "networking-basics"])
NOTHING_ASSUMED = talk("2", "Welcome keynote", [])
ONE_STEP_TALK = talk("3", "Shipping apps with containers", ["programming-basics", "containers"])
NOT_YET_TALK = talk("4", "Autoscaling containers at 10k pods", ["containers", "scaling-and-reliability"])
UNTAGGED = talk("5", "A talk the tagger hasn't reached")
BEGINNER = talk("6", "Containers 101", [], level="Beginner", abstract="What a Docker container image is")
SCALING_ONE_STEP = talk("7", "Balancing load", ["scaling-and-reliability", "networking-basics"],
                        starts="2026-11-10T09:00:00-07:00")
KIOSKS = [{"project": "Helm", "kiosk": "P-1A"}, {"project": "Autoscaling working group", "kiosk": "P-2B",
                                                 "room_short": "Project Pavilion"}]


def words(text):
    return {w for w in text.lower().replace(",", " ").split()}


class StubES:
    """Answers the queries /week makes, over the fixture table."""

    def __init__(self, talks, kiosks=KIOSKS, fail=False):
        self.talks, self.kiosks, self.fail, self.calls = talks, kiosks, fail, []

    def search(self, index, body):
        self.calls.append(index)
        if self.fail:
            raise ESError("connection refused")
        if index == "courses":
            docs = [c for c in COURSES if c["slug"] in body["query"]["ids"]["values"]]
        elif index == "concepts":
            docs = CONCEPTS
        elif index == "sessions" and "sort" in body:
            docs = sorted(self.talks, key=lambda t: t["starts_at"])
        elif index == "sessions":  # beginner-talk lookup
            q = body["query"]["bool"]
            want = words(q["must"][0]["multi_match"]["query"])
            level = q["filter"][1]["term"]["level"]
            not_assumed = q["must_not"][0]["term"]["assumes"]
            docs = [t for t in self.talks if t["level"] == level and not_assumed not in t.get("assumes", [])
                    and want & words(t["title"] + " " + t["abstract"])]
        else:  # kiosks
            want = words(body["query"]["match"]["project"])
            docs = [k for k in self.kiosks if want & words(k["project"])]
        return {"hits": {"hits": [{"_source": d} for d in docs]}}


def get_week(stub, **params):
    app.dependency_overrides[get_es] = lambda: stub
    try:
        return TestClient(app).get("/week", params=params)
    finally:
        app.dependency_overrides.clear()


ALL = [READY_TALK, NOTHING_ASSUMED, ONE_STEP_TALK, NOT_YET_TALK, UNTAGGED, BEGINNER, SCALING_ONE_STEP]
STUDENT = {"state": "utah", "courses": "fx-intro-cs,fx-networks"}


@pytest.fixture
def week():
    return get_week(StubES(ALL), **STUDENT).json()


def titles(week, group):
    return [t["title"] for t in week["groups"][group]]


def test_concepts_come_from_the_courses_teaches_field(week):
    assert week["state"] == "Utah"
    assert [c["slug"] for c in week["courses"]] == ["fx-intro-cs", "fx-networks"]
    assert week["knows"] == ["Networking basics", "Programming basics"]
    assert week["messages"] == []


def test_ready_group(week):
    assert titles(week, "ready") == ["Writing your first operator", "Welcome keynote", "Containers 101"]
    assert all(t["missing"] == [] for t in week["groups"]["ready"])


def test_one_step_away_group_names_the_missing_concept(week):
    assert titles(week, "one_step") == ["Balancing load", "Shipping apps with containers"]  # sorted by time
    by_title = {t["title"]: t for t in week["groups"]["one_step"]}
    assert by_title["Shipping apps with containers"]["missing"] == ["Containers"]  # the name, not the slug
    assert by_title["Balancing load"]["missing"] == ["Scaling and reliability"]


def test_not_yet_group_lists_every_missing_concept_by_name(week):
    (t,) = week["groups"]["not_yet"]
    assert t["title"] == "Autoscaling containers at 10k pods"
    assert t["missing"] == ["Containers", "Scaling and reliability"]


def test_untagged_talk_is_unknown_never_ready(week):
    assert titles(week, "unknown") == ["A talk the tagger hasn't reached"]
    assert week["groups"]["unknown"][0]["missing"] == []
    assert week["counts"] == {"ready": 3, "one_step": 2, "not_yet": 1, "unknown": 1}
    assert week["labels"]["one_step"] == "One step away"


def test_one_step_talk_suggests_a_beginner_talk_for_the_missing_concept(week):
    by_title = {t["title"]: t for t in week["groups"]["one_step"]}
    s = by_title["Shipping apps with containers"]["suggestion"]
    assert s == {"kind": "beginner_talk", "title": "Containers 101", "starts_at": BEGINNER["starts_at"],
                 "room": "Room 6", "covers": "Containers"}


def test_no_beginner_talk_falls_back_to_a_project_booth(week):
    by_title = {t["title"]: t for t in week["groups"]["one_step"]}
    s = by_title["Balancing load"]["suggestion"]
    assert s["kind"] == "project_booth" and s["project"] == "Autoscaling working group" and s["kiosk"] == "P-2B"
    assert s["covers"] == "Scaling and reliability"


def test_suggestions_are_looked_up_once_per_concept():
    stub = StubES(ALL + [talk("8", "More containers", ["programming-basics", "containers"])])
    get_week(stub, **STUDENT)
    assert stub.calls.count("sessions") == 1 + 2  # all talks once, then one beginner lookup per missing concept


def test_unknown_course_is_explained_in_plain_words():
    body = get_week(StubES(ALL), state="Utah", courses="fx-intro-cs,fx-typo,fx-elsewhere").json()
    assert [c["slug"] for c in body["courses"]] == ["fx-intro-cs"]
    assert body["messages"] == [
        'We don\'t know the course "fx-typo" for Utah yet, so it isn\'t counted. Check the spelling, or ask a teacher to add it.',
        'We don\'t know the course "fx-elsewhere" for Utah yet, so it isn\'t counted. Check the spelling, or ask a teacher to add it.',
    ]
    assert body["knows"] == ["Programming basics"]


def test_everything_unknown_before_tagging():
    body = get_week(StubES([talk("9", "Untagged A"), talk("10", "Untagged B")]), **STUDENT).json()
    assert body["counts"] == {"ready": 0, "one_step": 0, "not_yet": 0, "unknown": 2}


def test_elasticsearch_down_is_a_502():
    r = get_week(StubES(ALL, fail=True), **STUDENT)
    assert r.status_code == 502


def test_state_is_required():
    assert get_week(StubES(ALL), courses="fx-intro-cs").status_code == 422
