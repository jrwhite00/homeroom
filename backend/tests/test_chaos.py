import contextlib
import random
from collections import Counter

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import chaos, loadgen
from app.main import app
from app.search import get_es


class FakeSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


@pytest.fixture
def spans(monkeypatch):
    """Record attributes set on the current span and on spans chaos starts."""
    current, started = FakeSpan(), []

    @contextlib.contextmanager
    def start(name, attributes=None):
        span = FakeSpan()
        span.attributes.update(attributes or {})
        started.append((name, span))
        yield span

    monkeypatch.setattr(chaos.trace, "get_current_span", lambda: current)
    monkeypatch.setattr(main_module.trace, "get_current_span", lambda: current)
    monkeypatch.setattr(chaos.tracer, "start_as_current_span", start)
    monkeypatch.setattr(chaos.time, "sleep", lambda s: started.append(("slept", s)))
    yield current, started
    chaos.set_enabled(False)


class StubES:
    def search(self, index, body):
        return {"hits": {"hits": []}}


@pytest.fixture
def client():
    app.dependency_overrides[get_es] = lambda: StubES()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_off_by_default_and_labeled(client):
    body = client.get("/chaos").json()
    assert body["enabled"] is False and body["simulated"] is True and body["delay_ms"] == 800
    assert "Simulated slowdown" in body["label"]


def test_flipping_needs_a_configured_token(client, monkeypatch):
    monkeypatch.delenv("CHAOS_TOKEN", raising=False)
    assert client.post("/chaos", json={"enabled": True}).status_code == 503
    monkeypatch.setenv("CHAOS_TOKEN", "s3cret")
    assert client.post("/chaos", json={"enabled": True}).status_code == 401
    assert client.post("/chaos", json={"enabled": True}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert chaos.enabled() is False


def test_on_adds_800ms_to_search_and_marks_spans_induced(client, monkeypatch, spans):
    current, started = spans
    monkeypatch.setenv("CHAOS_TOKEN", "s3cret")
    r = client.post("/chaos", json={"enabled": True}, headers={"Authorization": "Bearer s3cret"})
    assert r.json()["enabled"] is True
    body = client.get("/search", params={"q": "autoscaling"}).json()
    assert ("slept", 0.8) in started
    assert current.attributes["induced"] is True
    name, delay_span = started[0]
    assert name == "chaos.delay" and delay_span.attributes["induced"] is True
    assert body["induced_delay"]["simulated"] is True


def test_off_means_no_delay_and_no_induced_label(client, spans):
    current, started = spans
    body = client.get("/search", params={"q": "autoscaling"}).json()
    assert started == [] and "induced" not in current.attributes and "induced_delay" not in body


def test_load_generator_requests_are_labeled_simulated(client, spans):
    current, _ = spans
    client.get("/healthz", headers={"X-Homeroom-Simulated": "loadgen"})
    assert current.attributes["simulated"] is True


def test_load_generator_mix_and_no_pseudonyms_sent():
    rng = random.Random(7)
    picks = [loadgen.pick_request(rng) for _ in range(2000)]
    kinds = Counter(k for k, _ in picks)
    assert 0.55 < kinds["search"] / 2000 < 0.65 and 0.2 < kinds["week"] / 2000 < 0.3
    names = {p["name"] for p in loadgen.PERSONAS}
    assert not any(n in path for _, path in picks for n in names)
    assert {path.split("?")[0] for _, path in picks} == {"/search", "/week", "/placements"}


def test_load_generator_sends_the_simulated_header(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        seen.update(request.headers)
        return Resp()
    monkeypatch.setattr(loadgen.urllib.request, "urlopen", fake_urlopen)
    assert loadgen.send("http://backend", "/search?q=x") == 200
    assert seen["X-homeroom-simulated"] == "loadgen"
