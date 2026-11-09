"""Push the Homeroom guide (our Elastic Agent Builder agent) from elastic/agent/ to Kibana.

    cd backend && python -m app.elastic_sync push [--dry-run]   # connector, tools, agent: create or update
    cd backend && python -m app.elastic_sync ask "question"     # ask the agent once, print its answer

elastic/agent/ holds the definitions: connector.json (an .inference connector to Claude, without the
key), tools/*.json (one index search and four ES|QL tools) and agent.json. Re-running push changes
only what differs, so a second run reports everything unchanged.

Environment, never arguments, so nothing secret lands in shell history:
    KIBANA_URL          default http://127.0.0.1:5601 (127.0.0.1, not localhost: see es.py)
    KIBANA_API_KEY      or ES_LOCAL_API_KEY (start-local's .env): sent as an ApiKey header
    ES_LOCAL_PASSWORD   used with the elastic user when there is no API key
    ANTHROPIC_API_KEY   only needed to create the connector, or with --rotate-key
Secrets are only ever sent as headers or request bodies. They are never printed.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.es import loopback

DIR = Path(__file__).resolve().parents[2] / "elastic" / "agent"
CONNECTORS = "/api/actions/connector"
TOOLS = "/api/agent_builder/tools"
AGENTS = "/api/agent_builder/agents"


class KibanaError(Exception):
    """Kibana refused a request. The message never contains a secret."""


class Kibana:
    def __init__(self, url: str, auth_header: str | None):
        self.url = loopback(url.rstrip("/"))
        self._headers = {"kbn-xsrf": "true", "Content-Type": "application/json"}
        if auth_header:
            self._headers["Authorization"] = auth_header

    @classmethod
    def from_env(cls) -> "Kibana":
        key = os.environ.get("KIBANA_API_KEY") or os.environ.get("ES_LOCAL_API_KEY")
        password = os.environ.get("ES_LOCAL_PASSWORD")
        if key:
            auth = f"ApiKey {key}"
        elif password:
            auth = "Basic " + base64.b64encode(f"elastic:{password}".encode()).decode()
        else:
            auth = None
        return cls(os.environ.get("KIBANA_URL", "http://127.0.0.1:5601"), auth)

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 120) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}")
            except ValueError:
                detail = {}
            return e.code, {"message": str(detail.get("message", ""))[:400]}
        except (urllib.error.URLError, OSError) as e:
            raise KibanaError(f"could not reach Kibana at {self.url}: {type(e).__name__}") from None


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def definitions(directory: Path = DIR) -> tuple[dict, list[dict], dict]:
    tools = [load(p) for p in sorted((directory / "tools").glob("*.json"))]
    return load(directory / "connector.json"), tools, load(directory / "agent.json")


def connector_secrets(connector: dict) -> dict:
    """Build the secrets object from the env vars named in secrets_from_env ("a.b": "ENV")."""
    secrets: dict = {}
    for dotted, env in connector.get("secrets_from_env", {}).items():
        value = os.environ.get(env)
        if not value:
            raise KibanaError(f"{env} is not set; it is needed to create the connector")
        *parents, leaf = dotted.split(".")
        node = secrets
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return secrets


def differs(live: dict, wanted: dict) -> bool:
    """True if any wanted field is different in the live object (fields Kibana adds are ignored)."""
    return any(live.get(k) != v for k, v in wanted.items())


def sync_connector(kb: Kibana, connector: dict, dry_run: bool, rotate_key: bool) -> str:
    cid = connector["id"]
    wanted = {"name": connector["name"], "config": connector["config"]}
    status, live = kb.request("GET", f"{CONNECTORS}/{cid}")
    if status == 404:
        if dry_run:
            return "create"
        body = {**wanted, "connector_type_id": connector["connector_type_id"], "secrets": connector_secrets(connector)}
        status, res = kb.request("POST", f"{CONNECTORS}/{cid}", body)
        return check(status, res, f"connector {cid}", "created")
    check(status, live, f"connector {cid}", "")
    if not differs(live, wanted) and not rotate_key:
        return "unchanged"
    if dry_run:
        return "update"
    # Kibana keeps the stored secret on an update, so the key is only sent when rotating it.
    body = {**wanted, "secrets": connector_secrets(connector)} if rotate_key else wanted
    status, res = kb.request("PUT", f"{CONNECTORS}/{cid}", body)
    return check(status, res, f"connector {cid}", "updated")


def sync_object(kb: Kibana, base: str, obj: dict, dry_run: bool, immutable: tuple[str, ...]) -> str:
    oid = obj["id"]
    status, live = kb.request("GET", f"{base}/{oid}")
    if status == 404:
        if dry_run:
            return "create"
        status, res = kb.request("POST", base, obj)
        return check(status, res, oid, "created")
    check(status, live, oid, "")
    wanted = {k: v for k, v in obj.items() if k not in ("id", *immutable)}
    if not differs(live, wanted):
        return "unchanged"
    if dry_run:
        return "update"
    status, res = kb.request("PUT", f"{base}/{oid}", wanted)
    return check(status, res, oid, "updated")


def check(status: int, body: dict, what: str, done: str) -> str:
    if status >= 300:
        raise KibanaError(f"{what}: HTTP {status} {body.get('message', '')}")
    return done


def push(kb: Kibana, dry_run: bool = False, rotate_key: bool = False, directory: Path = DIR) -> dict[str, str]:
    connector, tools, agent = definitions(directory)
    results = {f"connector {connector['id']}": sync_connector(kb, connector, dry_run, rotate_key)}
    for tool in tools:  # tools before the agent that uses them
        results[f"tool {tool['id']}"] = sync_object(kb, TOOLS, tool, dry_run, immutable=("type",))
    results[f"agent {agent['id']}"] = sync_object(kb, AGENTS, agent, dry_run, immutable=())
    return results


def ask(kb: Kibana, question: str, directory: Path = DIR) -> str:
    connector, _, agent = definitions(directory)
    status, res = kb.request("POST", "/api/agent_builder/converse",
                             {"input": question, "agent_id": agent["id"], "connector_id": connector["id"]}, timeout=300)
    check(status, res, "converse", "")
    return (res.get("response") or {}).get("message", "")


def main(argv: list[str] | None = None, kb: Kibana | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    kb = kb or Kibana.from_env()
    try:
        if args[:1] == ["push"]:
            results = push(kb, dry_run="--dry-run" in args, rotate_key="--rotate-key" in args)
            for what, outcome in results.items():
                print(f"{what}: {'would ' + outcome if '--dry-run' in args and outcome != 'unchanged' else outcome}")
            return 0
        if args[:1] == ["ask"] and len(args) > 1:
            print(ask(kb, " ".join(args[1:])))
            return 0
    except KibanaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
