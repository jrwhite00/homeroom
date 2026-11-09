"""GET /week?state=&courses=&knows=: sort every talk into ready, one step away, not yet and unknown.

The student's concepts are the union of what their chosen courses teach (the `teaches` field in
the courses index) and any concepts they ticked on the checklist (knows=, concept slugs), for a
student whose course we don't know. Each talk's group comes from James's readiness() in
backend/readiness.py, which this module only calls. Missing concepts are shown by their name from the concepts index.

For each concept that keeps a talk one step away, we suggest one way to pick it up first:
a Beginner-level talk about that concept that doesn't itself assume it, or, if there is none,
a project booth (kiosks index) whose project matches the concept's technical words.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.es import ES, ESError
from app.search import get_es, talk_filter
from app.telemetry import tracer
from readiness import NOT_YET, ONE_STEP, READY, UNKNOWN, readiness

SESSIONS, COURSES, CONCEPTS, KIOSKS = "sessions", "courses", "concepts", "kiosks"
GROUPS = [READY, ONE_STEP, NOT_YET, UNKNOWN]
LABELS = {READY: "Ready", ONE_STEP: "One step away", NOT_YET: "Not yet", UNKNOWN: "Not sorted yet"}
TALK_SOURCE = ["id", "title", "starts_at", "room", "level", "assumes", "assumes_status"]
MAX_TALKS = 2000

router = APIRouter()


def split_courses(courses: str) -> list[str]:
    seen: list[str] = []
    for slug in (c.strip() for c in courses.split(",")):
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def find_courses(es: ES, state: str, slugs: list[str]) -> tuple[list[dict], list[str]]:
    """Return (courses found for this state, slugs we don't know)."""
    res = es.search(COURSES, {"size": len(slugs), "query": {"ids": {"values": slugs}}})
    found = {h["_source"]["slug"]: h["_source"] for h in res.get("hits", {}).get("hits", [])
             if h["_source"].get("state", "").strip().lower() == state.strip().lower()}
    return [found[s] for s in slugs if s in found], [s for s in slugs if s not in found]


def unknown_concept_message(slug: str) -> str:
    return f"We don't know the concept \"{slug}\" yet, so it isn't counted. Pick it again from the list."


def unknown_course_message(slug: str, state: str) -> str:
    return (f'We don\'t know the course "{slug}" for {state} yet, so it isn\'t counted. '
            "Check the spelling, or ask a teacher to add it.")


def concept_table(es: ES) -> dict[str, dict]:
    res = es.search(CONCEPTS, {"size": 500, "query": {"match_all": {}},
                               "_source": ["slug", "concept", "technical_words"]})
    return {h["_source"]["slug"]: h["_source"] for h in res.get("hits", {}).get("hits", [])}


def concept_name(concepts: dict[str, dict], slug: str) -> str:
    return concepts.get(slug, {}).get("concept") or slug


def all_talks(es: ES) -> list[dict]:
    res = es.search(SESSIONS, {"size": MAX_TALKS, "query": talk_filter(), "_source": TALK_SOURCE,
                               "sort": [{"starts_at": "asc"}]})
    return [h["_source"] for h in res.get("hits", {}).get("hits", [])]


def talk_assumes(talk: dict) -> list[str] | None:
    """The concepts a talk assumes, or None when it hasn't been tagged (readiness() says unknown)."""
    if talk.get("assumes_status") != "tagged":
        return None
    return list(talk.get("assumes") or [])


def search_words(concept: dict) -> str:
    return " ".join([concept.get("concept", "")] + list(concept.get("technical_words") or []))


def beginner_talk(es: ES, slug: str, concept: dict, before: str | None) -> dict | None:
    if before is None:
        return None
    query = {"bool": {"must": [{"multi_match": {"query": search_words(concept), "fields": ["title", "abstract"]}}],
                      "filter": [talk_filter(), {"term": {"level": "Beginner"}},
                                 {"term": {"assumes_status": "tagged"}},
                                 {"range": {"starts_at": {"lt": before}}}],
                      "must_not": [{"term": {"assumes": slug}}]}}
    hits = es.search(SESSIONS, {"size": 1, "query": query, "_source": ["title", "starts_at", "room"]})
    hits = hits.get("hits", {}).get("hits", [])
    if not hits:
        return None
    s = hits[0]["_source"]
    return {"kind": "beginner_talk", "title": s.get("title"), "starts_at": s.get("starts_at"), "room": s.get("room")}


def project_booth(es: ES, concept: dict) -> dict | None:
    hits = es.search(KIOSKS, {"size": 1, "query": {"match": {"project": search_words(concept)}},
                              "_source": ["project", "kiosk", "starts_at", "ends_at", "room_short"]})
    hits = hits.get("hits", {}).get("hits", [])
    if not hits:
        return None
    k = hits[0]["_source"]
    return {"kind": "project_booth", "project": k.get("project"), "kiosk": k.get("kiosk"),
            "starts_at": k.get("starts_at"), "ends_at": k.get("ends_at"), "room": k.get("room_short")}


def suggestion(es: ES, slug: str, concepts: dict[str, dict], before: str | None) -> dict | None:
    concept = concepts.get(slug, {"concept": slug})
    found = beginner_talk(es, slug, concept, before) or project_booth(es, concept)
    if found:
        found["covers"] = concept_name(concepts, slug)
    return found


def build_week(es: ES, state: str, course_slugs: list[str], ticked: list[str] | None = None) -> dict:
    state = " ".join(state.split()).title()  # "utah" and "Utah" are the same state
    courses, unknown = find_courses(es, state, course_slugs) if course_slugs else ([], [])
    concepts = concept_table(es)
    ticked = ticked or []
    knows = {slug for c in courses for slug in c.get("teaches", [])} | {s for s in ticked if s in concepts}
    groups: dict[str, list[dict]] = {g: [] for g in GROUPS}
    suggestions: dict[tuple[str, str | None], dict | None] = {}
    for talk in all_talks(es):
        group, missing = readiness(talk_assumes(talk), knows)
        item = {"id": talk.get("id"), "title": talk.get("title"), "starts_at": talk.get("starts_at"),
                "room": talk.get("room"), "level": talk.get("level"),
                "missing": [concept_name(concepts, m) for m in missing]}
        if group == ONE_STEP:
            suggestion_key = (missing[0], talk.get("starts_at"))
            if suggestion_key not in suggestions:
                suggestions[suggestion_key] = suggestion(es, missing[0], concepts, talk.get("starts_at"))
            item["suggestion"] = suggestions[suggestion_key]
        groups[group].append(item)
    return {
        "state": state,
        "courses": [{"slug": c["slug"], "course": c.get("course")} for c in courses],
        "messages": [unknown_course_message(s, state) for s in unknown]
                    + [unknown_concept_message(s) for s in ticked if s not in concepts],
        "knows": sorted(concept_name(concepts, s) for s in knows),
        "labels": LABELS,
        "counts": {g: len(groups[g]) for g in GROUPS},
        "groups": groups,
    }


@router.get("/week")
def week(state: str = Query(..., min_length=1), courses: str = Query("", description="comma-separated course slugs"),
         knows: str = Query("", description="comma-separated concept slugs, from the concept checklist"),
         es: ES = Depends(get_es)) -> dict:
    slugs = split_courses(courses)
    with tracer.start_as_current_span("homeroom.week") as span:  # counts only, never the student
        try:
            body = build_week(es, state, slugs, split_courses(knows))
        except ESError:
            span.set_attribute("week.error", True)
            raise HTTPException(status_code=502, detail="Your week is unavailable right now.") from None
        span.set_attribute("week.courses_known", len(body["courses"]))
        span.set_attribute("week.concepts_known", len(body["knows"]))
        for group, n in body["counts"].items():
            span.set_attribute(f"week.{group}", n)
        return body
