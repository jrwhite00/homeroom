"""Place students with the real kube-scheduler, and keep the API's view of where they landed.

    cd backend && python -m app.placer place --class demo --student maya --slot thu-1430 --knows a,b
    cd backend && python -m app.placer learn --class demo --student maya --concept containers
    cd backend && python -m app.placer watch --class demo [--once]
    cd backend && python -m app.placer cancel talk-1250326        # NoExecute: evicts its students
    cd backend && python -m app.placer uncancel talk-1250326
    cd backend && python -m app.placer test-node create|delete     # simulated node for the Pending check

The desired placements (class, pseudonym, slot, concepts known) are stored in the Elasticsearch
"placements" index. `watch` is the controller loop: it reads each pod's phase and the scheduler's
FailedScheduling events into that index, which GET /placements serves, and it recreates any pod
that is gone. A talk cancelled with a NoExecute taint evicts its students, and an evicted bare pod
is not recreated by Kubernetes itself.

Logs show counts only. Pseudonyms never appear in log lines.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from app.es import ES, ESError
from app.kube import KubeError, Kubectl
from app.placement import (CANCEL_TAINT, MANAGED_BY, MANAGER, PREREQ, SLOT_LABEL, check_pseudonym, namespace,
                           namespace_manifest, pod_name, student_pod, summarize, toleration)

INDEX = "placements"
TEST_NODE = "simulated-prereq-test"
TEST_SLOT = "test-0000"


def doc_id(class_name: str, pseudonym: str, slot: str) -> str:
    return f"{class_name}:{pseudonym}:{slot}"


def ensure_index(es: ES) -> None:
    if es.request("HEAD", f"/{INDEX}", ok404=True) is None:
        kw = {"type": "keyword"}
        es.request("PUT", f"/{INDEX}", {"mappings": {"properties": {
            "class": kw, "student": kw, "slot": kw, "knows": kw, "updated_at": {"type": "date"},
            "status": {"properties": {"phase": kw, "talk": kw, "title": {"type": "text"},
                                      "message": {"type": "text"}, "plain": {"type": "text"},
                                      "missing": {"properties": {"slug": kw, "name": kw}}}}}}})


def desired(es: ES, class_name: str) -> list[dict]:
    res = es.request("POST", f"/{INDEX}/_search", {"size": 1000, "query": {"term": {"class": class_name}}})
    return [h["_source"] for h in res["hits"]["hits"]]


def ensure_namespace(kube: Kubectl, class_name: str) -> None:
    if not kube.run("get", "namespace", namespace(class_name), "--ignore-not-found", "-o", "name").strip():
        kube.run("create", "-f", "-", stdin=json.dumps(namespace_manifest(class_name)))


def get_pod(kube: Kubectl, class_name: str, name: str) -> dict | None:
    out = kube.run("get", "pod", name, "-n", namespace(class_name), "--ignore-not-found", "-o", "json")
    return json.loads(out) if out.strip() else None


def add_tolerations(kube: Kubectl, pod: dict, knows: list[str]) -> int:
    """Kubernetes lets a pod gain tolerations; a Pending pod is then retried by the scheduler."""
    have = {t.get("key") for t in pod["spec"].get("tolerations") or []}
    ops = [{"op": "add", "path": "/spec/tolerations/-", "value": toleration(c)}
           for c in sorted(set(knows)) if PREREQ + c not in have]
    if ops:
        kube.run("patch", "pod", pod["metadata"]["name"], "-n", pod["metadata"]["namespace"],
                 "--type=json", "-p", json.dumps(ops))
    return len(ops)


def place(kube: Kubectl, es: ES, class_name: str, pseudonym: str, slot: str, knows: list[str]) -> str:
    check_pseudonym(pseudonym)
    ensure_index(es)
    ensure_namespace(kube, class_name)
    es.request("PUT", f"/{INDEX}/_doc/{doc_id(class_name, pseudonym, slot)}?refresh=true",
               {"class": class_name, "student": pseudonym, "slot": slot, "knows": sorted(set(knows))})
    pod = get_pod(kube, class_name, pod_name(pseudonym, slot))
    if pod is None:
        kube.run("create", "-f", "-", stdin=json.dumps(student_pod(pseudonym, slot, knows, class_name)))
        return "created"
    return "updated" if add_tolerations(kube, pod, knows) else "unchanged"


def learn(kube: Kubectl, es: ES, class_name: str, pseudonym: str, concept: str) -> int:
    """The student learned a concept: add it everywhere, and let the scheduler retry their Pending hours."""
    n = 0
    for d in desired(es, class_name):
        if d["student"] != pseudonym:
            continue
        knows = sorted(set(d["knows"]) | {concept})
        es.request("POST", f"/{INDEX}/_update/{doc_id(class_name, pseudonym, d['slot'])}?refresh=true",
                   {"doc": {"knows": knows}})
        pod = get_pod(kube, class_name, pod_name(pseudonym, d["slot"]))
        if pod:
            n += add_tolerations(kube, pod, knows)
    return n


def latest_failed_scheduling(events: list[dict]) -> dict[str, str]:
    """Pod name -> the scheduler's most recent FailedScheduling message."""
    latest: dict[str, tuple[str, str]] = {}
    for e in events:
        if e.get("reason") != "FailedScheduling":
            continue
        name = e["involvedObject"]["name"]
        stamp = e.get("lastTimestamp") or e.get("eventTime") or ""
        if name not in latest or stamp >= latest[name][0]:
            latest[name] = (stamp, e.get("message", ""))
    return {k: v[1] for k, v in latest.items()}


def reconcile(kube: Kubectl, es: ES, class_name: str, names: dict[str, str] | None = None) -> dict:
    """One pass of the controller loop. Returns counts."""
    ns = namespace(class_name)
    want = desired(es, class_name)
    pods = {p["metadata"]["name"]: p for p in kube.json("get", "pods", "-n", ns, "-l", f"{MANAGED_BY}={MANAGER}")["items"]}
    counts = {"placements": len(want), "recreated": 0, "running": 0, "pending": 0}
    for d in want:
        name = pod_name(d["student"], d["slot"])
        pod = pods.get(name)
        if pod is None or pod.get("status", {}).get("phase") == "Failed":
            if pod is not None:
                kube.run("delete", "pod", name, "-n", ns, "--wait=false")
            kube.run("create", "-f", "-", stdin=json.dumps(student_pod(d["student"], d["slot"], d["knows"], class_name)))
            counts["recreated"] += 1
    if counts["recreated"]:
        time.sleep(2)  # give the scheduler a moment before reading status
        pods = {p["metadata"]["name"]: p for p in kube.json("get", "pods", "-n", ns, "-l", f"{MANAGED_BY}={MANAGER}")["items"]}
    messages = latest_failed_scheduling(kube.json("get", "events", "-n", ns, "--field-selector",
                                                  "reason=FailedScheduling")["items"])
    slots = {d["slot"] for d in want}
    nodes = kube.json("get", "nodes", "-l", "type=kwok")["items"] if slots else []
    by_slot: dict[str, list[dict]] = {}
    for n in nodes:
        by_slot.setdefault((n["metadata"].get("labels") or {}).get(SLOT_LABEL, ""), []).append(n)
    now = datetime.now(timezone.utc).isoformat()
    lines = []
    for d in want:
        name = pod_name(d["student"], d["slot"])
        pod = pods.get(name)
        status = summarize(pod, messages.get(name) if pod and not pod["spec"].get("nodeName") else None,
                           by_slot.get(d["slot"], []), names)
        counts["running" if status["talk"] else "pending"] += 1
        lines.append(json.dumps({"update": {"_index": INDEX, "_id": doc_id(class_name, d["student"], d["slot"])}}))
        lines.append(json.dumps({"doc": {"status": status, "updated_at": now}}))
    if lines:
        es.request("POST", "/_bulk?refresh=true", "\n".join(lines) + "\n", ndjson=True)
    return counts


def cancel(kube: Kubectl, node: str, undo: bool = False) -> None:
    """The NoExecute path from ~/cnh-infra/kwok/examples/test.sh: evicts every student from the talk."""
    taint = f"{CANCEL_TAINT}=true:NoExecute" + ("-" if undo else "")
    kube.run("taint", "node", node, taint, *(["--overwrite"] if not undo else []))


def test_node(kube: Kubectl, action: str) -> None:
    if action == "delete":
        kube.run("delete", "node", TEST_NODE, "--ignore-not-found")
        return
    node = {"apiVersion": "v1", "kind": "Node",
            "metadata": {"name": TEST_NODE,
                         "annotations": {"node.alpha.kubernetes.io/ttl": "0", "kwok.x-k8s.io/node": "fake",
                                         "homeroom.dev/title": "Simulated test talk that assumes containers"},
                         "labels": {"type": "kwok", SLOT_LABEL: TEST_SLOT, "homeroom.dev/simulated": "true"}},
            "spec": {"taints": [{"key": "kwok.x-k8s.io/node", "value": "fake", "effect": "NoSchedule"},
                                {"key": PREREQ + "containers", "effect": "NoSchedule"}]},
            "status": {"allocatable": {"cpu": "32", "memory": "256Gi", "pods": "5"},
                       "capacity": {"cpu": "32", "memory": "256Gi", "pods": "5"}}}
    kube.run("apply", "-f", "-", stdin=json.dumps(node))


def concept_names(es: ES) -> dict[str, str]:
    try:
        res = es.request("POST", "/concepts/_search", {"size": 1000, "_source": ["slug", "concept"]})
        return {h["_source"]["slug"]: h["_source"]["concept"] for h in res["hits"]["hits"]}
    except ESError:
        return {}  # no concepts index yet: fall back to slugs


def main(argv: list[str] | None = None, kube: Kubectl | None = None, es: ES | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("place")
    p.add_argument("--class", dest="class_name", required=True)
    p.add_argument("--student", required=True, help="a pseudonym, never a real name")
    p.add_argument("--slot", required=True)
    p.add_argument("--knows", default="", help="comma-separated concept slugs")
    lp = sub.add_parser("learn")
    lp.add_argument("--class", dest="class_name", required=True)
    lp.add_argument("--student", required=True)
    lp.add_argument("--concept", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--class", dest="class_name", required=True)
    w.add_argument("--once", action="store_true")
    w.add_argument("--interval", type=float, default=5)
    for name in ("cancel", "uncancel"):
        sub.add_parser(name).add_argument("node")
    sub.add_parser("test-node").add_argument("action", choices=["create", "delete"])
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    kube = kube or Kubectl()
    try:
        kube.check_context()
        if args.cmd in ("cancel", "uncancel"):
            cancel(kube, args.node, undo=args.cmd == "uncancel")
            print(f"{args.cmd}led {args.node}")
            return 0
        if args.cmd == "test-node":
            test_node(kube, args.action)
            print(f"test node {TEST_NODE} {args.action}d (slot {TEST_SLOT})")
            return 0
        es = es or ES.from_env()
        if args.cmd == "place":
            result = place(kube, es, args.class_name, args.student, args.slot,
                           [c.strip() for c in args.knows.split(",") if c.strip()])
            print(f"placement {result}")
        elif args.cmd == "learn":
            print(f"added {learn(kube, es, args.class_name, args.student, args.concept)} toleration(s)")
        else:
            ensure_index(es)
            names = concept_names(es)
            while True:
                counts = reconcile(kube, es, args.class_name, names)
                print(" ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
                if args.once:
                    break
                time.sleep(args.interval)
    except (KubeError, ESError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
