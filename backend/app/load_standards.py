"""Index data/standards/*.txt into the Elasticsearch "standards" index, one document per standard.

    cd backend && python -m app.load_standards [--recreate] [--dry-run]

Connection comes from the environment, never from arguments, so the key stays out of shell history:
    ES_URL      default http://localhost:9200 (falls back to ES_LOCAL_URL)
    ES_API_KEY  (falls back to ES_LOCAL_API_KEY, the name start-local uses)

After loading it compares each source's document count from _count with the number of standards
parsed from that file, and exits 1 if any differ. It uses _count rather than _cat/indices because
semantic_text chunks inflate docs.count.

Standard library only, like the schedule loader.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.standards import DEFAULT_DIR, parse_all

INDEX = "standards"
DEFAULT_INFERENCE = ".elser-2-elasticsearch"


def mapping(inference_id: str = DEFAULT_INFERENCE) -> dict:
    kw = {"type": "keyword"}
    return {
        "mappings": {
            "properties": {
                "id": kw, "source": kw, "framework": kw, "course": kw,
                "code": kw, "grade_band": kw, "strand": kw,
                "statement": {"type": "text"},
                "description": {"type": "text", "copy_to": "semantic"},
                "semantic": {"type": "semantic_text", "inference_id": inference_id},
            }
        }
    }


class ES:
    def __init__(self, url: str, api_key: str | None):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}

    def req(self, method: str, path: str, body=None, ndjson: bool = False, ok404: bool = False, timeout: int = 900):
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            headers["Content-Type"] = "application/x-ndjson" if ndjson else "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if ok404 and e.code == 404:
                return None
            raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:1000]}")


def bulk_body(index: str, docs: list[dict]) -> str:
    return "".join(json.dumps({"index": {"_index": index, "_id": d["id"]}}) + "\n" + json.dumps(d) + "\n"
                   for d in docs)


def load(es: ES, by_source: dict[str, list[dict]], recreate: bool, inference_id: str, batch: int = 50) -> None:
    exists = es.req("HEAD", f"/{INDEX}", ok404=True) is not None
    if exists and recreate:
        es.req("DELETE", f"/{INDEX}")
        exists = False
    if not exists:
        es.req("PUT", f"/{INDEX}", mapping(inference_id))
        print(f"created {INDEX}")
    docs = [d for source_docs in by_source.values() for d in source_docs]
    for i in range(0, len(docs), batch):
        res = es.req("POST", "/_bulk", bulk_body(INDEX, docs[i:i + batch]), ndjson=True)
        errors = [item["index"]["error"] for item in res.get("items", []) if item["index"].get("error")]
        if errors:
            raise SystemExit(f"bulk errors: {json.dumps(errors[:3])[:1000]}")
        print(f"  {min(i + batch, len(docs))}/{len(docs)}", flush=True)
    es.req("POST", f"/{INDEX}/_refresh")


def compare_counts(es: ES, by_source: dict[str, list[dict]]) -> bool:
    """Print parsed vs indexed per source (via _count) and return True if they all match."""
    ok = True
    print(f"{'source':42} {'file':>5} {'index':>6}")
    for source, docs in by_source.items():
        indexed = es.req("POST", f"/{INDEX}/_count", {"query": {"term": {"source": source}}})["count"]
        ok &= indexed == len(docs)
        print(f"{source:42} {len(docs):5} {indexed:6} {'' if indexed == len(docs) else 'MISMATCH'}")
    total_parsed = sum(len(d) for d in by_source.values())
    total_indexed = es.req("GET", f"/{INDEX}/_count")["count"]
    ok &= total_indexed == total_parsed
    print(f"{'total':42} {total_parsed:5} {total_indexed:6} {'' if total_indexed == total_parsed else 'MISMATCH'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="folder of standards .txt files")
    ap.add_argument("--recreate", action="store_true", help="delete and recreate the index first")
    ap.add_argument("--dry-run", action="store_true", help="parse and print counts, no Elasticsearch")
    ap.add_argument("--inference-id", default=DEFAULT_INFERENCE)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    by_source = parse_all(args.dir)
    if args.dry_run:
        for source, docs in by_source.items():
            print(f"{source:42} {len(docs):5}")
        print(f"{'total':42} {sum(len(d) for d in by_source.values()):5}")
        return 0

    es = ES(os.environ.get("ES_URL") or os.environ.get("ES_LOCAL_URL") or "http://localhost:9200",
            os.environ.get("ES_API_KEY") or os.environ.get("ES_LOCAL_API_KEY"))
    print("Elasticsearch", es.req("GET", "/")["version"]["number"], "at", es.url)
    load(es, by_source, args.recreate, args.inference_id)
    return 0 if compare_counts(es, by_source) else 1


if __name__ == "__main__":
    sys.exit(main())
