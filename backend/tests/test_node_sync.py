import copy
import json

from app import node_sync
from app.node_sync import desired, plan, seats, slot

SESSIONS = [
    {"id": "101", "title": "Autoscaling at Scale", "room": "Salt Palace | Level 2 | 254",
     "starts_at": "2026-11-12T11:00:00-07:00", "is_talk": True},
    {"id": "102", "title": "GPU Dashboards", "room": "Hyatt Regency | Level 4 | Regency Ballroom B-D",
     "starts_at": "2026-11-11T13:20:00-07:00", "is_talk": True,
     "assumes": ["containers", "networking-basics"], "assumes_status": "tagged"},
    {"id": "103", "title": "Unknown one", "room": "Salt Palace | Level 1 | 151",
     "starts_at": "2026-11-10T09:00:00-07:00", "is_talk": True, "assumes": None, "assumes_status": "unknown"},
]


class FakeKube:
    """Holds nodes in memory and applies create, merge-patch and delete like the API server."""

    def __init__(self, context="kind-cnh", nodes=None):
        self.context = context
        self.nodes = {n["metadata"]["name"]: n for n in (nodes or [])}
        self.calls = []

    def current_context(self):
        return self.context

    def run(self, *args, stdin=None):
        self.calls.append(args[0])
        if args[0] == "get":
            selector = args[3].split("=")
            items = [n for n in self.nodes.values() if (n["metadata"].get("labels") or {}).get(selector[0]) == selector[1]]
            return json.dumps({"items": copy.deepcopy(items)})
        if args[0] == "create":
            for item in json.loads(stdin)["items"]:
                self.nodes[item["metadata"]["name"]] = item
        elif args[0] == "patch":
            node, patch = self.nodes[args[2]], json.loads(args[-1])
            merge(node, patch)
        elif args[0] == "delete":
            for name in args[2:]:
                del self.nodes[name]
        return ""


def merge(target, patch):
    for k, v in patch.items():
        if v is None:
            target.pop(k, None)
        elif isinstance(v, dict) and isinstance(target.get(k), dict):
            merge(target[k], v)
        else:
            target[k] = copy.deepcopy(v)


class FakeES:
    def __init__(self, sessions):
        self.sessions = sessions

    def search(self, index, body):
        return {"hits": {"total": {"value": len(self.sessions)}, "hits": [{"_source": s} for s in self.sessions]}}


def taints(node):
    return {(t["key"], t["effect"]) for t in node["spec"]["taints"]}


def test_node_follows_the_kwok_example_and_labels_the_slot():
    d = desired(SESSIONS[0])
    m = node_sync.manifest(d)
    assert m["metadata"]["name"] == "talk-101"
    assert m["metadata"]["annotations"]["kwok.x-k8s.io/node"] == "fake"
    assert m["metadata"]["labels"]["type"] == "kwok"
    assert m["metadata"]["labels"]["homeroom.dev/slot"] == "thu-1100"
    assert {"key": "kwok.x-k8s.io/node", "value": "fake", "effect": "NoSchedule"} in m["spec"]["taints"]
    assert "kubernetes.io/os" not in m["metadata"]["labels"]  # keeps real DaemonSets off


def test_untagged_and_unknown_are_pending_with_no_prereq_taints():
    for s in (SESSIONS[0], SESSIONS[2]):
        d = desired(s)
        assert d["labels"]["homeroom.dev/tags"] == "pending"
        assert [t["key"] for t in d["taints"]] == ["kwok.x-k8s.io/node"]


def test_tagged_talk_gets_one_prereq_taint_per_concept():
    d = desired(SESSIONS[1])
    assert d["labels"]["homeroom.dev/tags"] == "tagged"
    assert {t["key"] for t in d["taints"]} == {"kwok.x-k8s.io/node", "prereq.homeroom.dev/containers",
                                               "prereq.homeroom.dev/networking-basics"}
    assert all(t["effect"] == "NoSchedule" for t in d["taints"])


def test_capacity_is_simulated_seats_by_room_size():
    assert desired(SESSIONS[1])["pods"] == "600"
    assert desired(SESSIONS[1])["labels"]["homeroom.dev/seats"] == "simulated"
    assert seats("Salt Palace | Level 2 | 254") == 150
    assert seats("Salt Palace | Level 1 | North Grand Ballroom Foyer") == 100
    assert seats(None) == node_sync.DEFAULT_SEATS


def test_slot_uses_denver_local_time():
    assert slot("2026-11-10T14:30:00-07:00") == "tue-1430"


def run(kube, sessions, *args):
    return node_sync.main(list(args), kube=kube, es=FakeES(sessions))


def test_first_sync_creates_second_changes_nothing(capsys):
    kube = FakeKube()
    assert run(kube, SESSIONS) == 0
    assert set(kube.nodes) == {"talk-101", "talk-102", "talk-103"}
    capsys.readouterr()
    kube.calls.clear()
    assert run(kube, SESSIONS) == 0
    assert "created 0, updated 0, deleted 0, unchanged 3" in capsys.readouterr().out
    assert kube.calls == ["get"]  # nothing but the read


def test_tagging_later_adds_taints_and_drops_pending():
    kube = FakeKube()
    run(kube, SESSIONS)
    tagged = [dict(SESSIONS[0], assumes=["programming-basics"], assumes_status="tagged")] + SESSIONS[1:]
    run(kube, tagged)
    node = kube.nodes["talk-101"]
    assert ("prereq.homeroom.dev/programming-basics", "NoSchedule") in taints(node)
    assert node["metadata"]["labels"]["homeroom.dev/tags"] == "tagged"


def test_foreign_taints_and_unmanaged_nodes_are_left_alone():
    kube = FakeKube()
    run(kube, SESSIONS)
    kube.nodes["talk-102"]["spec"]["taints"].append({"key": "node.kubernetes.io/not-ready", "effect": "NoSchedule"})
    kube.nodes["cnh-worker"] = {"metadata": {"name": "cnh-worker", "labels": {}}, "spec": {}}
    p = plan(SESSIONS, json.loads(kube.run("get", "nodes", "-l", "app.kubernetes.io/managed-by=homeroom-node-sync",
                                           "-o", "json"))["items"])
    assert p["updates"] == [] and p["deletes"] == []
    run(kube, [SESSIONS[0], SESSIONS[2], dict(SESSIONS[1], assumes=["containers"])])
    assert ("node.kubernetes.io/not-ready", "NoSchedule") in taints(kube.nodes["talk-102"])
    assert ("prereq.homeroom.dev/networking-basics", "NoSchedule") not in taints(kube.nodes["talk-102"])
    assert "cnh-worker" in kube.nodes


def test_apiserver_rewrites_and_ttl_controller_do_not_cause_updates():
    """Kubernetes stores pods 1000 as '1k', and the TTL controller changes the ttl annotation."""
    big = dict(SESSIONS[0], room="Salt Palace | Level 1 | Grand Ballroom ACE")
    kube = FakeKube()
    run(kube, [big])
    node = kube.nodes["talk-101"]
    assert node["metadata"]["annotations"]["node.alpha.kubernetes.io/ttl"] == "0"  # set on create
    node["status"]["capacity"]["pods"] = node["status"]["allocatable"]["pods"] = "1k"
    node["metadata"]["annotations"]["node.alpha.kubernetes.io/ttl"] = "15"
    assert plan([big], [node])["updates"] == []


def test_removed_session_deletes_its_node_and_room_change_updates_capacity():
    kube = FakeKube()
    run(kube, SESSIONS)
    run(kube, [dict(SESSIONS[0], room="Salt Palace | Level 1 | Grand Ballroom H"), SESSIONS[1]])
    assert "talk-103" not in kube.nodes
    assert kube.nodes["talk-101"]["status"]["capacity"]["pods"] == "500"


def test_dry_run_changes_nothing(capsys):
    kube = FakeKube()
    assert run(kube, SESSIONS, "--dry-run") == 0
    assert kube.nodes == {} and "would create 3" in capsys.readouterr().out


def test_refuses_any_context_but_kind_cnh(capsys):
    kube = FakeKube(context="prod-cluster")
    assert run(kube, SESSIONS) == 1
    assert kube.calls == [] and "Refusing" in capsys.readouterr().err
