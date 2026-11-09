import contextlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dash0_sync, search, telemetry
from app.main import app
from app.search import get_es

DASH0 = Path(__file__).resolve().parents[2] / "observability" / "dash0"


class Recorder:
    def __init__(self):
        self.points = []

    def add(self, value, attrs):
        self.points.append((value, attrs))

    record = add


class FakeSpan:
    def __init__(self, attributes=None):
        self.attributes = dict(attributes or {})

    def set_attribute(self, k, v):
        self.attributes[k] = v


@pytest.fixture
def metrics(monkeypatch):
    counter, histogram = Recorder(), Recorder()
    monkeypatch.setattr(telemetry, "requests_counter", counter)
    monkeypatch.setattr(telemetry, "duration_histogram", histogram)
    return counter, histogram


@pytest.fixture
def spans(monkeypatch):
    started = []

    @contextlib.contextmanager
    def start(name, attributes=None):
        span = FakeSpan(attributes)
        started.append((name, span))
        yield span
    monkeypatch.setattr(search.tracer, "start_as_current_span", start)
    return started


class StubES:
    def search(self, index, body):
        return {"hits": {"hits": [{"_score": 1.0, "_source": {"title": "KEDA"}}]}}


@pytest.fixture
def client():
    app.dependency_overrides[get_es] = lambda: StubES()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_request_metrics_use_the_route_template_and_labels(client, metrics):
    counter, histogram = metrics
    client.get("/search", params={"q": "autoscaling"}, headers={"X-Homeroom-Simulated": "loadgen"})
    (value, attrs), = counter.points
    assert value == 1 and attrs == {"http.route": "/search", "http.request.method": "GET", "http.status_class": "2xx",
                                    "simulated": True, "induced": False}
    assert histogram.points[0][0] >= 0
    assert "autoscaling" not in json.dumps(attrs)


def test_unhandled_exception_records_500_request_metrics(metrics):
    from fastapi import FastAPI

    failing_app = FastAPI()
    telemetry.install(failing_app)

    @failing_app.get("/failure")
    def fail():
        raise RuntimeError("expected test failure")

    response = TestClient(failing_app, raise_server_exceptions=False).get("/failure")
    assert response.status_code == 500
    counter, histogram = metrics
    (value, attrs), = counter.points
    assert value == 1 and attrs == {"http.route": "/failure", "http.request.method": "GET", "http.status_class": "5xx",
                                    "simulated": False, "induced": False}
    assert histogram.points[0][0] >= 0


def test_search_span_has_useful_attributes(client, spans):
    client.get("/search", params={"q": "autoscaling", "mode": "keyword"})
    name, span = next(s for s in spans if s[0] == "homeroom.search")
    assert span.attributes == {"search.mode": "keyword", "search.translate": False, "search.result_count": 1}


def test_placement_stats_count_pending_and_three_talks_a_day_without_names():
    rows = [{"class": "demo", "student": "maya", "slot": f"thu-{h}00", "status": {"phase": "Running"}} for h in (9, 10, 11)]
    rows += [{"class": "demo", "student": "theo", "slot": "thu-0900", "status": {"phase": "Running"}},
             {"class": "demo", "student": "theo", "slot": "thu-1000", "status": {"phase": "Pending"}}]
    pending, days = telemetry.placement_stats(rows)
    assert pending == [("demo", 1)]
    assert days == [("demo", False, 1), ("demo", True, 1)]
    assert "maya" not in json.dumps([pending, days]) and "theo" not in json.dumps([pending, days])


def test_readiness_span_carries_the_talk_not_the_student(monkeypatch):
    seen = []

    @contextlib.contextmanager
    def start(name, attributes=None):
        seen.append((name, attributes))
        yield FakeSpan(attributes)
    monkeypatch.setattr(telemetry.tracer, "start_as_current_span", start)
    with telemetry.readiness_span("1250326", known=3):
        pass
    assert seen == [("homeroom.readiness", {"talk.id": "1250326", "student.concepts_known": 3})]


def test_dash0_definitions_hold_the_two_targets():
    dashboard = json.loads((DASH0 / "dashboard.json").read_text())
    assert dashboard["kind"] == "Dashboard" and dashboard["metadata"]["dash0Extensions"]["id"] == "homeroom-overview"
    queries = json.dumps(dashboard["spec"]["panels"])
    for metric in ("homeroom.http.requests", "homeroom.http.duration", "homeroom.students.pending", "homeroom.student_days"):
        assert metric in queries
    latency = json.loads((DASH0 / "slo-schedule-latency.json").read_text())
    placed = json.loads((DASH0 / "slo-students-placed.json").read_text())
    assert latency["spec"]["objectives"] == [{"target": 0.99}] and placed["spec"]["objectives"] == [{"target": 0.99}]
    assert "histogram_fraction(0, 2" in json.dumps(latency) and 'http_route=\\"/week\\"' in json.dumps(latency)
    assert "placed_3_or_more" in json.dumps(placed)


def test_dash0_sync_push_upserts_by_origin_and_never_prints_token(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(dash0_sync, "call", lambda m, p, b=None: (calls.append((m, p)), (200, {}))[1])
    monkeypatch.setenv("DASH0_API_TOKEN", "auth_secret_token")
    assert dash0_sync.main(["push"]) == 0
    assert calls == [("PUT", "/api/dashboards/homeroom-overview"), ("PUT", "/api/slos/homeroom-schedule-latency"),
                     ("PUT", "/api/slos/homeroom-three-talks-a-day")]
    assert "auth_secret_token" not in capsys.readouterr().out
