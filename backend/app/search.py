"""GET /search over the sessions index.

hybrid (default): an rrf retriever that fuses a BM25 multi_match on title and abstract with a
                  match on the semantic (ELSER) field.
keyword:          the same BM25 multi_match on its own, so the demo can compare the two.

The sessions index has no "description" field; a talk's description is stored in "abstract".
Only talks are returned. Breaks, registration, co-located events and other non-talk entries
have is_talk=false. Solutions Showcase tip talks and Sponsor Demo Theater demos have speakers,
so they are excluded by track.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app import chaos
from app.telemetry import tracer
from app.es import ES, ESError
from app.translate import translate as translate_question

INDEX = "sessions"
SIZE = 10
RANK_WINDOW = 50
TEXT_FIELDS = ["title", "abstract"]
EXCLUDED_TRACKS = ["Solutions Showcase", "Sponsor Demo Theater"]
SOURCE = ["title", "speaker_names", "room", "starts_at"]

Mode = Literal["hybrid", "keyword"]

router = APIRouter()


def talk_filter() -> dict:
    return {"bool": {"filter": [{"term": {"is_talk": True}}],
                     "must_not": [{"terms": {"track": EXCLUDED_TRACKS}}]}}


def _with_filter(query: dict) -> dict:
    return {"bool": {"must": [query], "filter": [talk_filter()]}}


def build_query(q: str, mode: Mode = "hybrid", size: int = SIZE) -> dict:
    bm25 = _with_filter({"multi_match": {"query": q, "fields": TEXT_FIELDS}})
    if mode == "keyword":
        return {"query": bm25, "size": size, "_source": SOURCE}
    semantic = _with_filter({"match": {"semantic": {"query": q}}})
    return {
        "retriever": {"rrf": {
            "retrievers": [{"standard": {"query": bm25}}, {"standard": {"query": semantic}}],
            "rank_window_size": RANK_WINDOW,
        }},
        "size": size,
        "_source": SOURCE,
    }


def to_results(response: dict) -> list[dict]:
    results = []
    for hit in response.get("hits", {}).get("hits", []):
        s = hit.get("_source", {})
        results.append({
            "title": s.get("title"),
            "speakers": s.get("speaker_names") or [],
            "room": s.get("room"),
            "starts_at": s.get("starts_at"),  # ISO 8601 with the America/Denver offset
            "score": hit.get("_score"),
        })
    return results


def run(es: ES, q: str, mode: Mode = "hybrid", size: int = SIZE) -> list[dict]:
    return to_results(es.search(INDEX, build_query(q, mode, size)))


@lru_cache
def get_es() -> ES:
    return ES.from_env()


def translated_text(es: ES, q: str, concepts_index: str = "concepts") -> tuple[str, dict | None]:
    """Return (text to search, translation). Falls back to the question if nothing translates."""
    try:
        translation = translate_question(es, q, concepts_index)
    except ESError:
        translation = None  # concepts index missing or unreachable: still search the question itself
    return (translation["text"] if translation else q), translation


@router.get("/search")
def search(response: Response, q: str = Query(min_length=1, max_length=500), mode: Mode = "hybrid",
           translate: bool = False, es: ES = Depends(get_es)) -> dict:
    induced = chaos.apply_delay()  # marks the server span induced=true
    if induced:
        response.headers["x-homeroom-induced"] = "true"
    with tracer.start_as_current_span("homeroom.search", attributes={"search.mode": mode,
                                                                      "search.translate": translate,
                                                                      **({"induced": True} if induced else {})}) as span:
        text, translation = translated_text(es, q) if translate else (q, None)
        if translation:
            span.set_attribute("search.translated_concepts", translation["concepts"])
        try:
            results = run(es, text, mode)
        except ESError:
            span.set_attribute("search.error", "elasticsearch")
            raise HTTPException(status_code=502, detail="Search is unavailable right now.") from None
        span.set_attribute("search.result_count", len(results))
    body = {"q": q, "mode": mode, "translated": translation, "results": results}
    if induced:
        body["induced_delay"] = chaos.status()
    return body
