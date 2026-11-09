"""The jargon decoder shows a concept's definition exactly as the teacher wrote it, and nothing else."""
from fastapi.testclient import TestClient

from app.curriculum import DEFAULT_DIR, read_concepts
from app.decoder import decode
from app.main import app
from app.search import get_es

TABLE = [
    {"slug": "containers", "concept": "Containers",
     "definition": "A way to package a program with everything it needs so it runs the same on any computer",
     "technical_words": ["Docker", "container image", "registry"]},
    {"slug": "data-and-databases", "concept": "Data and Databases",
     "definition": "Storing information so programs can save it and look it up",
     "technical_words": ["query", "table", "image"]},
]


def decoded(segments):
    return [(s["text"], [c["slug"] for c in s["concepts"]]) for s in segments if "concepts" in s]


def test_technical_words_get_the_concepts_definition():
    segs = decode("Push the Docker image to a registry.", TABLE)
    assert decoded(segs) == [("Docker", ["containers"]), ("image", ["data-and-databases"]), ("registry", ["containers"])]
    assert "".join(s["text"] for s in segs) == "Push the Docker image to a registry."  # nothing added or lost
    assert segs[1]["concepts"][0]["definition"] == TABLE[0]["definition"]


def test_case_insensitive_whole_words_with_plurals():
    segs = decode("Two QUERIES hit stable tables; no docker-compose tricks.", TABLE)
    assert decoded(segs) == [("QUERIES", ["data-and-databases"]), ("tables", ["data-and-databases"]),
                             ("docker", ["containers"])]


def test_vowel_y_terms_allow_regular_s_plural():
    table = TABLE + [{"slug": "keys", "concept": "Keys", "definition": "Small metal objects",
                      "technical_words": ["key"]}]
    assert decoded(decode("Copy the keys.", table)) == [("keys", ["keys"])]


def test_longer_technical_words_win_over_overlapping_shorter_ones():
    assert decoded(decode("Build a container  image.", TABLE)) == [("container  image", ["containers"])]


def test_word_in_two_concepts_shows_both_definitions():
    table = TABLE + [{"slug": "x", "concept": "X", "definition": "An x thing", "technical_words": ["query"]}]
    assert decoded(decode("one query", table)) == [("query", ["data-and-databases", "x"])]


def test_definitions_only_come_from_the_table():
    assert decoded(decode("Kubernetes autoscaling with Helm", TABLE)) == []  # not in the table: left alone
    no_definition = [{"slug": "y", "concept": "Y", "definition": "", "technical_words": ["Helm"]}]
    assert decoded(decode("Helm", no_definition)) == []  # a word without a teacher's definition is never decoded


def test_decoded_term_shows_annas_wording_exactly():
    """Against the real concept files (read only): the definition is the file's definition line, verbatim."""
    concepts = read_concepts(DEFAULT_DIR)
    for concept in concepts:
        path = DEFAULT_DIR / "concepts" / f"{concept['slug']}.yaml"
        line = next(l for l in path.read_text(encoding="utf-8").splitlines() if l.lower().startswith("definition:"))
        for word in concept["technical_words"]:
            terms = [s for s in decode(f"About {word} today.", concepts) if "concepts" in s]
            assert terms, f"{word} was not decoded"
            shown = {c["slug"]: c["definition"] for c in terms[0]["concepts"]}
            assert shown[concept["slug"]] == line.split(":", 1)[1].strip().strip('"').strip("'").strip()


class StubES:
    def __init__(self, sessions):
        self.sessions = sessions

    def search(self, index, body):
        if index == "concepts":
            return {"hits": {"hits": [{"_source": c} for c in TABLE]}}
        want = body["query"]["term"]["id"]
        return {"hits": {"hits": [{"_source": s} for s in self.sessions if s["id"] == want]}}


def get(path, sessions):
    app.dependency_overrides[get_es] = lambda: StubES(sessions)
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.clear()


def test_talk_endpoint_returns_the_decoded_abstract():
    body = get("/talks/42", [{"id": "42", "title": "Ship it", "abstract": "Use Docker."}]).json()
    assert body["title"] == "Ship it"
    assert body["abstract"][1] == {"text": "Docker", "concepts": [
        {"slug": "containers", "concept": "Containers", "definition": TABLE[0]["definition"]}]}


def test_unknown_talk_is_404():
    assert get("/talks/nope", []).status_code == 404


def post_decode(body, es=None):
    app.dependency_overrides[get_es] = lambda: es or StubES([])
    try:
        return TestClient(app).post("/decode", json=body)
    finally:
        app.dependency_overrides.clear()


def test_decode_endpoint_returns_one_segment_list_per_text_in_order():
    texts = ["Pod name", "1 node(s) had untolerated taint(s) for the Docker registry."]
    body = post_decode({"texts": texts}).json()
    assert ["".join(s["text"] for s in segs) for segs in body["segments"]] == texts  # word for word
    assert body["segments"][0] == [{"text": "Pod name"}]  # no teacher definition: left alone
    assert decoded(body["segments"][1]) == [("Docker", ["containers"]), ("registry", ["containers"])]


def test_decode_endpoint_only_takes_short_page_text():
    assert post_decode({"texts": ["x" * 1001]}).status_code == 422
    assert post_decode({"texts": ["x"] * 101}).status_code == 422


def test_decode_endpoint_says_plainly_when_the_table_is_unavailable():
    from app.es import ESError

    class DownES:
        def search(self, index, body):
            raise ESError("connection refused to http://secret-host:9200")

    r = post_decode({"texts": ["Pod"]}, DownES())
    assert r.status_code == 502 and "secret-host" not in r.text
