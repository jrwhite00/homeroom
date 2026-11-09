"""Index curriculum/concepts and curriculum/courses into the "concepts" and "courses" indexes.

    cd backend && python -m app.load_curriculum [--recreate] [--dir PATH] [--index-prefix PREFIX]

Each concept's definition is copied into a semantic_text field (ELSER), which translate() uses
to find the concepts closest to a question. Courses are stored as they are written.

It refuses to load if a course teaches a concept that has no concept file. After loading it
checks each index with _count (not _cat/indices) and exits 1 on a mismatch.

Connection comes from ES_URL and ES_API_KEY (or ES_LOCAL_URL and ES_LOCAL_API_KEY).
--index-prefix loads into other indexes, for example to try a practice table without
touching the real ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.curriculum import DEFAULT_DIR, missing_concepts, read_concepts, read_courses
from app.es import ES, ESError

DEFAULT_INFERENCE = ".elser-2-elasticsearch"
KW = {"type": "keyword"}


def concepts_mapping(inference_id: str = DEFAULT_INFERENCE) -> dict:
    return {"mappings": {"properties": {
        "slug": KW,
        "concept": {"type": "text", "fields": {"raw": KW}},
        "definition": {"type": "text", "copy_to": "semantic"},
        "technical_words": KW,
        "semantic": {"type": "semantic_text", "inference_id": inference_id},
    }}}


def courses_mapping() -> dict:
    return {"mappings": {"properties": {
        "slug": KW,
        "course": {"type": "text", "fields": {"raw": KW}},
        "state": KW,
        "standard": {"type": "text"},
        "teaches": KW,
        "added_by": KW,
    }}}


def load_index(es: ES, index: str, mapping: dict, docs: list[dict], recreate: bool) -> None:
    """Create the index if needed (or recreate it), then bulk-index docs by slug."""
    exists = es.request("HEAD", f"/{index}", ok404=True) is not None
    if exists and recreate:
        es.request("DELETE", f"/{index}")
        exists = False
    if not exists:
        es.request("PUT", f"/{index}", mapping)
        print(f"created {index}")
    if docs:
        body = "".join(json.dumps({"index": {"_index": index, "_id": d["slug"]}}) + "\n" + json.dumps(d) + "\n"
                       for d in docs)
        res = es.request("POST", "/_bulk", body, ndjson=True, timeout=600)
        errors = [i["index"]["error"] for i in res.get("items", []) if i["index"].get("error")]
        if errors:
            raise ESError(f"bulk errors in {index}: {json.dumps(errors[:3])[:800]}")
    es.request("POST", f"/{index}/_refresh")


def counts_match(es: ES, expected: dict[str, int]) -> bool:
    """Print file vs _count for each index and return True if all match."""
    ok = True
    for index, n in expected.items():
        indexed = es.request("GET", f"/{index}/_count")["count"]
        ok &= indexed == n
        print(f"{index:24} files {n:4}  _count {indexed:4} {'' if indexed == n else 'MISMATCH'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="curriculum folder (read only)")
    ap.add_argument("--recreate", action="store_true", help="delete and recreate both indexes first")
    ap.add_argument("--index-prefix", default="", help="for example 'practice-' to load practice-concepts")
    ap.add_argument("--inference-id", default=DEFAULT_INFERENCE)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    concepts, courses = read_concepts(args.dir), read_courses(args.dir)
    print(f"read {len(concepts)} concepts and {len(courses)} courses from {args.dir}")
    if missing := missing_concepts(courses, concepts):
        for course, slug in missing:
            print(f"course {course} teaches '{slug}', but there is no concepts/{slug}.yaml", file=sys.stderr)
        return 1

    es = ES.from_env()
    concepts_index, courses_index = f"{args.index_prefix}concepts", f"{args.index_prefix}courses"
    try:
        load_index(es, concepts_index, concepts_mapping(args.inference_id), concepts, args.recreate)
        load_index(es, courses_index, courses_mapping(), courses, args.recreate)
        ok = counts_match(es, {concepts_index: len(concepts), courses_index: len(courses)})
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
