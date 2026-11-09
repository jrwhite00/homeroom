"""Keep one KWOK simulated node per session in sync with the sessions index.

    cd backend && python -m app.node_sync            # create, update and delete nodes to match
    cd backend && python -m app.node_sync --dry-run  # only report what would change

Each session becomes a node named talk-<session id>, following ~/cnh-infra/kwok/examples/nodes.yaml:
  annotation kwok.x-k8s.io/node=fake, label type=kwok, taint kwok.x-k8s.io/node=fake:NoSchedule
  so real workloads stay off. Real DaemonSets also stay off because the nodes have no
  kubernetes.io/os label.
  label homeroom.dev/slot=<day>-<HHMM>   the start time in America/Denver, for example tue-1430
  taint prereq.homeroom.dev/<slug>:NoSchedule for each concept the talk assumes
  label homeroom.dev/tags=pending        until the talk is tagged (no assumes_status, or unknown);
                                         tagged talks get homeroom.dev/tags=tagged
  pod capacity = the room's seats        made up from the room's size (the schedule has none),
                                         so every node carries homeroom.dev/seats=simulated

Only nodes labeled app.kubernetes.io/managed-by=homeroom-node-sync are ever changed or deleted.
Taints and labels this sync does not own (for example node.kubernetes.io/not-ready) are left alone.
A second run with nothing changed reports 0 created, 0 updated, 0 deleted.

Every kubectl call names --context kind-cnh, and the sync refuses to run if the current context is
anything else.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime

from app.es import ES, ESError

CONTEXT = "kind-cnh"
MANAGER = "homeroom-node-sync"
MANAGED_BY = "app.kubernetes.io/managed-by"
INDEX = "sessions"
KWOK_TAINT = {"key": "kwok.x-k8s.io/node", "value": "fake", "effect": "NoSchedule"}
PREREQ = "prereq.homeroom.dev/"
ANNOTATIONS = {"kwok.x-k8s.io/node": "fake"}
# Set on create as in the example, then owned by kube-controller-manager's TTL controller, which
# changes it as the node count grows. Never compared, or every run would fight the controller.
CREATE_ONLY_ANNOTATIONS = {"node.alpha.kubernetes.io/ttl": "0"}
SI = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30}


def quantity(value: str | None) -> int | None:
    """Kubernetes stores pods 1000 as '1k'; compare quantities as numbers."""
    m = re.fullmatch(r"(\d+)([kMG]i?|Ki|Mi|Gi)?", value or "")
    return int(m.group(1)) * SI[m.group(2) or ""] if m else None

# Made-up seat counts by room size (the schedule has no capacities). First match wins.
SEAT_RULES = [
    (r"Foyer|Lobby|Registration|Entrance|Mezzanine|Off-Site|Network Nook", 100),  # not a seated room
    (r"Halls A-C, Halls 1-5", 5000),        # keynote hall
    (r"Grand Ballroom [A-J]{2,}", 1000),    # combined ballrooms (ACE, BDF, GI)
    (r"Hall DE", 1000),
    (r"Grand Ballroom", 500),               # single ballroom (H, J)
    (r"Regency Ballroom B-D", 600),
    (r"Ballroom [A-Z]{2,}|Ballroom [A-Z]-[A-Z]", 400),  # Regency CD, Salt Lake DE
    (r"Ballroom", 250),                     # Regency A, B, Salt Lake C
    (r"Solutions Showcase|Project Pavilion|Demo Theater", 80),
    (r"\b\d{3} [A-F]-[A-F]\b|\b\d{3} [A-F]{2}\b", 300),  # combined meeting rooms (250 A-C, 255 BC)
    (r"\b\d{3}\b", 150),                    # single meeting rooms (151, 254, 255 A)
]
DEFAULT_SEATS = 100


def seats(room: str | None) -> int:
    for pattern, n in SEAT_RULES:
        if re.search(pattern, room or ""):
            return n
    return DEFAULT_SEATS


def node_name(session_id: str) -> str:
    return "talk-" + re.sub(r"[^a-z0-9-]", "-", str(session_id).lower()).strip("-")


def slot(starts_at: str) -> str:
    """'2026-11-10T14:30:00-07:00' -> 'tue-1430'. starts_at is stored in America/Denver time."""
    t = datetime.fromisoformat(starts_at)
    return f"{t.strftime('%a').lower()}-{t:%H%M}"


def desired(session: dict) -> dict:
    """The state this sync owns on the node for one session."""
    tagged = session.get("assumes_status") == "tagged"
    concepts = sorted(set(session.get("assumes") or [])) if tagged else []
    return {
        "name": node_name(session["id"]),
        "labels": {
            "type": "kwok",
            MANAGED_BY: MANAGER,
            "homeroom.dev/session-id": str(session["id"]),
            "homeroom.dev/slot": slot(session["starts_at"]),
            "homeroom.dev/talk": "true" if session.get("is_talk") else "false",
            "homeroom.dev/tags": "tagged" if tagged else "pending",
            "homeroom.dev/seats": "simulated",
        },
        "annotations": {**ANNOTATIONS, "homeroom.dev/title": (session.get("title") or "")[:500]},
        "taints": [KWOK_TAINT] + [{"key": PREREQ + c, "effect": "NoSchedule"} for c in concepts],
        "pods": str(seats(session.get("room"))),
    }


def owned_label(key: str) -> bool:
    return key in ("type", MANAGED_BY) or key.startswith("homeroom.dev/")


def owned_taint(taint: dict) -> bool:
    return taint["key"] == KWOK_TAINT["key"] or taint["key"].startswith(PREREQ)


def taint_set(taints: list[dict]) -> set[tuple]:
    return {(t["key"], t.get("value", ""), t["effect"]) for t in taints}


def observed(node: dict) -> dict:
    """The part of a live node that this sync owns, in the same shape as desired()."""
    meta, spec, status = node["metadata"], node.get("spec", {}), node.get("status", {})
    return {
        "name": meta["name"],
        "labels": {k: v for k, v in (meta.get("labels") or {}).items() if owned_label(k)},
        "annotations": {k: v for k, v in (meta.get("annotations") or {}).items()
                        if k in ANNOTATIONS or k.startswith("homeroom.dev/")},
        "taints": [t for t in spec.get("taints") or [] if owned_taint(t)],
        "foreign_taints": [t for t in spec.get("taints") or [] if not owned_taint(t)],
        "pods": (status.get("capacity") or {}).get("pods"),
    }


def manifest(d: dict) -> dict:
    return {
        "apiVersion": "v1", "kind": "Node",
        "metadata": {"name": d["name"], "labels": d["labels"],
                     "annotations": {**CREATE_ONLY_ANNOTATIONS, **d["annotations"]}},
        "spec": {"taints": d["taints"]},
        "status": {"allocatable": {"cpu": "32", "memory": "256Gi", "pods": d["pods"]},
                   "capacity": {"cpu": "32", "memory": "256Gi", "pods": d["pods"]}},
    }


def diff(want: dict, have: dict) -> tuple[dict | None, dict | None]:
    """Return (metadata/spec merge patch, status merge patch), each None if nothing differs."""
    patch: dict = {}
    labels = {k: v for k, v in want["labels"].items() if have["labels"].get(k) != v}
    labels |= {k: None for k in have["labels"] if k not in want["labels"]}
    annotations = {k: v for k, v in want["annotations"].items() if have["annotations"].get(k) != v}
    annotations |= {k: None for k in have["annotations"] if k not in want["annotations"]}
    if labels or annotations:
        patch["metadata"] = {k: v for k, v in (("labels", labels), ("annotations", annotations)) if v}
    if taint_set(want["taints"]) != taint_set(have["taints"]):
        patch["spec"] = {"taints": have["foreign_taints"] + want["taints"]}  # keep taints we don't own
    status = None
    if quantity(want["pods"]) != quantity(have["pods"]):
        status = {"status": {"capacity": {"pods": want["pods"]}, "allocatable": {"pods": want["pods"]}}}
    return (patch or None), status


def plan(sessions: list[dict], nodes: list[dict]) -> dict:
    want = {d["name"]: d for d in map(desired, sessions)}
    have = {n["metadata"]["name"]: observed(n) for n in nodes
            if (n["metadata"].get("labels") or {}).get(MANAGED_BY) == MANAGER}
    creates = [want[n] for n in sorted(want) if n not in have]
    deletes = sorted(n for n in have if n not in want)
    updates = []
    for name in sorted(set(want) & set(have)):
        patch, status = diff(want[name], have[name])
        if patch or status:
            updates.append({"name": name, "patch": patch, "status": status})
    return {"creates": creates, "updates": updates, "deletes": deletes, "unchanged": len(want) - len(creates) - len(updates)}


class Kubectl:
    def __init__(self, context: str = CONTEXT):
        self.context = context

    def run(self, *args: str, stdin: str | None = None) -> str:
        result = subprocess.run(["kubectl", "--context", self.context, *args], input=stdin,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args[:3])} failed: {result.stderr.strip()[:500]}")
        return result.stdout

    def current_context(self) -> str:
        out = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True)
        return out.stdout.strip()


def apply(kube: Kubectl, p: dict) -> None:
    if p["creates"]:
        kube.run("create", "-f", "-", stdin=json.dumps({"apiVersion": "v1", "kind": "List",
                                                           "items": [manifest(d) for d in p["creates"]]}))
    for u in p["updates"]:
        if u["patch"]:
            kube.run("patch", "node", u["name"], "--type=merge", "-p", json.dumps(u["patch"]))
        if u["status"]:
            kube.run("patch", "node", u["name"], "--subresource=status", "--type=merge", "-p", json.dumps(u["status"]))
    if p["deletes"]:
        kube.run("delete", "node", *p["deletes"])


def fetch_sessions(es: ES) -> list[dict]:
    res = es.search(INDEX, {"size": 2000, "query": {"match_all": {}},
                            "_source": ["id", "title", "room", "starts_at", "is_talk", "assumes", "assumes_status"]})
    sessions = [h["_source"] for h in res["hits"]["hits"]]
    if len(sessions) != res["hits"]["total"]["value"]:
        raise SystemExit("did not read every session; raise the search size")
    return sessions


def main(argv: list[str] | None = None, kube: Kubectl | None = None, es: ES | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    kube = kube or Kubectl()
    if (ctx := kube.current_context()) != CONTEXT:
        print(f"Current kubectl context is '{ctx}', not {CONTEXT}. Refusing to touch it.", file=sys.stderr)
        return 1
    try:
        sessions = fetch_sessions(es or ES.from_env())
    except ESError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    nodes = json.loads(kube.run("get", "nodes", "-l", f"{MANAGED_BY}={MANAGER}", "-o", "json"))["items"]
    p = plan(sessions, nodes)
    pending = sum(1 for s in sessions if s.get("assumes_status") != "tagged")
    print(f"{len(sessions)} sessions ({pending} not tagged yet), {len(nodes)} managed nodes")
    print(f"{'would create' if args.dry_run else 'created'} {len(p['creates'])}, "
          f"{'update' if args.dry_run else 'updated'} {len(p['updates'])}, "
          f"{'delete' if args.dry_run else 'deleted'} {len(p['deletes'])}, unchanged {p['unchanged']}")
    if not args.dry_run:
        apply(kube, p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
