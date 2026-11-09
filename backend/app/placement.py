"""Student pods for the real kube-scheduler. Pure logic: no cluster or network calls.

Each hour of a student's week is a pod, following ~/cnh-infra/kwok/examples/student.sh:
  nodeSelector   type=kwok and homeroom.dev/slot=<slot>, so only talks in that time slot qualify
  tolerations    the kwok fake taint, plus prereq.homeroom.dev/<slug> for every concept the student knows
  namespace      class-<class>
kube-scheduler then picks a talk whose prerequisites the student meets, or leaves the pod Pending.

Pod names are <pseudonym>-<slot>. Only pseudonyms, never real names. The pseudonym is not put in
labels or annotations (the Dash0 operator copies those into telemetry), and the class namespace is
labeled dash0.com/enable=false.

Kubernetes' Pending message only says "untolerated taint(s)". missing_concepts() works out which
concept by comparing the slot's talk taints with the pod's tolerations.
"""
from __future__ import annotations

import re

PREREQ = "prereq.homeroom.dev/"
SLOT_LABEL = "homeroom.dev/slot"
MANAGED_BY = "app.kubernetes.io/managed-by"
MANAGER = "homeroom-placer"
CANCEL_TAINT = "homeroom.dev/cancelled"
PSEUDONYM = re.compile(r"^[a-z][a-z0-9]{1,20}$")
NAME_PART = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


def check_pseudonym(pseudonym: str) -> str:
    if not PSEUDONYM.match(pseudonym):
        raise ValueError("a pseudonym is 2 to 21 lowercase letters or digits, starting with a letter")
    return pseudonym


def namespace(class_name: str) -> str:
    if not NAME_PART.match(class_name):
        raise ValueError("a class name is lowercase letters, digits and dashes")
    return f"class-{class_name}"


def pod_name(pseudonym: str, slot: str) -> str:
    return f"{check_pseudonym(pseudonym)}-{slot}"


def toleration(concept: str) -> dict:
    return {"key": PREREQ + concept, "operator": "Exists", "effect": "NoSchedule"}


def namespace_manifest(class_name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": namespace(class_name),
                         "labels": {MANAGED_BY: MANAGER, "homeroom.dev/class": class_name,
                                    "dash0.com/enable": "false"}}}


def student_pod(pseudonym: str, slot: str, knows: list[str], class_name: str) -> dict:
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": pod_name(pseudonym, slot), "namespace": namespace(class_name),
                     "labels": {MANAGED_BY: MANAGER, SLOT_LABEL: slot},
                     "annotations": {"homeroom.dev/knows": ",".join(sorted(set(knows)))}},
        "spec": {
            "nodeSelector": {"type": "kwok", SLOT_LABEL: slot},
            "tolerations": [{"key": "kwok.x-k8s.io/node", "operator": "Exists", "effect": "NoSchedule"}]
                           + [toleration(c) for c in sorted(set(knows))],
            "containers": [{"name": "student", "image": "fake-image"}],
        },
    }


def known(pod: dict) -> set[str]:
    return {t["key"][len(PREREQ):] for t in pod["spec"].get("tolerations") or []
            if t.get("key", "").startswith(PREREQ)}


def missing_concepts(pod: dict, node: dict) -> list[str]:
    """Prereq concepts on the node that the pod does not tolerate, sorted."""
    have = known(pod)
    return sorted({t["key"][len(PREREQ):] for t in node.get("spec", {}).get("taints") or []
                   if t["key"].startswith(PREREQ) and t.get("effect") == "NoSchedule"} - have)


def cancelled(node: dict) -> bool:
    return any(t["key"] == CANCEL_TAINT for t in node.get("spec", {}).get("taints") or [])


def plain(missing: list[str], names: dict[str, str]) -> str:
    """Student-facing text for a pending hour."""
    if not missing:
        return "No talk in this hour has a free seat right now."
    concepts = [names.get(m, m.replace("-", " ")) for m in missing]
    joined = concepts[0] if len(concepts) == 1 else ", ".join(concepts[:-1]) + " and " + concepts[-1]
    return f"Learn {joined} first, and a talk in this hour opens up."


def summarize(pod: dict | None, event_message: str | None, slot_nodes: list[dict],
              names: dict[str, str] | None = None) -> dict:
    """Status of one placement: where the pod is, or why it is Pending and which concept is missing."""
    names = names or {}
    if pod is None:
        return {"phase": "Missing", "talk": None, "title": None, "message": None, "missing": [], "plain": None}
    node_name = pod["spec"].get("nodeName")
    if node_name:
        node = next((n for n in slot_nodes if n["metadata"]["name"] == node_name), {"metadata": {}})
        return {"phase": pod.get("status", {}).get("phase", "Pending"), "talk": node_name,
                "title": (node["metadata"].get("annotations") or {}).get("homeroom.dev/title"),
                "message": None, "missing": [], "plain": None}
    # Pending: find the open talk in this slot that is closest (fewest missing concepts).
    candidates = [n for n in slot_nodes if not cancelled(n)]
    closest = min((missing_concepts(pod, n) for n in candidates), key=len, default=[])
    return {"phase": "Pending", "talk": None, "title": None, "message": event_message,
            "missing": [{"slug": m, "name": names.get(m, m)} for m in closest],
            "plain": plain(closest, names) if candidates else "There are no talks left in this hour."}
