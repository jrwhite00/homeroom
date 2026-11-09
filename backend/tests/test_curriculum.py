import http.client
import json

import pytest
from fastapi.testclient import TestClient

from app import load_curriculum
from app.curriculum import (DEFAULT_DIR, CurriculumError, missing_concepts, parse_lines, read_concepts,
                            read_courses)
from app.es import ESError
from app.main import app
from app.search import get_es
from app.translate import translate


# --- the real curriculum ---------------------------------------------------------------------

def test_every_course_teaches_only_concepts_that_have_a_file():
    """Fails if any course in curriculum/courses teaches a slug with no curriculum/concepts/<slug>.yaml."""
    missing = missing_concepts(read_courses(DEFAULT_DIR), read_concepts(DEFAULT_DIR))
    assert missing == [], "\n".join(f"{course} teaches '{slug}', but there is no concepts/{slug}.yaml"
                                    for course, slug in missing)


# --- reading files -----------------------------------------------------------------------------

def write(root, kind, slug, text):
    (root / kind).mkdir(parents=True, exist_ok=True)
    (root / kind / f"{slug}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def curriculum(tmp_path):
    write(tmp_path, "concepts", "scaling", "concept: Scaling\n"
          "definition: How a website keeps working when lots of people use it at once: more copies.\n"
          "technical words: autoscaling, load balancing, high availability\n")
    write(tmp_path, "concepts", "containers", "concept: Containers\n"
          "definition: A way to package a program so it runs the same anywhere.\n"
          "technical words: Docker, container image\n")
    write(tmp_path, "courses", "web-1", "course: Web Development 1\nstate: Utah\n"
          "standard: Utah CTE Web Development 1 strands\nteaches: scaling, containers\n"
          "added by: Ms. Example (fictional)\n")
    return tmp_path


def test_reads_keys_with_spaces_and_comma_lists(curriculum):
    concepts = {c["slug"]: c for c in read_concepts(curriculum)}
    assert concepts["scaling"]["technical_words"] == ["autoscaling", "load balancing", "high availability"]
    assert concepts["scaling"]["definition"].endswith("at once: more copies.")  # colon inside a value
    course = read_courses(curriculum)[0]
    assert course["teaches"] == ["scaling", "containers"] and course["added_by"] == "Ms. Example (fictional)"


def test_course_level_is_optional_and_read_as_a_slug(curriculum):
    assert read_courses(curriculum)[0]["level"] == ""  # web-1 doesn't say: it is for everyone
    write(curriculum, "courses", "prog-ug", "course: Programming 1\nstate: Utah\nteaches: scaling\n"
          "level: Undergraduate\n")
    assert {c["slug"]: c["level"] for c in read_courses(curriculum)}["prog-ug"] == "undergraduate"


def test_unknown_course_level_names_the_file_and_the_choices(curriculum):
    write(curriculum, "courses", "prog-x", "course: Programming 1\nteaches: scaling\nlevel: college\n")
    with pytest.raises(CurriculumError, match="prog-x.yaml.*high-school, undergraduate, graduate, technical-program"):
        read_courses(curriculum)


def test_missing_concept_is_reported(curriculum):
    write(curriculum, "courses", "cs-principles", "course: CSP\nteaches: scaling, kubernetes-basics\n")
    assert missing_concepts(read_courses(curriculum), read_concepts(curriculum)) == [
        ("cs-principles", "kubernetes-basics")]


def test_line_without_colon_names_file_and_line():
    with pytest.raises(CurriculumError, match="x.yaml line 2"):
        parse_lines("concept: A\njust words\n", "x.yaml")


def test_blank_lines_and_comments_are_ignored():
    assert parse_lines("# note\n\nconcept: A\n") == {"concept": "A"}


def test_empty_folder_reads_nothing(tmp_path):
    assert read_concepts(tmp_path) == [] and read_courses(tmp_path) == []


# --- loading -----------------------------------------------------------------------------------

class FakeES:
    def __init__(self):
        self.indexes: dict[str, dict] = {}

    def request(self, method, path, body=None, ndjson=False, ok404=False, timeout=None):
        index = path.strip("/").split("/")[0]
        if method == "HEAD":
            return {} if index in self.indexes else None
        if method == "PUT":
            self.indexes[index] = {"mapping": body, "docs": {}}
        elif path == "/_bulk":
            lines = body.splitlines()
            for action, source in zip(lines[::2], lines[1::2]):
                meta = json.loads(action)["index"]
                self.indexes[meta["_index"]]["docs"][meta["_id"]] = json.loads(source)
            return {"items": [{"index": {}} for _ in lines[::2]]}
        elif path.endswith("/_count"):
            return {"count": len(self.indexes[index]["docs"])}
        return {}


def test_concepts_mapping_puts_semantic_text_on_definition():
    props = load_curriculum.concepts_mapping()["mappings"]["properties"]
    assert props["definition"]["copy_to"] == "semantic"
    assert props["semantic"]["type"] == "semantic_text"


def test_load_indexes_by_slug_and_counts_match(curriculum):
    es = FakeES()
    concepts, courses = read_concepts(curriculum), read_courses(curriculum)
    load_curriculum.load_index(es, "concepts", load_curriculum.concepts_mapping(), concepts, recreate=False)
    load_curriculum.load_index(es, "courses", load_curriculum.courses_mapping(), courses, recreate=False)
    assert set(es.indexes["concepts"]["docs"]) == {"scaling", "containers"}
    assert load_curriculum.counts_match(es, {"concepts": 2, "courses": 1})
    assert not load_curriculum.counts_match(es, {"concepts": 3, "courses": 1})


def test_loader_refuses_missing_concepts(curriculum, monkeypatch, capsys):
    write(curriculum, "courses", "bad", "course: Bad\nteaches: nothing-here\n")
    monkeypatch.setattr(load_curriculum.ES, "from_env", lambda: pytest.fail("must not touch Elasticsearch"))
    assert load_curriculum.main(["--dir", str(curriculum)]) == 1
    assert "teaches 'nothing-here'" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [
    http.client.RemoteDisconnected("closed"),
    http.client.IncompleteRead(b"par"),
    ConnectionResetError("reset"),
])
def test_request_wraps_connection_failures(monkeypatch, failure):
    from app import es as es_module

    def boom(*args, **kwargs):
        raise failure
    monkeypatch.setattr(es_module.urllib.request, "urlopen", boom)
    with pytest.raises(ESError):
        es_module.ES("http://es.invalid").request("GET", "/concepts/_count")


def test_request_wraps_non_json_response(monkeypatch):
    from app import es as es_module

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>proxy error</html>"
    monkeypatch.setattr(es_module.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(ESError, match="not JSON"):
        es_module.ES("http://es.invalid").request("GET", "/concepts/_count")


# --- translate ---------------------------------------------------------------------------------

def concept_hit(score, name, words):
    return {"_score": score, "_source": {"slug": name.lower(), "concept": name, "technical_words": words}}


class StubES:
    def __init__(self, concept_hits=None, session_hits=None, concepts_error=None):
        self.concept_hits = concept_hits or []
        self.session_hits = session_hits or []
        self.concepts_error = concepts_error
        self.calls = []

    def search(self, index, body):
        self.calls.append((index, body))
        if index == "concepts":
            if self.concepts_error:
                raise self.concepts_error
            return {"hits": {"hits": self.concept_hits}}
        return {"hits": {"hits": self.session_hits}}


def test_translate_uses_closest_concept_words():
    es = StubES([concept_hit(10.0, "Scaling", ["autoscaling", "load balancing"]),
                 concept_hit(4.0, "Containers", ["Docker"])])
    assert translate(es, "how do big websites stay up") == {
        "text": "autoscaling, load balancing", "concepts": ["Scaling"]}
    index, body = es.calls[0]
    assert index == "concepts" and body["query"] == {"match": {"semantic": {"query": "how do big websites stay up"}}}


def test_translate_adds_close_runners_up_without_repeating_words():
    es = StubES([concept_hit(10.0, "Scaling", ["autoscaling", "load balancing"]),
                 concept_hit(9.0, "Networking", ["DNS", "load balancing"])])
    assert translate(es, "q")["text"] == "autoscaling, load balancing, DNS"


def test_translate_returns_none_without_matches():
    assert translate(StubES([]), "q") is None


# --- /search?translate=true --------------------------------------------------------------------

@pytest.fixture
def client_with(request):
    def make(es):
        app.dependency_overrides[get_es] = lambda: es
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()


def test_search_translate_searches_technical_words(client_with):
    es = StubES([concept_hit(10.0, "Scaling", ["autoscaling", "load balancing", "high availability"])],
                [{"_score": 0.03, "_source": {"title": "Autoscaling at Scale with KEDA"}}])
    body = client_with(es).get("/search", params={"q": "how do big websites stay up", "translate": "true"}).json()
    assert body["translated"] == {"text": "autoscaling, load balancing, high availability", "concepts": ["Scaling"]}
    sessions_query = es.calls[1][1]
    bm25 = sessions_query["retriever"]["rrf"]["retrievers"][0]["standard"]["query"]["bool"]["must"][0]
    assert bm25["multi_match"]["query"] == "autoscaling, load balancing, high availability"
    assert body["results"][0]["title"] == "Autoscaling at Scale with KEDA"


def test_search_without_translate_does_not_touch_concepts(client_with):
    es = StubES()
    body = client_with(es).get("/search", params={"q": "autoscaling"}).json()
    assert [index for index, _ in es.calls] == ["sessions"] and body["translated"] is None


def test_search_translate_falls_back_to_question_if_concepts_unavailable(client_with):
    es = StubES(concepts_error=ESError("no such index [concepts]"))
    r = client_with(es).get("/search", params={"q": "how do big websites stay up", "translate": "true"})
    assert r.status_code == 200 and r.json()["translated"] is None
    bm25 = es.calls[-1][1]["retriever"]["rrf"]["retrievers"][0]["standard"]["query"]["bool"]["must"][0]
    assert bm25["multi_match"]["query"] == "how do big websites stay up"
