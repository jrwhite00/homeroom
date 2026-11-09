"""Push the Elastic Workflows in elastic/workflows/ to Kibana, or run one.

    cd backend && python -m app.elastic_workflows push [--dry-run]
    cd backend && python -m app.elastic_workflows run talk-cancelled --input talk_id=1371083 [--input simulated=true]

push validates every workflow with Kibana's own validator, then creates it or updates it by name
(the workflow id is its name). Unchanged workflows are left alone, so a rerun changes nothing. It
also creates the homeroom-workflow-events index with its mapping if it doesn't exist yet.

Connection comes from the environment, never from arguments:
    KIBANA_URL      default http://127.0.0.1:5601
    KIBANA_API_KEY  an Elasticsearch API key (falls back to ES_API_KEY, then ES_LOCAL_API_KEY)
    ES_URL / ES_API_KEY (or ES_LOCAL_*) for creating the events index, as elsewhere
Keys are only sent as headers and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.es import ES, ESError, loopback

DIR = Path(__file__).resolve().parents[2] / "elastic" / "workflows"
EVENTS_INDEX = "homeroom-workflow-events"
EVENTS_MAPPING = {"mappings": {"properties": {
    "type": {"type": "keyword"}, "talk_id": {"type": "keyword"}, "title": {"type": "text"},
    "starts_at": {"type": "date"}, "slot": {"type": "keyword"}, "students_affected": {"type": "integer"},
    "total": {"type": "integer"}, "untagged": {"type": "integer"}, "simulated": {"type": "boolean"},
    "note": {"type": "keyword"},
    "at": {"type": "date"}, "execution_id": {"type": "keyword"},
}}}
DONE = {"completed", "failed", "cancelled", "timed_out"}


class Kibana:
    def __init__(self, url: str, api_key: str | None, timeout: float = 30):
        self.url = loopback(url.rstrip("/"))
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json", "kbn-xsrf": "true"}
        if api_key:
            self._headers["Authorization"] = f"ApiKey {api_key}"

    @classmethod
    def from_env(cls) -> "Kibana":
        key = os.environ.get("KIBANA_API_KEY") or os.environ.get("ES_API_KEY") or os.environ.get("ES_LOCAL_API_KEY")
        return cls(os.environ.get("KIBANA_URL", "http://127.0.0.1:5601"), key)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(self.url + path, method=method, headers=self._headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:600]}
        except (urllib.error.URLError, OSError) as e:
            return 0, {"error": f"could not reach Kibana at {self.url}: {type(e).__name__}"}


def workflow_name(yaml_text: str) -> str:
    m = re.search(r"^name:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$", yaml_text, re.M)
    if not m:
        raise ValueError("workflow YAML has no top-level name")
    return m.group(1)


def read_workflows(directory: Path = DIR) -> dict[str, str]:
    """{name: yaml} for every *.yaml in the folder, in file order."""
    return {workflow_name(p.read_text(encoding="utf-8")): p.read_text(encoding="utf-8")
            for p in sorted(directory.glob("*.yaml"))}


def validate(kb: Kibana, yaml_text: str) -> list[str]:
    """Kibana's own validator (the Agent Builder platform tool). Returns error messages."""
    status, body = kb.call("POST", "/api/agent_builder/tools/_execute",
                           {"tool_id": "platform.workflows.validate_workflow", "tool_params": {"yaml": yaml_text}})
    if status >= 300 or status == 0:
        return [f"validator unavailable: HTTP {status} {body.get('error', '')}".strip()]
    result = body["results"][0]["data"]["result"]
    return [] if result["valid"] else [d.get("message", "invalid") for d in result.get("diagnostics", [])]


def ensure_events_index(es: ES, dry_run: bool) -> str:
    if es.request("HEAD", f"/{EVENTS_INDEX}", ok404=True) is not None:
        return "exists"
    if dry_run:
        return "would create"
    es.request("PUT", f"/{EVENTS_INDEX}", EVENTS_MAPPING)
    return "created"


def push(kb: Kibana, es: ES, workflows: dict[str, str], dry_run: bool = False) -> int:
    try:
        print(f"index {EVENTS_INDEX}: {ensure_events_index(es, dry_run)}")
    except ESError as e:
        print(f"index {EVENTS_INDEX}: failed ({e})", file=sys.stderr)
        return 1
    ok = True
    for name, yaml_text in workflows.items():
        errors = validate(kb, yaml_text)
        if errors:
            print(f"workflow {name}: invalid, not saved: {'; '.join(errors)}", file=sys.stderr)
            ok = False
            continue
        status, current = kb.call("GET", f"/api/workflows/workflow/{name}")
        if status == 200 and current.get("yaml") == yaml_text:
            print(f"workflow {name}: unchanged")
            continue
        if dry_run:
            print(f"workflow {name}: would {'update' if status == 200 else 'create'}")
            continue
        if status == 200:
            status, body = kb.call("PUT", f"/api/workflows/workflow/{name}", {"yaml": yaml_text})
            done = status < 300 and body.get("valid", True)
            print(f"workflow {name}: " + ("updated" if done else f"update failed: HTTP {status} {body.get('error', '')}"))
        else:
            status, body = kb.call("POST", "/api/workflows", {"workflows": [{"yaml": yaml_text}]})
            created = [w.get("id") for w in body.get("created", [])]
            done = status < 300 and name in created
            print(f"workflow {name}: " + ("created" if done else f"create failed: HTTP {status} {body.get('error', body)}"))
        ok &= done
    return 0 if ok else 1


def parse_inputs(pairs: list[str]) -> dict:
    inputs = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        inputs[key] = {"true": True, "false": False}.get(value.lower(), value)
    return inputs


def run(kb: Kibana, name: str, inputs: dict, wait: float = 60, poll: float = 2, sleep=time.sleep) -> int:
    status, body = kb.call("POST", f"/api/workflows/workflow/{name}/run", {"inputs": inputs})
    execution = body.get("workflowExecutionId") or body.get("executionId")
    if status >= 300 or not execution:
        print(f"run {name} failed: HTTP {status} {body.get('error', body)}", file=sys.stderr)
        return 1
    deadline = time.monotonic() + wait
    while True:
        status, ex = kb.call("GET", f"/api/workflows/executions/{execution}")
        if ex.get("status") in DONE or time.monotonic() > deadline:
            break
        sleep(poll)
    print(f"execution {execution}: {ex.get('status')}")
    for step in ex.get("stepExecutions", []):
        print(f"  {step.get('stepId')}: {step.get('status')}" + (f" ({step['error']})" if step.get("error") else ""))
    return 0 if ex.get("status") == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push", help="validate, then create or update every workflow in elastic/workflows/")
    p.add_argument("--dry-run", action="store_true", help="validate and report, change nothing")
    r = sub.add_parser("run", help="run one workflow and wait for it")
    r.add_argument("name")
    r.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    kb = Kibana.from_env()
    if args.cmd == "push":
        return push(kb, ES.from_env(), read_workflows(), args.dry_run)
    return run(kb, args.name, parse_inputs(args.input))


if __name__ == "__main__":
    sys.exit(main())
