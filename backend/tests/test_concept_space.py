"""Concept space (concept_vec), GET /easier and GET /next against a stubbed Elasticsearch."""
import json

import pytest
from fastapi.testclient import TestClient

from app import concept_space, easier
from app.main import app
from app.search import get_es

DIMS = ["containers", "data-and-databases", "networking-basics", "programming-basics", "scaling-and-reliability"]
CONCEPTS = [{"slug": s, "concept": s.replace("-", " ").capitalize()} for s in DIMS]
COURSES = [{"slug": "fx-cs", "state": "Utah", "teaches": ["programming-basics", "data-and-databases"]}]


def session(id_, title, assumes=None, abstract=""):
    s = {"id": id_, "title": title, "abstract": abstract, "track": ["Observability"]}
    if assumes is not None:
        s.update(assumes=assumes, assumes_status="tagged")
    return s


GPU = session("1", "Your GPU Dashboard Is Lying", ["containers", "scaling-and-reliability", "data-and-databases"],
              "GPU metrics")
READY_OBS = session("2", "The Race to Standardize AI Observability", ["data-and-databases"])
ONE_STEP = session("3", "OTel Drift", ["containers", "programming-basics"])
NOT_YET = session("4", "GPU eBPF", ["containers", "networking-basics"])
UNTAGGED = session("5", "Not sorted yet")


# --- concept space ----------------------------------------------------------------------------------------

def test_dims_are_concept_slugs_sorted(tmp_path):
    (tmp_path / "concepts").mkdir()
    for slug in ("zeta", "alpha", "mid"):
        (tmp_path / "concepts" / f"{slug}.yaml").write_text(f"concept: {slug}\ndefinition: d\ntechnical words: w\n")
    assert concept_space.dims_from_curriculum(tmp_path) == ["alpha", "mid", "zeta"]
    concept_space.write_dims(["alpha", "mid", "zeta"], tmp_path / "dims.json")
    assert concept_space.load_dims(tmp_path / "dims.json") == ["alpha", "mid", "zeta"]


def test_one_where_assumed_zero_elsewhere_and_untagged_gets_none():
    assert concept_space.talk_vector(GPU, DIMS) == [1.0, 1.0, 0.0, 0.0, 1.0]
    assert concept_space.talk_vector(session("6", "Keynote", []), DIMS) == [0.0] * 5  # zero vector: valid with l2_norm
    assert concept_space.talk_vector(UNTAGGED, DIMS) is None


def test_mapping_is_l2_norm_dense_vector_and_records_the_order():
    m = concept_space.mapping(DIMS)
    assert m["properties"]["concept_vec"]["similarity"] == "l2_norm"
    assert m["properties"]["concept_vec"]["dims"] == 5 and m["_meta"]["concept_dims"] == DIMS


def test_bulk_sets_vectors_and_removes_them_from_untagged_talks():
    lines = [json.loads(line) for line in concept_space.bulk_lines([GPU, UNTAGGED], DIMS)]
    assert lines[1] == {"doc": {"concept_vec": [1.0, 1.0, 0.0, 0.0, 1.0]}}
    assert "remove('concept_vec')" in lines[3]["script"]["source"]


def test_committed_dims_file_matches_the_curriculum():
    assert concept_space.load_dims() == concept_space.dims_from_curriculum()


# --- /easier and /next ------------------------------------------------------------------------------------

class StubES:
    def __init__(self, ranked):
        self.ranked, self.bodies = ranked, []

    def request(self, method, path, body=None, **kw):
        return {"sessions": {"mappings": {"_meta": {"concept_dims": DIMS}}}}

    def search(self, index, body):
        self.bodies.append((index, body))
        if index == "courses":
            docs = [c for c in COURSES if c["slug"] in body["query"]["ids"]["values"]]
        elif index == "concepts":
            docs = CONCEPTS
        elif "term" in body.get("query", {}):  # one talk by id
            docs = [s for s in (GPU, READY_OBS, ONE_STEP, NOT_YET, UNTAGGED) if s["id"] == body["query"]["term"]["id"]]
        elif "query" in body:  # the topic pool
            docs = [{"id": s["id"]} for s in self.ranked]
        else:  # the rrf retriever or the /next kNN
            docs = self.ranked
        return {"hits": {"hits": [{"_source": d} for d in docs]}}


def call(path, stub, **params):
    app.dependency_overrides[get_es] = lambda: stub
    try:
        return TestClient(app).get(path, params=params)
    finally:
        app.dependency_overrides.clear()


def test_easier_zeroes_the_students_missing_concepts():
    assert easier.easier_vector([1.0, 1.0, 0.0, 0.0, 1.0], DIMS, {"data-and-databases", "programming-basics"}) == \
        [0.0, 1.0, 0.0, 0.0, 0.0]


def test_easier_blends_knn_and_semantic_with_rrf_and_skips_the_hard_concepts():
    stub = StubES([READY_OBS, ONE_STEP, NOT_YET])
    body = call("/easier", stub, talk_id="1", state="utah", courses="fx-cs").json()
    talk_query = next(b for _, b in stub.bodies if b.get("query", {}).get("term", {}).get("id") == "1")
    assert "abstract" in talk_query["_source"] and "concept_vec" not in talk_query["_source"]
    _, q = next((i, b) for i, b in stub.bodies if "retriever" in b)
    knn, standard = q["retriever"]["rrf"]["retrievers"]
    assert knn["knn"]["k"] == 10 and knn["knn"]["num_candidates"] == 100
    assert knn["knn"]["query_vector"] == [0.0, 1.0, 0.0, 0.0, 0.0]
    assert standard["standard"]["query"]["bool"]["must"][0]["match"]["semantic"]["query"] == \
        "Your GPU Dashboard Is Lying GPU metrics"
    must_not = knn["knn"]["filter"]["bool"]["filter"][0]["bool"]["must_not"]
    assert {"term": {"id": "1"}} in must_not
    assert {"terms": {"assumes": ["containers", "scaling-and-reliability"]}} in must_not
    assert {"terms": {"id": ["2", "3", "4"]}} in knn["knn"]["filter"]["bool"]["filter"]  # kNN within the topic pool
    assert body["talk"]["group"] == "not_yet"


def test_easier_results_carry_readiness_and_missing_concept_names():
    body = call("/easier", StubES([READY_OBS, ONE_STEP, NOT_YET]), talk_id="1", state="Utah", courses="fx-cs").json()
    assert [(r["title"], r["group"], r["missing"]) for r in body["results"]] == [
        ("The Race to Standardize AI Observability", "ready", []),
        ("OTel Drift", "one_step", ["Containers"]),
        ("GPU eBPF", "not_yet", ["Containers", "Networking basics"])]


def test_easier_unknown_or_untagged_talk():
    assert call("/easier", StubES([]), talk_id="nope", state="Utah", courses="fx-cs").status_code == 404
    assert call("/easier", StubES([]), talk_id="5", state="Utah", courses="fx-cs").status_code == 409


def test_next_starts_from_the_students_vector_and_keeps_ready_or_one_step():
    stub = StubES([READY_OBS, NOT_YET, ONE_STEP])
    body = call("/next", stub, state="Utah", courses="fx-cs").json()
    _, q = next((i, b) for i, b in stub.bodies if "knn" in b)
    assert q["knn"]["query_vector"] == [0.0, 1.0, 0.0, 1.0, 0.0]
    assert [r["title"] for r in body["results"]] == ["The Race to Standardize AI Observability", "OTel Drift"]
    assert body["knows"] == ["Data and databases", "Programming basics"]


@pytest.mark.parametrize("path,params", [("/easier", {"talk_id": "1"}), ("/next", {})])
def test_unknown_course_is_explained(path, params):
    body = call(path, StubES([READY_OBS]), state="Utah", courses="fx-typo", **params).json()
    assert body["messages"] and "fx-typo" in body["messages"][0]
