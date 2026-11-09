"""Tag every session with the concepts it assumes, then write the tags onto the sessions index.

    cd backend && python -m app.tag_sessions            # shows what would be sent; sends nothing
    cd backend && python -m app.tag_sessions --yes      # sends the untagged talks as one batch

Steps:
1. Read curriculum/concepts and every session in the index.
2. Skip sessions whose cached entry in data/tags.json still matches (same model, prompt, concept
   table and talk text). Those cost nothing.
3. Send the rest as one Message Batches request (half the price of single calls). The batch id
   is saved to data/.tags-batch.json, so if this stops, running it again resumes the same batch
   instead of paying twice.
4. Write each answer to data/tags.json as tagged or unknown.
5. Write assumes, assumes_reason and assumes_status onto every session, check the counts with
   _count, and print the talks to hand-check.

--apply-only skips steps 2 to 4, for example after the sessions index is reloaded.
The model key comes from ANTHROPIC_API_KEY (or an `ant auth login` profile). Elasticsearch
comes from ES_URL and ES_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from app.curriculum import DEFAULT_DIR, read_concepts
from app.es import ES, ESError
from app.tagging import (build_request, cache_key, custom_id, entry_from_result, session_update,
                         table_fingerprint)

DATA = Path(__file__).resolve().parents[2] / "data"
DEFAULT_MODEL = "claude-haiku-5-5"
INDEX = "sessions"
# Talks to hand-check with Anna once tagging has run.
CHECK_TITLES = [
    "Agents on the Platform: Four Perspectives on the Conversation We're Already Having in Private",
    "TAG Operational Resilience: Shaping the Future Through Initiatives",
    "Demo Theater: The Ultimate Hot Dog Showdown: Scaling AI Inference on Kubernetes",
    "Your GPU Dashboard Is Lying: Per-Tenant Blind Spots in MIG, MPS, and HAMi",
    "OpenFeature as a Control Plane for OpenTelemetry",
]


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, data) -> None:
    """Write atomically, with sorted keys so the file diffs cleanly."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fetch_sessions(es: ES) -> list[dict]:
    res = es.search(INDEX, {"size": 2000, "query": {"match_all": {}},
                            "_source": ["id", "title", "abstract", "track", "level"]})
    sessions = [h["_source"] for h in res["hits"]["hits"]]
    total = res["hits"]["total"]["value"]
    if len(sessions) != total:
        raise SystemExit(f"read {len(sessions)} of {total} sessions; raise the search size")
    return sessions


def plan(sessions: list[dict], tags: dict, concepts: list[dict], fingerprint: str, model: str,
         retry_unknown: bool) -> tuple[list[dict], dict]:
    """Return (batch requests to send, {custom_id: {session_id, key}}) for sessions not cached."""
    todo, metas = [], {}
    for s in sessions:
        key = cache_key(s, fingerprint, model)
        entry = tags.get(s["id"])
        if entry and entry["key"] == key and not (retry_unknown and entry["status"] == "unknown"):
            continue
        todo.append(build_request(s, concepts, model))
        metas[custom_id(s["id"])] = {"session_id": s["id"], "key": key}
    return todo, metas


def to_plain(obj):
    """SDK result objects to plain dicts."""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def run_batch(client, requests: list[dict], metas: dict, state_path: Path, poll_seconds: float) -> str:
    """Create the batch (or resume the saved one) and wait until it has ended. Returns the batch id.

    metas maps custom_id to {session_id, key}. It is saved locally, never sent to the API."""
    state = read_json(state_path, None)
    if state:
        batch_id = state["batch_id"]
        print(f"resuming batch {batch_id}")
    else:
        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id
        write_json(state_path, {"batch_id": batch_id, "requests": metas})
        print(f"created batch {batch_id} with {len(requests)} requests")
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return batch_id
        counts = batch.request_counts
        print(f"  {batch.processing_status}: {counts.processing} processing, {counts.succeeded} done", flush=True)
        time.sleep(poll_seconds)


def collect(client, batch_id: str, state: dict, model: str, slugs: set[str], tags: dict) -> int:
    """Store every batch result in tags; return how many were stored."""
    n = 0
    for r in client.messages.batches.results(batch_id):
        meta = state["requests"].get(r.custom_id)
        if meta is None:
            continue
        result = r.result
        message = to_plain(result.message) if result.type == "succeeded" else None
        error = None
        if result.type == "errored":
            err = to_plain(result.error)
            error = json.dumps(err)[:300] if isinstance(err, dict) else str(err)[:300]
        tags[meta["session_id"]] = entry_from_result(result.type, message, error, meta["key"], model, slugs)
        n += 1
    return n


def apply_to_index(es: ES, sessions: list[dict], tags: dict, fingerprint: str, model: str) -> None:
    es.request("PUT", f"/{INDEX}/_mapping", {"properties": {
        "assumes": {"type": "keyword"}, "assumes_reason": {"type": "text"}, "assumes_status": {"type": "keyword"}}})
    lines = []
    for s in sessions:
        entry = tags.get(s["id"])
        if entry and entry["key"] != cache_key(s, fingerprint, model):
            entry = None  # stale: tagged against a different concept table, model, or talk text
        lines.append(json.dumps({"update": {"_index": INDEX, "_id": s["id"]}}))
        lines.append(json.dumps({"doc": session_update(entry)}))
    for i in range(0, len(lines), 400):
        res = es.request("POST", "/_bulk", "\n".join(lines[i:i + 400]) + "\n", ndjson=True, timeout=300)
        if res.get("errors"):
            bad = [it["update"]["error"] for it in res["items"] if it["update"].get("error")]
            raise SystemExit(f"bulk update errors: {json.dumps(bad[:3])[:800]}")
    es.request("POST", f"/{INDEX}/_refresh")


def count(es: ES, query: dict | None = None) -> int:
    return es.request("POST", f"/{INDEX}/_count", {"query": query} if query else None)["count"]


def show_check_talks(sessions: list[dict], tags: dict, titles: list[str]) -> None:
    by_title = {s.get("title"): s for s in sessions}
    print("\nTalks to hand-check:")
    for title in titles:
        s = by_title.get(title)
        if s is None:
            print(f"\n* {title}\n    not found in the sessions index")
            continue
        entry = tags.get(s["id"])
        status = entry["status"] if entry else "not tagged yet"
        print(f"\n* {title}  [{status}]")
        for t in (entry or {}).get("assumes", []):
            print(f"    {t['concept']}: {t['reason']}")
        if entry and entry["status"] == "tagged" and not entry["assumes"]:
            print("    assumes no concepts")
        for note in (entry or {}).get("notes", []):
            print(f"    note: {note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually send the batch (it costs money)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--curriculum", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--tags", type=Path, default=DATA / "tags.json")
    ap.add_argument("--retry-unknown", action="store_true", help="send unknown sessions again")
    ap.add_argument("--apply-only", action="store_true", help="only write cached tags onto the index")
    ap.add_argument("--poll-seconds", type=float, default=30)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    concepts = read_concepts(args.curriculum)
    if not concepts:
        print(f"No concepts in {args.curriculum / 'concepts'}; there is nothing to tag against.", file=sys.stderr)
        return 1
    slugs, fingerprint = {c["slug"] for c in concepts}, table_fingerprint(concepts)
    es = ES.from_env()
    try:
        sessions = fetch_sessions(es)
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    tags: dict = read_json(args.tags, {})
    state_path = args.tags.with_name(".tags-batch.json")
    print(f"{len(concepts)} concepts, {len(sessions)} sessions, {len(tags)} cached tags, model {args.model}")

    if not args.apply_only:
        todo, metas = plan(sessions, tags, concepts, fingerprint, args.model, args.retry_unknown)
        print(f"{len(sessions) - len(todo)} cached, {len(todo)} to send")
        if state_path.exists():
            print(f"an unfinished batch is recorded in {state_path}; it will be resumed, not resent")
        if todo or state_path.exists():
            if not args.yes:
                print("Nothing sent. Run again with --yes to send them as one batch.")
                return 0
            import anthropic  # only the tagger needs the SDK; the web app never imports it

            client = anthropic.Anthropic()
            batch_id = run_batch(client, todo, metas, state_path, args.poll_seconds)
            state = read_json(state_path, {})
            stored = collect(client, batch_id, state, args.model, slugs, tags)
            write_json(args.tags, tags)
            state_path.unlink(missing_ok=True)
            print(f"stored {stored} results in {args.tags}")

    try:
        apply_to_index(es, sessions, tags, fingerprint, args.model)
        tagged = count(es, {"term": {"assumes_status": "tagged"}})
        unknown = count(es, {"term": {"assumes_status": "unknown"}})
        total = count(es)
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\nsessions _count {total}: tagged {tagged}, unknown {unknown}"
          f"{'' if tagged + unknown == total else '  MISMATCH'}")
    show_check_talks(sessions, tags, CHECK_TITLES)
    return 0 if tagged + unknown == total else 1


if __name__ == "__main__":
    sys.exit(main())
