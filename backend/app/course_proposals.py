"""Propose a course the curriculum doesn't know yet, as a pull request a teacher reviews.

The course file is curriculum/courses/<state>-<course>.yaml in the teachers' simple `name: value`
format. It goes on its own branch, course/<state>-<course>, so there is at most one open pull request
per course: proposing the same course again returns the open one.

Nothing about the student goes anywhere: not in the file, the branch, the commit or the pull request.
A student-confirmed match is recorded as "added by: Homeroom", the format teachers practiced; the
pull request body says a student confirmed the match.

The checks mirror .gitar/rules/curriculum.md, so a proposal that Gitar would reject is refused before
anything is opened: every concept must exist on main, at most 4 concepts, the standard must be
filled in, and no value may hold a line break or an email-like '@'.

If GitHub can't be reached or refuses, the file is written locally to data/proposed-courses/ (never
straight into curriculum/, which changes only through reviewed pull requests) and the student's
schedule is shown anyway.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.es import ES, ESError
from app.github import GitHub, GitHubError

STUDENT_ADDED_BY = "Homeroom"
MAX_CONCEPTS = 4
MAX_OPEN_COURSE_PRS = 5
MAX_LOCAL_PROPOSALS = 20
LOCAL_DIR = Path(__file__).resolve().parents[2] / "data" / "proposed-courses"
FOOTER = "A teacher must approve before this merges."
LIMIT_MESSAGE = "Course proposals are full right now. You can still view your schedule."
UNAVAILABLE_MESSAGE = "We couldn't send your course to a teacher right now. You can still view your schedule."
CONCEPTS_INDEX = "concepts"


class ProposalError(ValueError):
    """The proposal can't be opened as it is (the message is safe to show)."""


@dataclass
class Proposal:
    state: str
    course: str
    standard: str
    concepts: list[dict] = field(default_factory=list)  # [{"slug": ..., "reason": ...}]
    added_by: str = STUDENT_ADDED_BY

    @property
    def slugs(self) -> list[str]:
        return [c["slug"] for c in self.concepts]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def file_name(p: Proposal) -> str:
    return f"{slugify(p.state)}-{slugify(p.course)}.yaml"


def branch(p: Proposal) -> str:
    return f"course/{file_name(p)[:-5]}"


def path(p: Proposal) -> str:
    return f"curriculum/courses/{file_name(p)}"


def validate(p: Proposal) -> None:
    for name, value in (("state", p.state), ("course", p.course), ("standard", p.standard),
                        ("added by", p.added_by), *((f"reason for {c['slug']}", c.get("reason", "")) for c in p.concepts)):
        if "\n" in value or "\r" in value:
            raise ProposalError(f"The {name} can't contain a line break.")
        if "@" in value:
            raise ProposalError(f"The {name} can't contain '@'. Courses describe classes, never people.")
    if not p.state.strip() or not p.course.strip() or not slugify(p.course):
        raise ProposalError("A course needs a name and a state.")
    if not p.standard.strip():
        raise ProposalError("Name the state or national standard the course comes from.")
    if not p.concepts:
        raise ProposalError("A course needs at least one concept it teaches.")
    if len(p.concepts) > MAX_CONCEPTS:
        raise ProposalError(f"A course lists at most {MAX_CONCEPTS} concepts.")
    if len(set(p.slugs)) != len(p.slugs):
        raise ProposalError("A concept is listed twice.")


def course_file(p: Proposal) -> str:
    return (f"course: {p.course.strip()}\nstate: {p.state.strip()}\nstandard: {p.standard.strip()}\n"
            f"teaches: {', '.join(p.slugs)}\nadded by: {p.added_by}\n")


def title(p: Proposal) -> str:
    return f"Add course: {p.course.strip()} ({p.state.strip()})"


def body(p: Proposal) -> str:
    opened = ("Opened by Homeroom after a student confirmed a match for a course the curriculum doesn't "
              "list yet. It contains no student data." if p.added_by == STUDENT_ADDED_BY else
              f"Opened by hand by {p.added_by}.")
    lines = [opened, "", f"`{path(p)}` says the course teaches:", ""]
    lines += [f"- **{c['slug']}**: {c.get('reason') or 'No reasoning given.'}" for c in p.concepts]
    return "\n".join(lines + ["", FOOTER])


def known_concepts(gh: GitHub) -> set[str]:
    items = gh.request("GET", "/contents/curriculum/concepts?ref=main")
    return {i["name"][:-5] for i in items if i["name"].endswith(".yaml")}


def open_course_pr_count(gh: GitHub) -> int:
    """Count every open course/* pull request, following GitHub's pagination."""
    count, page = 0, 1
    while True:
        pulls = gh.request("GET", f"/pulls?state=open&per_page=100&page={page}")
        count += sum(pull.get("head", {}).get("ref", "").startswith("course/") for pull in pulls)
        if len(pulls) < 100:
            return count
        page += 1


def open_pull_request(gh: GitHub, p: Proposal) -> dict:
    """Open (or find) the pull request for this course. Raises GitHubError if GitHub fails."""
    owner = gh.repo.split("/")[0]
    existing = gh.request("GET", f"/pulls?state=open&head={owner}:{branch(p)}")
    if existing:
        return {"status": "already_open", "url": existing[0]["html_url"], "number": existing[0]["number"]}
    try:
        gh.request("GET", f"/contents/{path(p)}?ref=main")
        return {"status": "already_known", "url": None, "number": None}
    except GitHubError as e:
        if e.status != 404:
            raise
    unknown = sorted(set(p.slugs) - known_concepts(gh))
    if unknown:
        raise ProposalError("These concepts aren't in the curriculum yet: " + ", ".join(unknown) + ".")
    if open_course_pr_count(gh) >= MAX_OPEN_COURSE_PRS:
        return {"status": "limit_reached", "url": None, "number": None, "message": LIMIT_MESSAGE}
    main_sha = gh.request("GET", "/git/ref/heads/main")["object"]["sha"]
    try:
        gh.request("POST", "/git/refs", {"ref": f"refs/heads/{branch(p)}", "sha": main_sha})
    except GitHubError as e:
        if e.status != 422:  # 422: the branch is left from an earlier, closed proposal; reuse it
            raise
    put = {"message": title(p), "branch": branch(p),
           "content": base64.b64encode(course_file(p).encode()).decode()}
    try:
        put["sha"] = gh.request("GET", f"/contents/{path(p)}?ref={branch(p)}")["sha"]
    except GitHubError as e:
        if e.status != 404:
            raise
    gh.request("PUT", f"/contents/{path(p)}", put)
    pr = gh.request("POST", "/pulls", {"title": title(p), "head": branch(p), "base": "main", "body": body(p)})
    return {"status": "opened", "url": pr["html_url"], "number": pr["number"]}


class ProposalLimitReached(Exception):
    """The local proposal fallback has reached its storage cap."""


def save_locally(p: Proposal, directory: Path = LOCAL_DIR) -> Path:
    target = directory / file_name(p)
    if not target.exists() and directory.exists():
        file_count = sum(item.is_file() for item in directory.iterdir())
        if file_count >= MAX_LOCAL_PROPOSALS:
            raise ProposalLimitReached
    directory.mkdir(parents=True, exist_ok=True)
    target.write_text(course_file(p), encoding="utf-8")
    return target


def concepts_in_index(es: ES | None) -> set[str]:
    """Concept slugs in the Elasticsearch concepts index (loaded from main), or an empty set if unreadable.

    Used when GitHub can't be reached. The backend image holds only app/, so curriculum/ isn't there.
    """
    if es is None:
        return set()
    try:
        res = es.search(CONCEPTS_INDEX, {"size": 1000, "_source": False, "query": {"match_all": {}}})
    except ESError:
        return set()
    return {hit["_id"] for hit in res.get("hits", {}).get("hits", [])}


def propose(gh: GitHub, p: Proposal, local_dir: Path | None = None, es: ES | None = None) -> dict:
    """Validate, then open the pull request, or save the file locally if GitHub fails."""
    validate(p)
    try:
        return open_pull_request(gh, p)
    except GitHubError:
        known = concepts_in_index(es)
        if not known:
            # Neither GitHub nor Elasticsearch can confirm the concepts: save nothing.
            return {"status": "not_saved", "url": None, "number": None, "message": UNAVAILABLE_MESSAGE}
        unknown = sorted(set(p.slugs) - known)
        if unknown:
            raise ProposalError("These concepts aren't in the curriculum yet: " + ", ".join(unknown) + ".")
        try:
            save_locally(p, local_dir or LOCAL_DIR)
        except ProposalLimitReached:
            return {"status": "limit_reached", "url": None, "number": None, "message": LIMIT_MESSAGE}
        return {"status": "saved_locally", "url": None, "number": None, "file": file_name(p)}
