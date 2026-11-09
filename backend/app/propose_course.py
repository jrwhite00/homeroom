"""Open a course pull request by hand, the same way a student-confirmed match does.

    cd backend && python -m app.propose_course \
        --state Utah --course "Computer Programming 1" --standard "Utah CTE Computer Programming 1 strands" \
        --teaches programming-basics,containers --reason "programming-basics=Variables, loops and functions."

The file says `added by: James`. COURSE_PROPOSAL_GITHUB_TOKEN must contain a repository-scoped token,
never a personal token, and is never printed.
"""
from __future__ import annotations

import argparse
import sys

from app.course_proposals import Proposal, ProposalError, propose
from app.github import GitHub


def main(argv: list[str] | None = None, gh: GitHub | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--course", required=True)
    ap.add_argument("--standard", required=True)
    ap.add_argument("--teaches", required=True, help="comma-separated concept slugs")
    ap.add_argument("--reason", action="append", default=[], metavar="SLUG=TEXT",
                    help="one line of reasoning for a concept; repeat for each")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    reasons = dict(r.split("=", 1) for r in args.reason if "=" in r)
    concepts = [{"slug": s.strip(), "reason": reasons.get(s.strip(), "")} for s in args.teaches.split(",") if s.strip()]
    p = Proposal(state=args.state, course=args.course, standard=args.standard, concepts=concepts, added_by="James")
    try:
        result = propose(gh or GitHub.from_env(), p)
    except ProposalError as e:
        print(f"Not opened: {e}", file=sys.stderr)
        return 1
    if result["status"] == "saved_locally":
        print(f"GitHub didn't accept it; saved data/proposed-courses/{result['file']} instead.", file=sys.stderr)
        return 1
    print({"opened": f"Opened {result['url']}", "already_open": f"Already open: {result['url']}",
           "already_known": "That course is already in the curriculum."}[result["status"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
