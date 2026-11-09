"""GET /easier and GET /next: move around concept space (concept_vec on sessions).

/easier?talk_id=&state=&courses=   Talks like this one, but easier for this student. Start from the
    talk's vector and set the concepts the student is missing to 0, so the nearest talks (l2) assume
    what the student already has from this talk. kNN on that vector is blended by rrf with a semantic
    match on the talk's title and description, so the topic stays close. Both sides skip talks that
    assume the concepts this talk is missing for the student: that's what makes them easier.
    With one 0/1 dimension per concept, many talks share a vector and kNN alone can't order them, so
    kNN runs among the TOPIC_POOL talks closest in topic (one semantic search first).
/next?state=&courses=              Where to go next: kNN from the student's own vector (1.0 for each
    concept their courses teach), keeping talks that are ready or one step away.

Every result carries its group and missing concepts (by name) from James's readiness(), unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.concept_space import DIMS_FILE, FIELD, index_dims, load_dims, talk_vector, vector
from app.es import ES, ESError
from app.search import get_es, talk_filter
from app.telemetry import tracer
from app.week import concept_name, concept_table, find_courses, split_slugs, talk_assumes, unknown_course_message
from readiness import ONE_STEP, READY, readiness

SESSIONS = "sessions"
K, NUM_CANDIDATES, SIZE = 10, 100, 10
RANK_WINDOW = 50  # how deep rrf reads each list; the semantic list goes deeper than kNN's k
TOPIC_POOL = 10  # kNN only re-ranks the 10 talks closest in topic, so ties in concept space stay on topic
NEXT_K = 50  # /next filters by readiness afterwards, so it looks further
SOURCE = ["id", "title", "starts_at", "room", "level", "track", "assumes", "assumes_status"]

router = APIRouter()


def dims_for(es: ES) -> list[str]:
    dims = index_dims(es)
    if dims:
        return dims
    if DIMS_FILE.exists():
        return load_dims()
    raise HTTPException(status_code=503, detail="Concept space isn't set up yet. Run python -m app.concept_space.")


def student(es: ES, state: str, courses: str) -> tuple[set[str], list[str]]:
    """(concepts the student knows from their courses, plain messages about unknown courses)."""
    state = " ".join(state.split()).title()
    slugs = split_slugs(courses)
    found, unknown = find_courses(es, state, slugs) if slugs else ([], [])
    return {c for course in found for c in course.get("teaches", [])}, [unknown_course_message(s, state) for s in unknown]


def candidates_filter(exclude_id: str | None = None, without: list[str] | None = None) -> dict:
    """Talks only, tagged only (untagged talks have no vector), never the talk we started from, and none
    that assume a concept in `without` (for /easier: the concepts this talk is missing for the student)."""
    f = {"bool": {"filter": [talk_filter(), {"term": {"assumes_status": "tagged"}}]}}
    must_not = ([{"term": {"id": exclude_id}}] if exclude_id else []) + ([{"terms": {"assumes": without}}] if without else [])
    if must_not:
        f["bool"]["must_not"] = must_not
    return f


def easier_vector(talk_vec: list[float], dims: list[str], knows: set[str]) -> list[float]:
    """The talk's vector with the student's missing concepts set to 0."""
    return [v if d in knows else 0.0 for v, d in zip(talk_vec, dims)]


def topic_query(talk: dict, filt: dict, size: int | None = None) -> dict:
    text = " ".join(x for x in (talk.get("title"), talk.get("abstract")) if x)
    return {"query": {"bool": {"must": [{"match": {"semantic": {"query": text}}}], "filter": [filt]}},
            "size": TOPIC_POOL if size is None else size, "_source": ["id"]}


def easier_query(talk: dict, query_vec: list[float], missing: list[str], topic_ids: list[str]) -> dict:
    """rrf of kNN (concept space, among the talks closest in topic) and the semantic match itself."""
    filt = candidates_filter(talk.get("id"), missing)
    near_topic = {"bool": {"filter": [filt, {"terms": {"id": topic_ids}}]}}
    return {
        "retriever": {"rrf": {"retrievers": [
            {"knn": {"field": FIELD, "query_vector": query_vec, "k": K, "num_candidates": NUM_CANDIDATES,
                     "filter": near_topic}},
            {"standard": {"query": topic_query(talk, filt)["query"]}},
        ], "rank_window_size": RANK_WINDOW}},
        "size": SIZE,
        "_source": SOURCE,
    }


def next_query(student_vec: list[float]) -> dict:
    return {"knn": {"field": FIELD, "query_vector": student_vec, "k": NEXT_K, "num_candidates": NUM_CANDIDATES * 2,
                    "filter": candidates_filter()},
            "size": NEXT_K, "_source": SOURCE}


def result(hit: dict, knows: set[str], concepts: dict[str, dict]) -> dict:
    s = hit["_source"]
    group, missing = readiness(talk_assumes(s), knows)
    return {"id": s.get("id"), "title": s.get("title"), "starts_at": s.get("starts_at"), "room": s.get("room"),
            "level": s.get("level"), "track": s.get("track"), "group": group,
            "missing": [concept_name(concepts, m) for m in missing]}


def get_talk(es: ES, talk_id: str) -> dict:
    res = es.search(SESSIONS, {"size": 1, "query": {"term": {"id": talk_id}},
                               "_source": SOURCE + ["abstract"]})
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        raise HTTPException(status_code=404, detail="We couldn't find that talk.")
    return hits[0]["_source"]


def build_easier(es: ES, talk_id: str, state: str, courses: str) -> dict:
    dims = dims_for(es)
    talk = get_talk(es, talk_id)
    talk_vec = talk_vector(talk, dims)
    if talk_vec is None:
        raise HTTPException(status_code=409, detail="This talk hasn't been sorted yet, so we can't find easier ones.")
    knows, messages = student(es, state, courses)
    concepts = concept_table(es)
    group, missing = readiness(talk_assumes(talk), knows)
    pool = es.search(SESSIONS, topic_query(talk, candidates_filter(talk.get("id"), missing)))
    topic_ids = [h["_source"]["id"] for h in pool.get("hits", {}).get("hits", [])]
    res = es.search(SESSIONS, easier_query(talk, easier_vector(talk_vec, dims, knows), missing, topic_ids))
    return {"talk": {"id": talk.get("id"), "title": talk.get("title"), "group": group,
                     "missing": [concept_name(concepts, m) for m in missing]},
            "messages": messages,
            "results": [result(h, knows, concepts) for h in res.get("hits", {}).get("hits", [])]}


def build_next(es: ES, state: str, courses: str) -> dict:
    dims = dims_for(es)
    knows, messages = student(es, state, courses)
    concepts = concept_table(es)
    res = es.search(SESSIONS, next_query(vector(knows, dims)))
    keep = [r for r in (result(h, knows, concepts) for h in res.get("hits", {}).get("hits", []))
            if r["group"] in (READY, ONE_STEP)]
    return {"knows": sorted(concept_name(concepts, s) for s in knows), "messages": messages, "results": keep[:SIZE]}


@router.get("/easier")
def easier(talk_id: str = Query(..., min_length=1, max_length=40), state: str = Query(..., min_length=1),
           courses: str = Query(""), es: ES = Depends(get_es)) -> dict:
    with tracer.start_as_current_span("homeroom.easier") as span:  # the talk and counts, never the student
        span.set_attribute("talk.id", talk_id)
        try:
            body = build_easier(es, talk_id, state, courses)
        except ESError:
            raise HTTPException(status_code=502, detail="Easier talks are unavailable right now.") from None
        span.set_attribute("easier.results", len(body["results"]))
        return body


@router.get("/next")
def next_talks(state: str = Query(..., min_length=1), courses: str = Query(""), es: ES = Depends(get_es)) -> dict:
    with tracer.start_as_current_span("homeroom.next") as span:
        try:
            body = build_next(es, state, courses)
        except ESError:
            raise HTTPException(status_code=502, detail="Next talks are unavailable right now.") from None
        span.set_attribute("next.results", len(body["results"]))
        return body
