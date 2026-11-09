"""localhost becomes 127.0.0.1, so Windows doesn't wait ~2 s per request trying IPv6 first."""
import pytest

from app.es import ES, loopback


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:9200", "http://127.0.0.1:9200"),
    ("https://localhost:9200/", "https://127.0.0.1:9200/"),
    ("http://localhost", "http://127.0.0.1"),
    ("http://127.0.0.1:9200", "http://127.0.0.1:9200"),
    ("http://elasticsearch.manifest.svc:9200", "http://elasticsearch.manifest.svc:9200"),
    ("http://localhost.example.com:9200", "http://localhost.example.com:9200"),
])
def test_loopback(url, expected):
    assert loopback(url) == expected


def test_client_uses_loopback_for_es_local_url(monkeypatch):
    monkeypatch.delenv("ES_URL", raising=False)
    monkeypatch.setenv("ES_LOCAL_URL", "http://localhost:9200")
    assert ES.from_env().url == "http://127.0.0.1:9200"


def test_default_url(monkeypatch):
    monkeypatch.delenv("ES_URL", raising=False)
    monkeypatch.delenv("ES_LOCAL_URL", raising=False)
    assert ES.from_env().url == "http://127.0.0.1:9200"
