"""POST /courses/propose: a student confirmed a match for a course we don't know; propose it to teachers."""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.course_proposals import Proposal, ProposalError, propose
from app.github import GitHub

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


@router.post("/courses/propose", status_code=202)
def propose_course(proposal: CourseProposal, gh: GitHub = Depends(get_github)) -> dict:
    p = Proposal(state=proposal.state, course=proposal.course, standard=proposal.standard,
                 concepts=[c.model_dump() for c in proposal.teaches])
    try:
        result = propose(gh, p)
    except ProposalError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    # Whatever happened with GitHub, the student page goes on to show the schedule.
    return {**result, "show_schedule": True}
