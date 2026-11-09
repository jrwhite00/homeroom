"""The teacher's view: homeroom_missing_concept, counted by /week with only concept, class and simulated labels.

The identifier tests fail if a student identifier (a pseudonym, or a label key that names a student) ever
appears on the metric, including when a student's name is typed where the class code goes.
"""
import random

import pytest
from fastapi.testclient import TestClient

from app import loadgen, telemetry, week as week_module
from app.main import app
from app.search import get_es
from tests.test_week import ALL, ONE_STEP_TALK, StubES, talk

PSEUDONYMS = {p["name"] for p in loadgen.PERSONAS}
STUDENT_KEYS = {"student", "student_id", "student.id", "pseudonym", "name", "user", "user_id", "email"}


class FakeCounter:
    def __init__(self):
        self.calls = []

    def add(self, amount, attributes):
        self.calls.append((amount, dict(attributes)))


@pytest.fixture
def counter(monkeypatch):
    fake = FakeCounter()
    monkeypatch.setattr(telemetry, "missing_concept_counter", fake)
    monkeypatch.delenv("HOMEROOM_CLASS_CODES", raising=False)
    return fake


def get_week(stub, headers=None, **params):
    app.dependency_overrides[get_es] = lambda: stub
    try:
        return TestClient(app).get("/week", params=params, headers=headers or {})
    finally:
        app.dependency_overrides.clear()


def assert_no_student_identifier(calls):
    for _, attrs in calls:
        assert set(attrs) <= set(telemetry.MISSING_CONCEPT_LABELS), f"unexpected label: {set(attrs)}"
        assert not set(attrs) & STUDENT_KEYS, f"student identifier as a label key: {set(attrs) & STUDENT_KEYS}"
        for value in attrs.values():
            assert str(value).lower() not in PSEUDONYMS, f"pseudonym as a label value: {value}"


def test_one_count_per_student_and_missing_concept(counter):
    # Intro CS knows programming-basics. Three talks are one step away: two need containers, one needs
    # networking-basics. The student is counted once for each of those two concepts.
    more_containers = talk("8", "More containers", ["programming-basics", "containers"])
    get_week(StubES(ALL + [more_containers]), state="Utah", courses="fx-intro-cs", class_code="demo")
    assert sorted((a, c["concept"]) for a, c in counter.calls) == [(1, "containers"), (1, "networking-basics")]
    assert {c["class_code"] for _, c in counter.calls} == {"demo"}
    assert_no_student_identifier(counter.calls)


def test_concept_label_is_the_slug(counter):
    get_week(StubES([ONE_STEP_TALK], kiosks=[]), state="Utah", courses="fx-intro-cs", class_code="demo")
    assert counter.calls == [(1, {"concept": "containers", "class_code": "demo", "simulated": False})]


def test_load_generator_requests_are_labelled_simulated(counter):
    get_week(StubES([ONE_STEP_TALK], kiosks=[]), headers={"X-Homeroom-Simulated": "loadgen"},
             state="Utah", courses="fx-intro-cs", class_code="demo")
    assert counter.calls[0][1]["simulated"] is True


def test_nothing_counted_when_no_talk_is_one_step_away(counter):
    get_week(StubES([talk("9", "Untagged")]), state="Utah", courses="fx-intro-cs", class_code="demo")
    assert counter.calls == []


@pytest.mark.parametrize("typed, label", [("", "none"), ("demo", "demo"), (" DEMO ", "demo"),
                                          ("maya", "other"), ("not a class!", "other")])
def test_only_known_class_codes_become_labels(counter, typed, label):
    get_week(StubES([ONE_STEP_TALK], kiosks=[]), state="Utah", courses="fx-intro-cs", class_code=typed)
    assert counter.calls[0][1]["class_code"] == label
    assert_no_student_identifier(counter.calls)


def test_class_codes_come_from_the_environment(counter, monkeypatch):
    monkeypatch.setenv("HOMEROOM_CLASS_CODES", "demo, Period-3")
    get_week(StubES([ONE_STEP_TALK], kiosks=[]), state="Utah", courses="fx-intro-cs", class_code="period-3")
    assert counter.calls[0][1]["class_code"] == "period-3"


def test_every_pseudonym_typed_as_a_class_code_is_kept_off_the_metric(counter):
    for name in PSEUDONYMS:
        get_week(StubES([ONE_STEP_TALK], kiosks=[]), state="Utah", courses="fx-intro-cs", class_code=name)
    assert len(counter.calls) == len(PSEUDONYMS)
    assert_no_student_identifier(counter.calls)


def test_the_guard_catches_an_identifier():
    # The check above must fail if a student identifier ever gets on the metric.
    with pytest.raises(AssertionError):
        assert_no_student_identifier([(1, {"concept": "containers", "class_code": "demo", "student": "x"})])
    with pytest.raises(AssertionError):
        assert_no_student_identifier([(1, {"concept": "containers", "class_code": "maya", "simulated": True})])


def test_load_generator_week_requests_carry_the_class_code_and_no_pseudonym():
    rng = random.Random(7)
    paths = [path for kind, path in (loadgen.pick_request(rng) for _ in range(500)) if kind == "week"]
    assert paths and all("class_code=demo" in p for p in paths)
    assert not any(name in p for p in paths for name in PSEUDONYMS)
    assert week_module.label_for_class(loadgen.CLASS_CODE) == "demo"
