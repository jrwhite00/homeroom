"""Read endpoints for the student page: the courses we know, the closest known courses, the concepts.

GET /courses?state=           the state's courses from the courses index, by course name.
GET /courses/match?state=&q=  the 3 known courses closest in meaning to what the student typed.
GET /concepts                 every concept with its definition, for the checklist fallback.

The courses index has no semantic field (only the concepts index does), so a typed course is
matched to concepts first and courses are ranked by the concepts they share. Each of the
MAX_CONCEPTS closest concepts counts in proportion to its score. translate() keeps only the
concepts near the best one, which drops the concepts courses actually teach for text like
"Computer Programming 1", so all MAX_CONCEPTS count here. A course's score is the share of
that weight it teaches (0 to 1). Ties go to the course whose name shares more words with the
typed text, then to the course name. Courses that share no concept are left out.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app.es import ES, ESError
from app.search import get_es
from app.telemetry import tracer
from app.translate import closest_concepts

COURSES, CONCEPTS = "courses", "concepts"
MAX_MATCHES = 3
MAX_DOCS = 500

router = APIRouter()


def normal_state(state: str) -> str:
    """"utah" and " Utah " are the same state (as in /week)."""
    return " ".join(state.split()).title()


def by_name(name: str | None) -> str:
    return (name or "").casefold()


def sources(res: dict) -> list[dict]:
    return [h["_source"] for h in res.get("hits", {}).get("hits", [])]


def state_courses(es: ES, state: str) -> list[dict]:
    """The state's courses, sorted by course name (case-insensitive)."""
    res = es.search(COURSES, {"size": MAX_DOCS,
                              "query": {"term": {"state": {"value": state, "case_insensitive": True}}},
                              "_source": ["slug", "course", "teaches"]})
    courses = [{"slug": s.get("slug"), "course": s.get("course") or "", "teaches": list(s.get("teaches") or [])}
               for s in sources(res)]
    return sorted(courses, key=lambda c: by_name(c["course"]))


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def rank_courses(courses: list[dict], concepts: list[dict], typed: str) -> list[dict]:
    """Score each course by the concept weight it teaches; see the module docstring."""
    total = sum(c["_score"] for c in concepts)
    if total <= 0:
        return []
    typed_words = words(typed)
    scored = []
    for course in courses:
        taught = set(course["teaches"])
        score = round(sum(c["_score"] for c in concepts if c.get("slug") in taught) / total, 2)
        if score > 0:
            scored.append({"slug": course["slug"], "course": course["course"], "score": score})
    scored.sort(key=lambda m: (-m["score"], -len(typed_words & words(m["course"])), by_name(m["course"])))
    return scored[:MAX_MATCHES]


@router.get("/courses")
def list_courses(state: str = Query(..., min_length=1, max_length=60), es: ES = Depends(get_es)) -> dict:
    state = normal_state(state)
    with tracer.start_as_current_span("homeroom.courses") as span:
        try:
            courses = state_courses(es, state)
        except ESError:
            span.set_attribute("courses.error", True)
            raise HTTPException(status_code=502, detail="The course list is unavailable right now.") from None
        span.set_attribute("courses.count", len(courses))
        return {"state": state, "courses": courses}


@router.get("/courses/match")
def match_courses(state: str = Query(..., min_length=1, max_length=60),
                  q: str = Query(..., min_length=1, max_length=200), es: ES = Depends(get_es)) -> dict:
    # The typed text never goes on the span: it is whatever the student wrote.
    with tracer.start_as_current_span("homeroom.courses_match") as span:
        try:
            courses = state_courses(es, normal_state(state))
            matches = rank_courses(courses, closest_concepts(es, q), q) if courses and q.strip() else []
        except ESError:
            span.set_attribute("courses_match.error", True)
            raise HTTPException(status_code=502, detail="Finding your course is unavailable right now.") from None
        span.set_attribute("courses_match.count", len(matches))
        return {"matches": matches}


@router.get("/concepts")
def list_concepts(es: ES = Depends(get_es)) -> dict:
    with tracer.start_as_current_span("homeroom.concepts") as span:
        try:
            res = es.search(CONCEPTS, {"size": MAX_DOCS, "query": {"match_all": {}},
                                       "_source": ["slug", "concept", "definition"]})
        except ESError:
            span.set_attribute("concepts.error", True)
            raise HTTPException(status_code=502, detail="The concept list is unavailable right now.") from None
        concepts = sorted(({"slug": s.get("slug"), "concept": s.get("concept"), "definition": s.get("definition")}
                           for s in sources(res)), key=lambda c: by_name(c["concept"]))
        span.set_attribute("concepts.count", len(concepts))
        return {"concepts": concepts}
