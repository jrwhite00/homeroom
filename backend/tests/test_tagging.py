import json
import sys
import types
from types import SimpleNamespace as NS

import pytest

from app import tag_sessions
from app.tagging import (build_request, cache_key, custom_id, entry_from_result, parse_answer, session_update,
                         table_fingerprint)

CONCEPTS = [
    {"slug": "scaling", "concept": "Scaling", "definition": "Keeping a site up when many people visit.",
     "technical_words": ["autoscaling", "load balancing"]},
    {"slug": "containers", "concept": "Containers", "definition": "Packaging a program to run anywhere.",
     "technical_words": ["Docker"]},
]
SLUGS = {"scaling", "containers"}
SESSIONS = [
    {"id": "101", "title": "Autoscaling at Scale with KEDA", "abstract": "Scale pods on events.",
     "track": ["Operations + Performance"], "level": "Intermediate"},
    {"id": "102", "title": "Lunch", "abstract": "", "track": ["Breaks"]},
]


def answer(*pairs):
    return {"stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"assumes": [
                {"concept": c, "reason": r} for c, r in pairs]})}]}


# --- request and answer --------------------------------------------------------------------------

def test_request_constrains_concepts_to_real_slugs_and_caches_table():
    req = build_request(SESSIONS[0], CONCEPTS, "claude-haiku-5-5")
    assert req["custom_id"] == "101"
    params = req["params"]
    schema = params["output_config"]["format"]["schema"]
    assert schema["properties"]["assumes"]["items"]["properties"]["concept"]["enum"] == ["containers", "scaling"]
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "scaling: Scaling." in params["system"][-1]["text"]
    assert "Autoscaling at Scale with KEDA" in params["messages"][0]["content"]
    assert "meta" not in req  # nothing local leaks into the API request


def test_only_existing_slugs_kept_at_most_four_no_repeats():
    text = json.dumps({"assumes": [{"concept": c, "reason": "r"} for c in
                                   ["scaling", "kubernetes", "scaling", "containers", "a", "b"]]})
    accepted, notes = parse_answer(text, SLUGS | {"a", "b"})
    assert [t["concept"] for t in accepted] == ["scaling", "containers", "a", "b"]
    assert "dropped unknown concept 'kubernetes'" in notes and "dropped repeated concept 'scaling'" in notes


def test_succeeded_answer_is_tagged_and_empty_answer_means_assumes_nothing():
    e = entry_from_result("succeeded", answer(("scaling", "Uses autoscaling.")), None, "k", "m", SLUGS)
    assert e["status"] == "tagged" and e["assumes"] == [{"concept": "scaling", "reason": "Uses autoscaling."}]
    empty = entry_from_result("succeeded", answer(), None, "k", "m", SLUGS)
    assert empty["status"] == "tagged" and empty["assumes"] == []


@pytest.mark.parametrize("result_type,message", [
    ("errored", None), ("expired", None), ("canceled", None),
    ("succeeded", {"stop_reason": "refusal", "content": []}),
    ("succeeded", {"stop_reason": "max_tokens", "content": []}),
    ("succeeded", {"stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]}),
])
def test_failures_are_unknown(result_type, message):
    assert entry_from_result(result_type, message, "boom", "k", "m", SLUGS)["status"] == "unknown"


def test_session_update_fields():
    tagged = entry_from_result("succeeded", answer(("scaling", "R1."), ("containers", "R2.")), None, "k", "m", SLUGS)
    assert session_update(tagged) == {"assumes": ["scaling", "containers"], "assumes_reason": ["R1.", "R2."],
                                      "assumes_status": "tagged"}
    assert session_update(None) == {"assumes": None, "assumes_reason": None, "assumes_status": "unknown"}


def test_cache_key_changes_with_concepts_model_or_talk():
    fp = table_fingerprint(CONCEPTS)
    base = cache_key(SESSIONS[0], fp, "m")
    assert base == cache_key(dict(SESSIONS[0]), fp, "m")
    assert base != cache_key(SESSIONS[0], fp, "other-model")
    assert base != cache_key(SESSIONS[0], table_fingerprint(CONCEPTS[:1]), "m")
    assert base != cache_key({**SESSIONS[0], "abstract": "changed"}, fp, "m")


def test_custom_id_safe_for_odd_ids():
    assert custom_id("1250326") == "1250326"
    assert custom_id("weird id/with:chars").startswith("h") and len(custom_id("x" * 100)) <= 64


# --- end to end with fakes -------------------------------------------------------------------------

class FakeBatches:
    def __init__(self, answers):
        self.answers = answers  # session id -> message dict, or None for errored
        self.created = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return NS(id="batch_1")

    def retrieve(self, batch_id):
        self.polls += 1
        status = "ended" if self.polls > 1 else "in_progress"
        return NS(processing_status=status, request_counts=NS(processing=1, succeeded=0))

    def results(self, batch_id):
        for req in self.created[-1]:
            msg = self.answers.get(req["custom_id"])
            if msg is None:
                yield NS(custom_id=req["custom_id"], result=NS(type="errored", error={"type": "api_error"}))
            else:
                yield NS(custom_id=req["custom_id"], result=NS(type="succeeded", message=msg))


class FakeES:
    def __init__(self):
        self.updates = {}
        self.mapping = None

    def search(self, index, body):
        return {"hits": {"total": {"value": len(SESSIONS)}, "hits": [{"_source": s} for s in SESSIONS]}}

    def request(self, method, path, body=None, ndjson=False, ok404=False, timeout=None):
        if path.endswith("/_mapping"):
            self.mapping = body
        elif path == "/_bulk":
            lines = body.strip().splitlines()
            for action, doc in zip(lines[::2], lines[1::2]):
                self.updates[json.loads(action)["update"]["_id"]] = json.loads(doc)["doc"]
            return {"errors": False}
        elif path.endswith("/_count"):
            status = (body or {}).get("query", {}).get("term", {}).get("assumes_status")
            return {"count": sum(1 for d in self.updates.values() if status in (None, d["assumes_status"]))}
        return {}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    curriculum = tmp_path / "curriculum"
    (curriculum / "concepts").mkdir(parents=True)
    for c in CONCEPTS:
        (curriculum / "concepts" / f"{c['slug']}.yaml").write_text(
            f"concept: {c['concept']}\ndefinition: {c['definition']}\n"
            f"technical words: {', '.join(c['technical_words'])}\n", encoding="utf-8")
    es = FakeES()
    monkeypatch.setattr(tag_sessions.ES, "from_env", lambda: es)
    batches = FakeBatches({"101": answer(("scaling", "It tunes autoscalers.")), "102": None})
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda: NS(messages=NS(batches=batches))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    args = ["--curriculum", str(curriculum), "--tags", str(tmp_path / "tags.json"), "--poll-seconds", "0"]
    return NS(es=es, batches=batches, args=args, tags=tmp_path / "tags.json", state=tmp_path / ".tags-batch.json")


def test_without_yes_nothing_is_sent(setup, capsys):
    assert tag_sessions.main(setup.args) == 0
    assert setup.batches.created == [] and not setup.tags.exists()
    assert "2 to send" in capsys.readouterr().out


def test_tags_writes_cache_and_index_and_rerun_costs_nothing(setup, capsys):
    assert tag_sessions.main(setup.args + ["--yes"]) == 0
    assert len(setup.batches.created) == 1 and len(setup.batches.created[0]) == 2
    assert all(set(r) == {"custom_id", "params"} for r in setup.batches.created[0])
    tags = json.loads(setup.tags.read_text(encoding="utf-8"))
    assert tags["101"]["status"] == "tagged" and tags["102"]["status"] == "unknown"
    assert setup.es.updates["101"]["assumes"] == ["scaling"]
    assert setup.es.updates["102"]["assumes_status"] == "unknown"
    assert set(setup.es.mapping["properties"]) == {"assumes", "assumes_reason", "assumes_status"}
    assert not setup.state.exists()
    assert "tagged 1, unknown 1" in capsys.readouterr().out

    assert tag_sessions.main(setup.args + ["--yes"]) == 0
    assert len(setup.batches.created) == 1  # second run sent nothing
    assert "2 cached, 0 to send" in capsys.readouterr().out


def test_retry_unknown_resends_only_unknown(setup):
    tag_sessions.main(setup.args + ["--yes"])
    setup.batches.polls = 0
    tag_sessions.main(setup.args + ["--yes", "--retry-unknown"])
    assert [r["custom_id"] for r in setup.batches.created[-1]] == ["102"]


def test_apply_only_ignores_stale_cached_tags(setup):
    concepts = tag_sessions.read_concepts(setup.tags.parent / "curriculum")
    fingerprint = table_fingerprint(concepts)
    stale = entry_from_result("succeeded", answer(("scaling", "Stale tag.")), None, "old-key",
                              tag_sessions.DEFAULT_MODEL, SLUGS)
    current_key = cache_key(SESSIONS[1], fingerprint, tag_sessions.DEFAULT_MODEL)
    current = entry_from_result("succeeded", answer(("containers", "Current tag.")), None, current_key,
                                tag_sessions.DEFAULT_MODEL, SLUGS)
    setup.tags.write_text(json.dumps({"101": stale, "102": current}), encoding="utf-8")

    assert tag_sessions.main(setup.args + ["--apply-only"]) == 0

    assert setup.batches.created == []
    assert setup.es.updates["101"] == {"assumes": None, "assumes_reason": None, "assumes_status": "unknown"}
    assert setup.es.updates["102"] == {"assumes": ["containers"], "assumes_reason": ["Current tag."],
                                        "assumes_status": "tagged"}


def test_interrupted_run_resumes_saved_batch_instead_of_paying_twice(setup):
    setup.state.write_text(json.dumps({"batch_id": "batch_1", "requests": {
        "101": {"session_id": "101", "key": "old"}, "102": {"session_id": "102", "key": "old"}}}))
    setup.batches.created.append([{"custom_id": "101"}, {"custom_id": "102"}])  # the batch sent before the stop
    tag_sessions.main(setup.args + ["--yes"])
    assert len(setup.batches.created) == 1  # resumed, not created again
    assert json.loads(setup.tags.read_text(encoding="utf-8"))["101"]["status"] == "tagged"


def test_no_concepts_means_nothing_to_tag(tmp_path, capsys):
    assert tag_sessions.main(["--curriculum", str(tmp_path), "--tags", str(tmp_path / "t.json")]) == 1
    assert "nothing to tag against" in capsys.readouterr().err


def test_check_talks_print_tags_and_reasons(capsys):
    sessions = [{"id": "7", "title": "OpenFeature as a Control Plane for OpenTelemetry"}]
    tags = {"7": entry_from_result("succeeded", answer(("containers", "Runs in containers.")), None, "k", "m", SLUGS)}
    tag_sessions.show_check_talks(sessions, tags, ["OpenFeature as a Control Plane for OpenTelemetry", "Missing"])
    out = capsys.readouterr().out
    assert "containers: Runs in containers." in out and "not found in the sessions index" in out
