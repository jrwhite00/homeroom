"""ES.search turns every way the connection can fail into ESError, so /search answers 502, not 500."""
import http.client
import io

import pytest
from fastapi.testclient import TestClient

from app import es as es_module
from app.es import ES, ESError
from app.main import app
from app.search import get_es


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PartialResponse(FakeResponse):
    def read(self, *args):
        raise http.client.IncompleteRead(b'{"hits"', 100)


def dropped_connection(*args, **kwargs):
    # Raised by getresponse() when Elasticsearch closes the socket; urllib does not wrap it.
    raise http.client.RemoteDisconnected("Remote end closed connection without response")


def reset_connection(*args, **kwargs):
    raise ConnectionResetError(104, "Connection reset by peer")


FAILURES = {
    "dropped connection": dropped_connection,
    "connection reset": reset_connection,
    "partial read": lambda *a, **k: PartialResponse(),
    "non-JSON 200": lambda *a, **k: FakeResponse(b"<html>proxy login</html>"),
}


@pytest.fixture
def real_es():
    client = ES("http://es.test:9200", api_key="not-a-real-key")
    app.dependency_overrides[get_es] = lambda: client
    yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
def test_search_raises_eserror(monkeypatch, failure):
    monkeypatch.setattr(es_module.urllib.request, "urlopen", failure)
    with pytest.raises(ESError):
        ES("http://es.test:9200").search("sessions", {})


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
def test_search_endpoint_returns_502(monkeypatch, real_es, failure):
    monkeypatch.setattr(es_module.urllib.request, "urlopen", failure)
    r = TestClient(app).get("/search", params={"q": "autoscaling"})
    assert r.status_code == 502
    assert "es.test" not in r.text and "not-a-real-key" not in r.text


def test_non_json_error_message_names_the_index(monkeypatch):
    monkeypatch.setattr(es_module.urllib.request, "urlopen", FAILURES["non-JSON 200"])
    with pytest.raises(ESError, match="sessions returned a response that is not JSON"):
        ES("http://es.test:9200").search("sessions", {})
