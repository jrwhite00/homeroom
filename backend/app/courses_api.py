"""POST /courses/propose: a student confirmed a match for a course we don't know; propose it to teachers."""
from __future__ import annotations

from functools import lru_cache
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.course_proposals import Proposal, ProposalError, propose
from app.es import ES
from app.github import GitHub
from app.search import get_es

router = APIRouter()


class Concept(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    reason: str = Field(min_length=1, max_length=300)


class CourseProposal(BaseModel):
    """What the student page sends. There is deliberately no field for the student."""
    state: str = Field(min_length=1, max_length=60)
    course: str = Field(min_length=1, max_length=120)
    standard: str = Field(min_length=1, max_length=200)
    teaches: list[Concept] = Field(min_length=1)


@lru_cache
def get_github() -> GitHub:
    return GitHub.from_env()


# Behind the Envoy gateway every request has the gateway's address, so this is one shared limit of
# 3 proposals a minute for everyone, not per student. Fine for the single-laptop demo.
RATE_LIMIT = 3
RATE_WINDOW_SECONDS = 60
_client_proposals: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
_proposal_lock = threading.Lock()


def _rate_limited(client: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _rate_limit_lock:
        for key, timestamps in list(_client_proposals.items()):
            active = [timestamp for timestamp in timestamps if now - timestamp < RATE_WINDOW_SECONDS]
            if active:
                _client_proposals[key] = active
            else:
                del _client_proposals[key]
        timestamps = _client_proposals.setdefault(client, [])
        if len(timestamps) >= RATE_LIMIT:
            return True
        timestamps.append(now)
        return False


@router.post("/courses/propose", status_code=202)
def propose_course(proposal: CourseProposal, request: Request, gh: GitHub = Depends(get_github),
                   es: ES = Depends(get_es)) -> dict:
    client = request.client.host if request.client else "unknown"
    if _rate_limited(client):
        return {"status": "rate_limited", "message": "Please wait a minute before suggesting another course.",
                "show_schedule": True}

    p = Proposal(state=proposal.state, course=proposal.course, standard=proposal.standard,
                 concepts=[c.model_dump() for c in proposal.teaches])
    try:
        # Keep the count-then-open flow atomic within this single-replica demo.
        with _proposal_lock:
            result = propose(gh, p, es=es)
    except ProposalError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    # Whatever happened with GitHub, the student page goes on to show the schedule.
    return {**result, "show_schedule": True}
