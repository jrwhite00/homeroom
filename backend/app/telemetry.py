"""Custom OpenTelemetry spans and metrics for Dash0.

The Dash0 operator injects the OpenTelemetry SDK and exporter into the backend pod; this module only
uses the API, so locally and in tests everything here is a cheap no-op.

Metrics (Dash0 PromQL: {otel_metric_name="<name>"}):
  homeroom.http.requests       counter    http.route, http.request.method, http.status_class, simulated, induced
  homeroom.http.duration       histogram  same attributes, seconds, buckets up to 2 s for the schedule target
  homeroom.students.pending    gauge      class: student hours kube-scheduler left Pending
  homeroom.student_days        gauge      class, placed_3_or_more: student-days, split by whether the student
                                          got at least three talks that day (the second target)

Never put a student identifier (pseudonym or otherwise) on a span, metric or log. Only counts, routes,
concept slugs and classes.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.metrics import CallbackOptions, Observation

tracer = trace.get_tracer("homeroom")
meter = metrics.get_meter("homeroom")

DURATION_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

requests_counter = meter.create_counter("homeroom.http.requests", unit="{request}",
                                        description="HTTP requests handled by the backend")
duration_histogram = meter.create_histogram("homeroom.http.duration", unit="s",
                                            description="HTTP request duration",
                                            explicit_bucket_boundaries_advisory=DURATION_BUCKETS)

# Filled by refresh_placement_gauges(); read by the observable gauges at export time.
_gauges: dict[str, list] = {"pending": [], "days": []}


def _observe_pending(options: CallbackOptions):
    return [Observation(v, {"class": c}) for c, v in _gauges["pending"]]


def _observe_days(options: CallbackOptions):
    return [Observation(v, {"class": c, "placed_3_or_more": ok}) for c, ok, v in _gauges["days"]]


meter.create_observable_gauge("homeroom.students.pending", [_observe_pending], unit="{student_hour}",
                              description="Student hours that kube-scheduler left Pending")
meter.create_observable_gauge("homeroom.student_days", [_observe_days], unit="{student_day}",
                              description="Student-days, by whether the student got at least three talks")


def placement_stats(placements: list[dict]) -> tuple[list, list]:
    """From placements docs: (pending per class, student-days per class split by >= 3 talks placed).

    A slot looks like 'thu-1430'; its day is the part before the dash. Students are only grouped,
    never named in the output."""
    pending: dict[str, int] = defaultdict(int)
    placed: dict[tuple[str, str, str], int] = defaultdict(int)
    for p in placements:
        cls, phase = p.get("class", ""), (p.get("status") or {}).get("phase")
        day = p.get("slot", "").split("-")[0]
        placed.setdefault((cls, p.get("student", ""), day), 0)
        if phase == "Pending":
            pending[cls] += 1
        elif phase == "Running":
            placed[(cls, p.get("student", ""), day)] += 1
    days: dict[tuple[str, bool], int] = defaultdict(int)
    for (cls, _student, _day), n in placed.items():
        days[(cls, n >= 3)] += 1
    return sorted(pending.items()), sorted((c, ok, n) for (c, ok), n in days.items())


def refresh_placement_gauges(placements: list[dict]) -> None:
    _gauges["pending"], _gauges["days"] = placement_stats(placements)


def record_request(route: str, method: str, status: int, seconds: float, simulated: bool, induced: bool) -> None:
    attrs = {"http.route": route, "http.request.method": method, "http.status_class": f"{status // 100}xx",
             "simulated": simulated, "induced": induced}
    requests_counter.add(1, attrs)
    duration_histogram.record(seconds, attrs)


@contextmanager
def readiness_span(talk_id: str, known: int):
    """Wrap a call to readiness() for one talk. Attributes are the talk and counts, never the student."""
    with tracer.start_as_current_span("homeroom.readiness", attributes={"talk.id": talk_id,
                                                                         "student.concepts_known": known}) as span:
        yield span


def start_gauge_refresher(refresh, interval: float = 30) -> threading.Thread:
    """Run refresh() now and then every interval seconds, in a daemon thread."""
    def loop():
        while True:
            try:
                refresh()
            except Exception:  # never let telemetry take the app down
                pass
            time.sleep(interval)
    thread = threading.Thread(target=loop, name="homeroom-gauges", daemon=True)
    thread.start()
    return thread


def install(app, refresh=None) -> None:
    """Middleware: request metrics, plus simulated=true on load generator spans. refresh() feeds the gauges."""
    from fastapi import Request

    if refresh is not None:
        @app.on_event("startup")
        def _start() -> None:
            start_gauge_refresher(refresh)

    @app.middleware("http")
    async def measure(request: Request, call_next):
        simulated = bool(request.headers.get("x-homeroom-simulated"))
        span = trace.get_current_span()
        if simulated:
            span.set_attribute("simulated", True)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            record_request(route, request.method, 500, time.perf_counter() - start, simulated, False)
            raise
        route = getattr(request.scope.get("route"), "path", "unmatched")
        induced = response.headers.get("x-homeroom-induced") == "true"
        record_request(route, request.method, response.status_code, time.perf_counter() - start, simulated, induced)
        return response
