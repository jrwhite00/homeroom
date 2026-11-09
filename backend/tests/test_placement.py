import json

import pytest
from fastapi.testclient import TestClient

from app import placer
from app.kube import KubeError
from app.main import app
from app.placement import (check_pseudonym, missing_concepts, namespace_manifest, plain, student_pod, summarize)
from app.search import get_es


def node(name, slot="thu-1430", prereqs=(), cancelled=False, title="A talk"):
    taints = [{"key": "kwok.x-k8s.io/node", "value": "fake", "effect": "NoSchedule"}]
    taints += [{"key": f"prereq.homeroom.dev/{p}", "effect": "NoSchedule"} for p in prereqs]
    if cancelled:
        taints.append({"key": "homeroom.dev/cancelled", "value": "true", "effect": "NoExecute"})
    return {"metadata": {"name": name, "labels": {"type": "kwok", "homeroom.dev/slot": slot},
                         "annotations": {"homeroom.dev/title": title}}, "spec": {"taints": taints}}


# --- the pod ---------------------------------------------------------------------------------------

def test_pod_follows_student_sh():
    pod = student_pod("maya", "thu-1430", ["python", "containers"], "demo")
    assert pod["metadata"]["name"] == "maya-thu-1430" and pod["metadata"]["namespace"] == "class-demo"
    assert pod["spec"]["nodeSelector"] == {"type": "kwok", "homeroom.dev/slot": "thu-1430"}
    keys = [t["key"] for t in pod["spec"]["tolerations"]]
    assert keys == ["kwok.x-k8s.io/node", "prereq.homeroom.dev/containers", "prereq.homeroom.dev/python"]
    assert all(t["operator"] == "Exists" and t["effect"] == "NoSchedule" for t in pod["spec"]["tolerations"])


def test_pseudonym_only_in_the_name_and_namespace_opts_out_of_dash0():
    pod = student_pod("maya", "thu-1430", [], "demo")
    assert "maya" not in json.dumps({k: v for k, v in pod["metadata"].items() if k != "name"})
    assert namespace_manifest("demo")["metadata"]["labels"]["dash0.com/enable"] == "false"


@pytest.mark.parametrize("bad", ["Maya", "maya smith", "m", "maya.s", "", "1maya"])
def test_pseudonym_must_be_a_short_lowercase_handle(bad):
    with pytest.raises(ValueError):
        check_pseudonym(bad)


# --- reading where students are ----------------------------------------------------------------------

def test_missing_concepts_compares_taints_with_tolerations():
    pod = student_pod("maya", "thu-1430", ["python"], "demo")
    assert missing_concepts(pod, node("t1", prereqs=["python", "containers", "networking"])) == ["containers", "networking"]


def test_pending_summary_names_the_closest_missing_concept_in_plain_language():
    pod = student_pod("maya", "thu-1430", ["python"], "demo")
    nodes = [node("t1", prereqs=["containers", "networking"]), node("t2", prereqs=["containers"])]
    s = summarize(pod, "0/3 nodes are available: 2 node(s) had untolerated taint(s).", nodes, {"containers": "Containers"})
    assert s["phase"] == "Pending" and s["message"].startswith("0/3 nodes are available")
    assert s["missing"] == [{"slug": "containers", "name": "Containers"}]
    assert s["plain"] == "Learn Containers first, and a talk in this hour opens up."


def test_running_summary_names_the_talk():
    pod = student_pod("maya", "thu-1430", ["containers"], "demo")
    pod["spec"]["nodeName"], pod["status"] = "t2", {"phase": "Running"}
    s = summarize(pod, None, [node("t2", title="Kubernetes for Beginners")])
    assert (s["phase"], s["talk"], s["title"]) == ("Running", "t2", "Kubernetes for Beginners")


def test_cancelled_talks_are_not_offered():
    pod = student_pod("maya", "thu-1430", [], "demo")
    assert summarize(pod, "msg", [node("t1", cancelled=True)])["plain"] == "There are no talks left in this hour."


def test_plain_joins_several_concepts():
    assert plain(["a", "b", "c"], {"a": "Alpha"}) == "Learn Alpha, b and c first, and a talk in this hour opens up."


def test_latest_failed_scheduling_message_per_pod():
    events = [
        {"reason": "FailedScheduling", "involvedObject": {"name": "p"}, "lastTimestamp": "2026-11-09T19:00:00Z", "message": "old"},
        {"reason": "FailedScheduling", "involvedObject": {"name": "p"}, "lastTimestamp": "2026-11-09T19:05:00Z", "message": "new"},
        {"reason": "Scheduled", "involvedObject": {"name": "p"}, "lastTimestamp": "2026-11-09T19:06:00Z", "message": "x"},
    ]
    assert placer.latest_failed_scheduling(events) == {"p": "new"}


# --- controller loop with fakes ------------------------------------------------------------------------

class FakeKube:
    def __init__(self, nodes=(), context="kind-cnh"):
        self.pods, self.nodes, self.context = {}, list(nodes), context
        self.events, self.calls, self.namespaces = [], [], set()

    def check_context(self):
        if self.context != "kind-cnh":
            raise KubeError("current kubectl context is not kind-cnh; refusing to touch it")

    def run(self, *args, stdin=None, ok_missing=False):
        self.calls.append(args)
        if args[:2] == ("get", "namespace"):
            return args[2] if args[2] in self.namespaces else ""
        if args[:2] == ("get", "pod"):
            p = self.pods.get(args[2])
            return json.dumps(p) if p else ""
        if args[0] == "create":
            obj = json.loads(stdin)
            if obj["kind"] == "Namespace":
                self.namespaces.add(obj["metadata"]["name"])
            else:
                self.pods[obj["metadata"]["name"]] = obj
        elif args[:2] == ("patch", "pod"):
            for op in json.loads(args[-1]):
                self.pods[args[2]]["spec"]["tolerations"].append(op["value"])
        elif args[:2] == ("delete", "pod"):
            self.pods.pop(args[2], None)
        return ""

    def json(self, *args):
        if args[1] == "pods":
            return {"items": list(self.pods.values())}
        if args[1] == "events":
            return {"items": self.events}
        return {"items": self.nodes}


class FakeES:
    def __init__(self):
        self.docs, self.index = {}, False

    def request(self, method, path, body=None, ndjson=False, ok404=False, timeout=None):
        if method == "HEAD":
            return {} if self.index else None
        if method == "PUT" and path == "/placements":
            self.index = True
        elif method == "PUT":
            self.docs[path.split("/_doc/")[1].split("?")[0]] = dict(body)
        elif "/_update/" in path:
            self.docs[path.split("/_update/")[1].split("?")[0]].update(body["doc"])
        elif path == "/concepts/_search":
            return {"hits": {"hits": []}}
        elif path.endswith("/_search"):
            cls = body["query"]["term"]["class"]
            return {"hits": {"hits": [{"_source": d} for d in self.docs.values() if d["class"] == cls]}}
        elif path.startswith("/_bulk"):
            lines = body.strip().splitlines()
            for action, doc in zip(lines[::2], lines[1::2]):
                self.docs[json.loads(action)["update"]["_id"]].update(json.loads(doc)["doc"])
        return {}

    def search(self, index, body):
        return {"hits": {"hits": [{"_source": d} for d in self.docs.values()]}}


def test_place_then_learn_adds_the_toleration_to_the_pending_pod():
    kube, es = FakeKube(), FakeES()
    assert placer.place(kube, es, "demo", "maya", "test-0000", ["python"]) == "created"
    assert "class-demo" in kube.namespaces
    assert placer.place(kube, es, "demo", "maya", "test-0000", ["python"]) == "unchanged"
    assert placer.learn(kube, es, "demo", "maya", "containers") == 1
    keys = [t["key"] for t in kube.pods["maya-test-0000"]["spec"]["tolerations"]]
    assert "prereq.homeroom.dev/containers" in keys
    assert es.docs["demo:maya:test-0000"]["knows"] == ["containers", "python"]


def test_controller_records_pending_message_and_recreates_evicted_pod(monkeypatch):
    monkeypatch.setattr(placer.time, "sleep", lambda s: None)
    kube, es = FakeKube(nodes=[node("simulated", slot="test-0000", prereqs=["containers"])]), FakeES()
    placer.place(kube, es, "demo", "maya", "test-0000", ["python"])
    msg = "0/603 nodes are available: 2 node(s) had untolerated taint(s)."
    kube.events = [{"reason": "FailedScheduling", "involvedObject": {"name": "maya-test-0000"},
                    "lastTimestamp": "2026-11-09T19:00:00Z", "message": msg}]
    counts = placer.reconcile(kube, es, "demo")
    status = es.docs["demo:maya:test-0000"]["status"]
    assert counts["pending"] == 1 and status["message"] == msg and status["missing"][0]["slug"] == "containers"

    del kube.pods["maya-test-0000"]  # a NoExecute taint evicted the bare pod; nothing brings it back
    assert placer.reconcile(kube, es, "demo")["recreated"] == 1
    assert "maya-test-0000" in kube.pods


def test_cancel_and_uncancel_use_the_noexecute_taint():
    kube = FakeKube()
    placer.cancel(kube, "talk-1")
    placer.cancel(kube, "talk-1", undo=True)
    assert kube.calls[0][:4] == ("taint", "node", "talk-1", "homeroom.dev/cancelled=true:NoExecute")
    assert kube.calls[1][:4] == ("taint", "node", "talk-1", "homeroom.dev/cancelled=true:NoExecute-")


def test_refuses_any_context_but_kind_cnh(capsys):
    assert placer.main(["cancel", "talk-1"], kube=FakeKube(context="other")) == 1
    assert "refusing" in capsys.readouterr().err


def test_logs_never_contain_the_pseudonym(monkeypatch, capsys):
    monkeypatch.setattr(placer.time, "sleep", lambda s: None)
    kube, es = FakeKube(), FakeES()
    placer.main(["place", "--class", "demo", "--student", "maya", "--slot", "thu-1430"], kube=kube, es=es)
    placer.main(["watch", "--class", "demo", "--once"], kube=kube, es=es)
    assert "maya" not in capsys.readouterr().out


def test_placements_api_serves_what_the_controller_saw():
    es = FakeES()
    es.docs["demo:maya:thu-1430"] = {"class": "demo", "student": "maya", "slot": "thu-1430", "knows": [],
                                     "status": {"phase": "Pending", "plain": "Learn Containers first."}}
    app.dependency_overrides[get_es] = lambda: es
    try:
        body = TestClient(app).get("/placements", params={"class": "demo"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["placements"][0]["phase"] == "Pending" and body["placements"][0]["plain"] == "Learn Containers first."
