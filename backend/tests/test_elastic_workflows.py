"""elastic_workflows: validate, then create or update by name; run and wait; keys never printed."""
import re

import pytest

from app import elastic_workflows as ew

KEY = "es-api-key-should-never-appear-123"
GOOD = 'version: "1"\nname: tagging-check\nsteps: []\n'
OTHER = 'version: "1"\nname: talk-cancelled\nsteps: []\n'


class StubKibana:
    def __init__(self, existing=None, invalid=(), run_status="completed"):
        self.workflows = dict(existing or {})
        self.invalid, self.run_status, self.calls = set(invalid), run_status, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/api/agent_builder/tools/_execute":
            bad = ew.workflow_name(body["tool_params"]["yaml"]) in self.invalid
            diagnostics = [{"message": "steps must be an array"}] if bad else []
            return 200, {"results": [{"data": {"result": {"valid": not bad, "diagnostics": diagnostics}}}]}
        if method == "GET" and path.startswith("/api/workflows/workflow/"):
            name = path.rsplit("/", 1)[1]
            return (200, {"id": name, "yaml": self.workflows[name]}) if name in self.workflows else (404, {"error": "not found"})
        if method == "PUT" and path.startswith("/api/workflows/workflow/"):
            self.workflows[path.rsplit("/", 1)[1]] = body["yaml"]
            return 200, {"valid": True}
        if method == "POST" and path == "/api/workflows":
            created = []
            for w in body["workflows"]:
                name = ew.workflow_name(w["yaml"])
                self.workflows[name] = w["yaml"]
                created.append({"id": name})
            return 200, {"created": created}
        if method == "POST" and path.endswith("/run"):
            return 200, {"workflowExecutionId": "ex-1"}
        if method == "GET" and path == "/api/workflows/executions/ex-1":
            return 200, {"status": self.run_status, "stepExecutions": [{"stepId": "count_tags", "status": self.run_status}]}
        raise AssertionError(f"unexpected {method} {path}")


class StubES:
    def __init__(self, exists=False):
        self.exists, self.calls = exists, []

    def request(self, method, path, body=None, ok404=False, **kw):
        self.calls.append((method, path))
        if method == "HEAD":
            return {} if self.exists else None
        if method == "PUT":
            self.exists = True
            assert body == ew.EVENTS_MAPPING
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def writes(stub):
    return [c for c in stub.calls if c[0] in ("PUT", "POST") and "_execute" not in c[1] and not c[1].endswith("/run")]


def test_first_push_creates_index_and_workflows_then_rerun_changes_nothing(capsys):
    kb, es = StubKibana(), StubES()
    assert ew.push(kb, es, {"tagging-check": GOOD, "talk-cancelled": OTHER}) == 0
    assert kb.workflows == {"tagging-check": GOOD, "talk-cancelled": OTHER}
    assert ("PUT", "/homeroom-workflow-events") in es.calls
    kb.calls.clear()
    assert ew.push(kb, es, {"tagging-check": GOOD, "talk-cancelled": OTHER}) == 0
    assert writes(kb) == []
    out = capsys.readouterr().out
    assert "workflow tagging-check: unchanged" in out and "index homeroom-workflow-events: exists" in out


def test_changed_workflow_is_updated_in_place():
    kb = StubKibana(existing={"tagging-check": GOOD})
    changed = GOOD + "description: new\n"
    assert ew.push(kb, StubES(exists=True), {"tagging-check": changed}) == 0
    assert kb.workflows["tagging-check"] == changed
    assert ("PUT", "/api/workflows/workflow/tagging-check") in kb.calls


def test_invalid_workflow_is_not_saved_and_push_fails(capsys):
    kb = StubKibana(invalid={"talk-cancelled"})
    assert ew.push(kb, StubES(exists=True), {"tagging-check": GOOD, "talk-cancelled": OTHER}) == 1
    assert "talk-cancelled" not in kb.workflows and "tagging-check" in kb.workflows
    assert "steps must be an array" in capsys.readouterr().err


def test_dry_run_validates_but_writes_nothing(capsys):
    kb, es = StubKibana(), StubES()
    assert ew.push(kb, es, {"tagging-check": GOOD}, dry_run=True) == 0
    assert writes(kb) == [] and ("PUT", "/homeroom-workflow-events") not in es.calls
    assert ("POST", "/api/agent_builder/tools/_execute") in kb.calls
    assert "would create" in capsys.readouterr().out


def test_run_waits_for_the_execution_and_reports_steps(capsys):
    assert ew.run(StubKibana(), "tagging-check", {}, sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "execution ex-1: completed" in out and "count_tags: completed" in out
    assert ew.run(StubKibana(run_status="failed"), "tagging-check", {}, sleep=lambda s: None) == 1


def test_inputs_parse_booleans():
    assert ew.parse_inputs(["talk_id=1371083", "simulated=false"]) == {"talk_id": "1371083", "simulated": False}


def test_repo_workflows_have_names_and_write_only_to_the_events_index():
    workflows = ew.read_workflows()
    assert set(workflows) == {"talk-cancelled", "tagging-check"}
    for yaml_text in workflows.values():
        for index in re.findall(r"^\s+index: (\S+)", yaml_text, re.M):
            assert index in ("sessions", ew.EVENTS_INDEX)


def talk_cancelled_steps():
    """talk-cancelled's YAML and its steps as (indent, name, type), parsed from the file."""
    text = ew.read_workflows()["talk-cancelled"]
    return text, re.findall(r"^( *)- name: (\S+)\n\1  type: (\S+)", text, re.M)


def test_talk_cancelled_is_simulated_by_default():
    text, _ = talk_cancelled_steps()
    assert re.search(r"- name: simulated\n\s+type: boolean\n\s+default: true", text)


def test_simulated_run_never_touches_the_session():
    # The only write to sessions is mark_cancelled, nested under an if that runs only when simulated is false.
    text, steps = talk_cancelled_steps()
    updates = [(indent, name) for indent, name, kind in steps if kind == "elasticsearch.update"]
    assert updates == [("      ", "mark_cancelled")]  # nested one level, inside the if
    if_block = text[text.index("- name: apply_if_real"):text.index("- name: write_summary")]
    assert 'condition: "inputs.simulated : false"' in if_block and "mark_cancelled" in if_block
    assert "else:" not in if_block
    top = [name for indent, name, kind in steps if indent == "  "]
    assert top == ["get_talk", "count_students", "apply_if_real", "write_summary"]


def test_summary_always_written_and_says_which_path_ran():
    text, steps = talk_cancelled_steps()
    summary = text[text.index("- name: write_summary"):]
    assert f"index: {ew.EVENTS_INDEX}" in summary and 'simulated: "{{ inputs.simulated }}"' in summary
    assert "simulated: talk not changed" in summary and "talk marked cancelled" in summary
    assert "note" in ew.EVENTS_MAPPING["mappings"]["properties"]


def test_students_are_counted_never_copied_out():
    text, _ = talk_cancelled_steps()
    assert "COUNT_DISTINCT(student)" in text and "student }}" not in text


def test_key_goes_only_in_the_header(monkeypatch):
    monkeypatch.setenv("KIBANA_API_KEY", KEY)
    kb = ew.Kibana.from_env()
    assert KEY not in kb.url and kb._headers["Authorization"] == f"ApiKey {KEY}"


def test_unreachable_kibana_is_reported_without_the_key(monkeypatch, capsys):
    kb = ew.Kibana("http://127.0.0.1:1", KEY, timeout=1)
    errors = ew.validate(kb, GOOD)
    assert errors and KEY not in " ".join(errors)
