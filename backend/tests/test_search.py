import pytest
from fastapi.testclient import TestClient

from app.es import ESError
from app.main import app
from app.search import EXCLUDED_TRACKS, build_query, get_es

HIT = {
    "_score": 0.0328,
    "_source": {
        "title": "Autoscaling Without Tears",
        "speaker_names": ["Ana Example", "Bo Sample"],
        "room": "Salt Palace | Level 1 | Room 155",
        "starts_at": "2026-11-11T14:30:00-07:00",
    },
}


class StubES:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response if response is not None else {"hits": {"hits": [HIT]}}
        self.error = error

    def search(self, index, body):
        self.calls.append((index, body))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def stub():
    es = StubES()
    app.dependency_overrides[get_es] = lambda: es
    yield es
    app.dependency_overrides.clear()


client = TestClient(app)


def talk_filter_of(query):
    """The filter clause attached to a bm25 or semantic query."""
    return query["bool"]["filter"][0]["bool"]


def test_default_mode_is_hybrid_rrf(stub):
    r = client.get("/search", params={"q": "autoscaling"})
    assert r.status_code == 200
    assert r.json()["mode"] == "hybrid"
    index, body = stub.calls[0]
    assert index == "sessions"
    bm25, semantic = (x["standard"]["query"] for x in body["retriever"]["rrf"]["retrievers"])
    assert bm25["bool"]["must"][0] == {"multi_match": {"query": "autoscaling", "fields": ["title", "abstract"]}}
    assert semantic["bool"]["must"][0] == {"match": {"semantic": {"query": "autoscaling"}}}
    assert "query" not in body


def test_keyword_mode_is_bm25_only(stub):
    r = client.get("/search", params={"q": "autoscaling", "mode": "keyword"})
    assert r.status_code == 200
    body = stub.calls[0][1]
    assert "retriever" not in body
    assert body["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["title", "abstract"]
    assert "semantic" not in str(body)


@pytest.mark.parametrize("mode", ["hybrid", "keyword"])
def test_only_talks_and_no_showcase(mode):
    body = build_query("x", mode)
    queries = ([body["query"]] if mode == "keyword"
               else [x["standard"]["query"] for x in body["retriever"]["rrf"]["retrievers"]])
    for query in queries:
        f = talk_filter_of(query)
        assert {"term": {"is_talk": True}} in f["filter"]
        assert {"terms": {"track": EXCLUDED_TRACKS}} in f["must_not"]
    assert "Solutions Showcase" in EXCLUDED_TRACKS


def test_returns_title_speakers_room_start_score(stub):
    r = client.get("/search", params={"q": "autoscaling"})
    assert r.json()["results"] == [{
        "title": "Autoscaling Without Tears",
        "speakers": ["Ana Example", "Bo Sample"],
        "room": "Salt Palace | Level 1 | Room 155",
        "starts_at": "2026-11-11T14:30:00-07:00",
        "score": 0.0328,
    }]


def test_missing_fields_do_not_break_results(stub):
    stub.response = {"hits": {"hits": [{"_score": 1.0, "_source": {"title": "No speakers yet"}}]}}
    result = client.get("/search", params={"q": "x"}).json()["results"][0]
    assert result["speakers"] == [] and result["room"] is None and result["starts_at"] is None


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "x", "mode": "vector"}])
def test_bad_requests_are_rejected(stub, params):
    assert client.get("/search", params=params).status_code == 422
    assert stub.calls == []


def test_elasticsearch_failure_is_502_without_details(stub):
    stub.error = ESError("could not reach Elasticsearch at http://localhost:9200: refused")
    r = client.get("/search", params={"q": "x"})
    assert r.status_code == 502
    assert "localhost" not in r.text
