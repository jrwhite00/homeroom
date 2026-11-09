import copy
import json
import re

import pytest

from app import elastic_sync
from app.elastic_sync import AGENTS, CONNECTORS, TOOLS, Kibana, KibanaError, push

KEY = "sk-ant-not-a-real-key-123"


class StubKibana(Kibana):
    """Stores connectors, tools and agents like Kibana does, and records every call."""

    def __init__(self):
        super().__init__("http://127.0.0.1:5601", None)
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, body=None, timeout=0):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == "/api/agent_builder/converse":
            return 200, {"response": {"message": f"answer to: {body['input']}"}}
        if method == "GET":
            return (200, copy.deepcopy(self.objects[path])) if path in self.objects else (404, {"message": "not found"})
        if method == "POST" and path.startswith(CONNECTORS):
            self.objects[path] = {"id": path.rsplit("/", 1)[1], **{k: v for k, v in body.items() if k != "secrets"}}
            return 200, {}
        if method == "POST":
            self.objects[f"{path}/{body['id']}"] = copy.deepcopy(body)
            return 200, {}
        if method == "PUT":
            self.objects[path].update({k: v for k, v in body.items() if k != "secrets"})
            return 200, {}
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


@pytest.fixture
def kb(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    return StubKibana()


def test_first_push_creates_everything_and_second_changes_nothing(kb):
    first = push(kb)
    assert set(first.values()) == {"created"}
    assert len(first) == 7  # connector, five tools, agent
    kb.calls.clear()
    second = push(kb)
    assert set(second.values()) == {"unchanged"}
    assert kb.writes() == []


def test_tools_are_created_before_the_agent(kb):
    push(kb)
    posts = [p for m, p in kb.writes() if m == "POST"]
    assert posts.index(AGENTS) == len(posts) - 1 and posts.count(TOOLS) == 5


def test_connector_secret_comes_from_the_environment(kb):
    push(kb)
    body = next(b for m, p, b in kb.calls if m == "POST" and p.startswith(CONNECTORS))
    assert body["secrets"] == {"providerSecrets": {"api_key": KEY}}
    assert "secrets_from_env" not in body
    assert KEY not in json.dumps(kb.objects)  # Kibana keeps secrets apart; we never echo them


def test_changed_tool_is_updated_without_id_or_type(kb, tmp_path):
    push(kb)
    directory = copy_definitions(tmp_path)
    tool_file = directory / "tools" / "homeroom.talks.day.json"
    tool = json.loads(tool_file.read_text(encoding="utf-8"))
    tool["description"] = "A clearer description."
    tool_file.write_text(json.dumps(tool), encoding="utf-8")
    kb.calls.clear()
    results = push(kb, directory=directory)
    assert results["tool homeroom.talks.day"] == "updated"
    method, path, body = next(c for c in kb.calls if c[0] == "PUT")
    assert path == f"{TOOLS}/homeroom.talks.day" and "id" not in body and "type" not in body
    assert [r for k, r in results.items() if k != "tool homeroom.talks.day"] == ["unchanged"] * 6


def test_dry_run_writes_nothing(kb):
    results = push(kb, dry_run=True)
    assert set(results.values()) == {"create"}
    assert kb.writes() == []


def test_missing_key_names_the_variable_never_a_value(kb, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(KibanaError, match="ANTHROPIC_API_KEY is not set"):
        push(kb)


def test_main_never_prints_the_key(kb, capsys):
    assert elastic_sync.main(["push"], kb=kb) == 0
    assert elastic_sync.main(["push", "--rotate-key"], kb=kb) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err
    assert "connector homeroom-claude: updated" in out.out  # --rotate-key resends the secret


def test_kibana_error_is_reported_plainly(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    class Refusing(StubKibana):
        def request(self, method, path, body=None, timeout=0):
            return (404, {}) if method == "GET" else (400, {"message": "bad tool"})
    assert elastic_sync.main(["push", "--dry-run"], kb=Refusing()) == 0
    assert elastic_sync.main(["push"], kb=Refusing()) == 1
    assert "HTTP 400 bad tool" in capsys.readouterr().err


def test_ask_sends_the_question_to_the_homeroom_guide(kb):
    answer = elastic_sync.ask(kb, "What could I go to on Thursday?")
    assert answer == "answer to: What could I go to on Thursday?"
    _, path, body = kb.calls[-1]
    assert path == "/api/agent_builder/converse"
    assert body["agent_id"] == "homeroom-guide" and body["connector_id"] == "homeroom-claude"


def test_auth_and_url_from_environment(monkeypatch):
    for name in ("KIBANA_API_KEY", "ES_LOCAL_API_KEY", "ES_LOCAL_PASSWORD", "KIBANA_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KIBANA_URL", "http://localhost:5601")
    monkeypatch.setenv("ES_LOCAL_PASSWORD", "pw")
    kb = Kibana.from_env()
    assert kb.url == "http://127.0.0.1:5601"  # localhost would wait for the IPv6 fallback
    assert kb._headers["Authorization"].startswith("Basic ")
    monkeypatch.setenv("ES_LOCAL_API_KEY", "abc")
    assert Kibana.from_env()._headers["Authorization"] == "ApiKey abc"


# --- the definitions in elastic/agent/ ----------------------------------------------------------

def test_agent_uses_exactly_the_tools_in_the_folder():
    connector, tools, agent = elastic_sync.definitions()
    ids = [t["id"] for t in tools]
    assert sorted(agent["configuration"]["tools"][0]["tool_ids"]) == sorted(ids)
    assert all(t["id"].startswith("homeroom.") for t in tools)


def test_esql_tools_declare_every_parameter_they_use():
    _, tools, _ = elastic_sync.definitions()
    for tool in tools:
        if tool["type"] != "esql":
            continue
        used = set(re.findall(r"\?(\w+)", tool["configuration"]["query"]))
        declared = tool["configuration"]["params"]
        assert used == set(declared), tool["id"]
        assert all(p["type"] in ("string", "integer", "float", "boolean", "date", "array") for p in declared.values())


def test_connector_file_holds_no_secret():
    connector, _, _ = elastic_sync.definitions()
    text = json.dumps(connector)
    assert "sk-ant" not in text and "secrets" not in connector
    assert connector["secrets_from_env"] == {"providerSecrets.api_key": "ANTHROPIC_API_KEY"}


def test_instructions_follow_the_house_rules():
    _, _, agent = elastic_sync.definitions()
    text = agent["configuration"]["instructions"]
    assert 'say "concept", never "topic"' in text
    assert "Never ask for or repeat a name, school" in text
    assert "Mountain Time" in text


def copy_definitions(tmp_path):
    import shutil
    target = tmp_path / "agent"
    shutil.copytree(elastic_sync.DIR, target)
    return target


def test_connector_config_change_resends_key(kb, tmp_path):
    push(kb)
    directory = copy_definitions(tmp_path)
    connector_file = directory / "connector.json"
    connector = json.loads(connector_file.read_text(encoding="utf-8"))
    connector["config"]["taskTypeConfig"]["max_tokens"] = 4096
    connector_file.write_text(json.dumps(connector), encoding="utf-8")
    kb.calls.clear()
    assert push(kb, directory=directory)["connector homeroom-claude"] == "updated"
    _, path, body = next(c for c in kb.calls if c[0] == "PUT")
    assert path == f"{CONNECTORS}/homeroom-claude"
    assert body["secrets"] == {"providerSecrets": {"api_key": KEY}}


def test_day_tools_return_the_tag_status_and_accept_concept_names():
    _, tools, _ = elastic_sync.definitions()
    by_id = {t["id"]: t for t in tools}
    for tid in ("homeroom.talks.day", "homeroom.talks.concept_day"):
        assert "assumes_status" in by_id[tid]["configuration"]["query"]
        assert "isn't sorted yet" in by_id[tid]["description"]
    assert 'TO_LOWER(REPLACE(?concept, " ", "-"))' in by_id["homeroom.talks.concept_day"]["configuration"]["query"]
