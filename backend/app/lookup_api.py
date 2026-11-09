"""What the student page reads to fill in its form.

GET  /courses?state=   the courses we know (all states when state is empty), for the course checklist.
                       Each has a `level` ("" when it is for everyone), and `levels` lists the choices
                       for the education-level menu; the page does the filtering
POST /courses/match    {"state", "text"}: the state's courses closest in meaning to what the student
                       typed. It searches the state's CTE course standards (semantic search on the
                       standards index), so it also finds a real course that isn't in the curriculum
                       yet. Each match says whether we know it; an unknown one carries the standard
                       name POST /courses/propose needs.
GET  /concepts         every concept with its plain-language definition, for the concept checklist

/courses/match is a POST so the typed text stays out of URLs and request logs. It is never put on
a span either.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.course_proposals import slugify
from app.curriculum import LEVELS
from app.es import ES, ESError
from app.search import get_es

COURSES, CONCEPTS, STANDARDS = "courses", "concepts", "standards"
MAX_MATCHES = 3
STANDARD_HITS = 30  # standards to read when grouping matches by course
UNAVAILABLE = "Courses are unavailable right now."

router = APIRouter()


def normal_state(state: str) -> str:
    return " ".join(state.split()).title()


def known_courses(es: ES, state: str = "") -> list[dict]:
    query = {"term": {"state": normal_state(state)}} if state.strip() else {"match_all": {}}
    res = es.search(COURSES, {"size": 500, "query": query, "_source": ["slug", "course", "state", "level"]})
    courses = [h["_source"] for h in res.get("hits", {}).get("hits", [])]
    return sorted(courses, key=lambda c: (c.get("state", ""), c.get("course", "")))


def standard_name(state: str, course: str) -> str:
    return f"{state} CTE {course} strands"  # the wording teachers use in curriculum/courses


def match_courses(es: ES, state: str, text: str) -> list[dict]:
    state = normal_state(state)
    known = {c["course"].strip().lower(): c for c in known_courses(es, state)}
    body = {"size": STANDARD_HITS, "_source": ["course"],
            "query": {"bool": {"must": [{"match": {"semantic": {"query": text}}}],
                               "filter": [{"prefix": {"source": f"{slugify(state)}-cte-"}},
                                          {"exists": {"field": "course"}}]}}}
    names: list[str] = []
    for hit in es.search(STANDARDS, body).get("hits", {}).get("hits", []):
        name = hit["_source"].get("course")
        if name and name not in names:
            names.append(name)
    typed = slugify(text)
    names.sort(key=lambda n: slugify(n) != typed)  # the exact name, if typed, comes first
    matches = []
    for name in names[:MAX_MATCHES]:
        course = known.get(name.strip().lower())
        matches.append({"course": name, "known": course is not None,
                        "slug": course["slug"] if course else None,
                        "standard": standard_name(state, name)})
    return matches


class MatchRequest(BaseModel):
    state: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=120)


@router.get("/courses")
def courses(state: str = Query("", max_length=60), es: ES = Depends(get_es)) -> dict:
    try:
        return {"courses": known_courses(es, state),
                "levels": [{"slug": slug, "name": name} for slug, name in LEVELS.items()]}
    except ESError:
        raise HTTPException(status_code=502, detail=UNAVAILABLE) from None


@router.post("/courses/match")
def courses_match(req: MatchRequest, es: ES = Depends(get_es)) -> dict:
    try:
        return {"state": normal_state(req.state), "matches": match_courses(es, req.state, req.text)}
    except ESError:
        raise HTTPException(status_code=502, detail=UNAVAILABLE) from None


@router.get("/concepts")
def concepts(es: ES = Depends(get_es)) -> dict:
    try:
        res = es.search(CONCEPTS, {"size": 500, "query": {"match_all": {}},
                                   "_source": ["slug", "concept", "definition"]})
    except ESError:
        raise HTTPException(status_code=502, detail="Concepts are unavailable right now.") from None
    found = [h["_source"] for h in res.get("hits", {}).get("hits", [])]
    return {"concepts": sorted(found, key=lambda c: c.get("concept", ""))}
