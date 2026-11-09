"""Concept space: every tagged talk is a point, one dimension per concept in curriculum/concepts.

    cd backend && python -m app.concept_space            # write data/concept_dims.json, add concept_vec to sessions
    cd backend && python -m app.concept_space --dry-run  # print the dimensions and counts, change nothing

Dimensions are the concept slugs sorted alphabetically; the order is saved in data/concept_dims.json
and committed, so a vector always means the same thing. A talk's concept_vec has 1.0 where it assumes
the concept and 0.0 elsewhere. A tagged talk that assumes nothing is the zero vector, which is valid
with l2_norm similarity (cosine would reject it). Untagged talks get no vector.

dense_vector dims can't change once mapped. If the concept table grows, the run stops and says so:
recreate the sessions index (or add a new field) and run this again.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.curriculum import DEFAULT_DIR, read_concepts
from app.es import ES, ESError

INDEX = "sessions"
FIELD = "concept_vec"
DIMS_FILE = Path(__file__).resolve().parents[2] / "data" / "concept_dims.json"


def dims_from_curriculum(directory: Path = DEFAULT_DIR) -> list[str]:
    return sorted(c["slug"] for c in read_concepts(directory))


def load_dims(path: Path = DIMS_FILE) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))["dims"]


def write_dims(dims: list[str], path: Path = DIMS_FILE) -> None:
    path.write_text(json.dumps({"field": FIELD, "similarity": "l2_norm", "dims": dims}, indent=2) + "\n",
                    encoding="utf-8")


def vector(concepts, dims: list[str]) -> list[float]:
    """1.0 for each dimension in concepts, 0.0 elsewhere. Concepts outside the dimensions are ignored."""
    have = set(concepts or [])
    return [1.0 if d in have else 0.0 for d in dims]


def talk_vector(session: dict, dims: list[str]) -> list[float] | None:
    """The talk's point in concept space, or None when it hasn't been tagged."""
    if session.get("assumes_status") != "tagged":
        return None
    return vector(session.get("assumes") or [], dims)


def mapping(dims: list[str]) -> dict:
    # hnsw (not the default int8 quantization): the vectors are tiny and exactly 0/1, keep them exact.
    # The dimension order also goes in the index's _meta, so the API (whose image has no data/) can read it.
    return {"_meta": {"concept_dims": dims}, "properties": {FIELD: {"type": "dense_vector", "dims": len(dims), "index": True,
                                   "similarity": "l2_norm", "index_options": {"type": "hnsw"}}}}


def index_dims(es: ES) -> list[str] | None:
    """The dimension order saved on the sessions index, or None before the first run."""
    res = es.request("GET", f"/{INDEX}/_mapping")
    for idx in res.values():
        dims = (idx.get("mappings", {}).get("_meta") or {}).get("concept_dims")
        if dims:
            return list(dims)
    return None


def existing_dims(es: ES) -> int | None:
    res = es.request("GET", f"/{INDEX}/_mapping/field/{FIELD}")
    for idx in res.values():
        field = idx.get("mappings", {}).get(FIELD, {}).get("mapping", {}).get(FIELD, {})
        if field:
            return field.get("dims")
    return None


def bulk_lines(sessions: list[dict], dims: list[str]) -> list[str]:
    lines = []
    for s in sessions:
        vec = talk_vector(s, dims)
        lines.append(json.dumps({"update": {"_index": INDEX, "_id": s["id"]}}))
        if vec is None:  # untagged: no vector at all
            lines.append(json.dumps({"script": {"source": f"ctx._source.remove('{FIELD}')"}}))
        else:
            lines.append(json.dumps({"doc": {FIELD: vec}}))
    return lines


def fetch_sessions(es: ES) -> list[dict]:
    res = es.search(INDEX, {"size": 2000, "query": {"match_all": {}}, "_source": ["id", "assumes", "assumes_status"]})
    return [h["_source"] for h in res["hits"]["hits"]]


def count(es: ES, query: dict) -> int:
    return es.request("POST", f"/{INDEX}/_count", {"query": query})["count"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    dims = dims_from_curriculum()
    print(f"{len(dims)} dimensions: {', '.join(dims)}")
    try:
        es = ES.from_env()
        sessions = fetch_sessions(es)
        tagged = sum(1 for s in sessions if talk_vector(s, dims) is not None)
        print(f"{len(sessions)} sessions: {tagged} get a vector, {len(sessions) - tagged} untagged get none")
        if args.dry_run:
            return 0
        have = existing_dims(es)
        if have is not None and have != len(dims):
            print(f"error: {INDEX}.{FIELD} is mapped with {have} dims but the curriculum has {len(dims)} concepts. "
                  "dense_vector dims can't change: recreate the sessions index, then run this again.", file=sys.stderr)
            return 1
        es.request("PUT", f"/{INDEX}/_mapping", mapping(dims))  # same dims again is a no-op
        write_dims(dims)
        lines = bulk_lines(sessions, dims)
        for i in range(0, len(lines), 400):
            res = es.request("POST", "/_bulk", "\n".join(lines[i:i + 400]) + "\n", ndjson=True, timeout=300)
            bad = [it["update"].get("error") for it in res.get("items", [])
                   if it["update"].get("error") and it["update"].get("status") != 404]
            if bad:
                print(f"error: bulk update failed: {json.dumps(bad[:3])[:600]}", file=sys.stderr)
                return 1
        es.request("POST", f"/{INDEX}/_refresh")
        with_vec = count(es, {"exists": {"field": FIELD}})
        print(f"wrote {DIMS_FILE.name}; sessions with {FIELD} (_count): {with_vec}")
        return 0 if with_vec == tagged else 1
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
