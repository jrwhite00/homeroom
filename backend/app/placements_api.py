"""GET /placements?class=: where each student's hours landed, as the controller loop last saw it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.es import ES, ESError
from app.placement import PSEUDONYM
from app.search import get_es
from app.telemetry import refresh_placement_gauges, tracer

router = APIRouter()


def all_placements(es: ES) -> list[dict]:
    """Every placement, for the students pending / three-talks-a-day gauges."""
    res = es.search("placements", {"size": 10000, "_source": ["class", "student", "slot", "status.phase"]})
    return [h["_source"] for h in res["hits"]["hits"]]


@router.get("/placements")
def placements(class_name: str = Query(alias="class", pattern=r"^[a-z0-9-]{1,32}$"),
               student: str | None = Query(None, pattern=PSEUDONYM.pattern),
               es: ES = Depends(get_es)) -> dict:
    with tracer.start_as_current_span("homeroom.scheduling", attributes={"class": class_name}) as span:
        try:
            terms = [{"term": {"class": class_name}}] + ([{"term": {"student": student}}] if student else [])
            res = es.search("placements", {"size": 1000, "query": {"bool": {"filter": terms}},
                                           "sort": [{"student": "asc"}, {"slot": "asc"}]})
        except ESError:
            span.set_attribute("scheduling.error", "elasticsearch")
            raise HTTPException(status_code=502, detail="Placements are unavailable right now.") from None
        out = []
        for h in res["hits"]["hits"]:
            s = h["_source"]
            out.append({"student": s["student"], "slot": s["slot"], "knows": s.get("knows", []),
                        **(s.get("status") or {"phase": "Unknown"}), "updated_at": s.get("updated_at")})
        span.set_attribute("scheduling.placements", len(out))
        span.set_attribute("scheduling.pending", sum(1 for p in out if p.get("phase") == "Pending"))
        span.set_attribute("scheduling.running", sum(1 for p in out if p.get("phase") == "Running"))
    return {"class": class_name, "placements": out}


def refresh_gauges(es: ES) -> None:
    """Called periodically by telemetry's background refresher."""
    try:
        refresh_placement_gauges(all_placements(es))
    except ESError:
        pass  # no placements index yet, or Elasticsearch unreachable: keep the last values
