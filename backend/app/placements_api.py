"""GET /placements?class=: where each student's hours landed, as the controller loop last saw it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.es import ES, ESError
from app.search import get_es

router = APIRouter()


@router.get("/placements")
def placements(class_name: str = Query(alias="class", pattern=r"^[a-z0-9-]{1,32}$"),
               es: ES = Depends(get_es)) -> dict:
    try:
        res = es.search("placements", {"size": 1000, "query": {"term": {"class": class_name}},
                                       "sort": [{"student": "asc"}, {"slot": "asc"}]})
    except ESError:
        raise HTTPException(status_code=502, detail="Placements are unavailable right now.") from None
    out = []
    for h in res["hits"]["hits"]:
        s = h["_source"]
        out.append({"student": s["student"], "slot": s["slot"], "knows": s.get("knows", []),
                    **(s.get("status") or {"phase": "Unknown"}), "updated_at": s.get("updated_at")})
    return {"class": class_name, "placements": out}
