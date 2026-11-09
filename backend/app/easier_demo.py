"""Print 'easier than' for the five hand-check talks, for one fictional demo student.

    cd backend && python -m app.easier_demo                       # Maya's three Utah courses
    cd backend && python -m app.easier_demo --courses a,b --state Utah

Needs the concept space (python -m app.concept_space) and ES_API_KEY (or ES_LOCAL_API_KEY).
"""
from __future__ import annotations

import argparse
import sys

from fastapi import HTTPException

from app.easier import build_easier
from app.es import ES, ESError
from app.tag_sessions import CHECK_TITLES

MAYA = "utah-exploring-computer-science,utah-computer-science-principles,utah-computer-programming-1"


def find_id(es: ES, title: str) -> str | None:
    hits = es.search("sessions", {"size": 1, "query": {"match_phrase": {"title": title}}, "_source": ["id"]})
    hits = hits.get("hits", {}).get("hits", [])
    return hits[0]["_source"]["id"] if hits else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", default="Utah")
    p.add_argument("--courses", default=MAYA)
    args = p.parse_args(argv)
    try:
        es = ES.from_env()
        for title in CHECK_TITLES:
            talk_id = find_id(es, title)
            if talk_id is None:
                print(f"\n{title}\n  (not in the sessions index)")
                continue
            try:
                body = build_easier(es, talk_id, args.state, args.courses)
            except HTTPException as e:
                print(f"\n{title}\n  {e.detail}")
                continue
            t = body["talk"]
            print(f"\nEasier than: {t['title']}  [{t['group']}; missing: {', '.join(t['missing']) or 'nothing'}]")
            for r in body["results"]:
                print(f"  {r['group']:9} {r['title']}" + (f"  (missing: {', '.join(r['missing'])})" if r["missing"] else ""))
        return 0
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
