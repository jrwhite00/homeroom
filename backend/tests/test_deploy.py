"""deploy/backend.yaml points the backend at Elasticsearch, with the key only ever from a secret.

Read as text (no YAML dependency): each env entry is a `- name:` line and the lines indented under it.
"""
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "deploy" / "backend.yaml"


def env_entry(name: str) -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    indent = len(lines[start]) - len(lines[start].lstrip())
    entry = [lines[start].strip()]
    for line in lines[start + 1:]:
        if not line.strip() or len(line) - len(line.lstrip()) <= indent:
            break
        entry.append(line.strip())
    return entry


def test_backend_reaches_the_start_local_elasticsearch_by_name():
    assert env_entry("ES_URL") == ["- name: ES_URL", "value: http://es-local-dev:9200"]


def test_elasticsearch_key_comes_from_an_optional_secret_never_a_value():
    entry = env_entry("ES_API_KEY")
    assert not any(line.startswith("value:") for line in entry)
    assert entry[1:] == ["valueFrom:", "secretKeyRef:", "name: homeroom-es", "key: api_key", "optional: true"]
