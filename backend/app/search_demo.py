"""Run one query against the real sessions index in both modes and print them side by side.

    cd backend && python -m app.search_demo "autoscaling load balancing high availability"

Uses the same query builder as GET /search. Connection comes from ES_URL and ES_API_KEY
(or ES_LOCAL_URL and ES_LOCAL_API_KEY); see app/es.py.
"""
from __future__ import annotations

import argparse
import sys

from app.es import ES, ESError
from app.search import run, translated_text


def show(title: str, results: list[dict]) -> None:
    print(f"\n-- {title} --")
    if not results:
        print("  (no results)")
    for i, r in enumerate(results, 1):
        when = (r["starts_at"] or "")[:16].replace("T", " ")
        print(f"{i:3}. {r['score']:8.4f}  {when}  {r['title']}")
        print(f"     {', '.join(r['speakers']) or 'no speakers listed'} | {r['room']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("q", help="search text")
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--translate", action="store_true", help="translate the question through the concepts first")
    ap.add_argument("--concepts-index", default="concepts", help="for example practice-concepts")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    es = ES.from_env()
    print(f"QUERY: {args.q}   (sessions index at {es.url})")
    text = args.q
    if args.translate:
        text, translation = translated_text(es, args.q, args.concepts_index)
        if translation:
            print(f"TRANSLATED via {', '.join(translation['concepts'])} -> {text}")
        else:
            print(f"no concept matched in {args.concepts_index}; searching the question as written")
    try:
        show("hybrid: BM25 + semantic, fused with rrf", run(es, text, "hybrid", args.size))
        show("keyword: BM25 only", run(es, text, "keyword", args.size))
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
