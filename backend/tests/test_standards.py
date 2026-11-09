import json

import pytest

from app import load_standards
from app.standards import DEFAULT_DIR, parse_all, parse_utah_cte

# Counted independently from the text files: CSTA "Boundary Statement:" lines, distinct Utah 6-12
# codes, and Utah CTE "Standard N" headings before the test-points appendix.
EXPECTED = {
    "csta-2026-pk12-cs-standards": 331,
    "utah-6-12-cs-standards": 51,
    "utah-cte-computer-programming-1": 17,
    "utah-cte-computer-programming-2": 17,
    "utah-cte-computer-science-principles": 20,
    "utah-cte-exploring-computer-science": 26,
    "utah-cte-web-development-1": 18,
}


@pytest.fixture(scope="module")
def parsed():
    return parse_all(DEFAULT_DIR)


def by_code(docs, code):
    return next(d for d in docs if d["code"] == code)


def test_counts_match_files(parsed):
    assert {source: len(docs) for source, docs in parsed.items()} == EXPECTED


def test_ids_unique_and_statements_present(parsed):
    docs = [d for source_docs in parsed.values() for d in source_docs]
    assert len({d["id"] for d in docs}) == len(docs)
    assert all(d["statement"] and d["description"].startswith(d["statement"]) for d in docs)
    assert all("/" not in d["id"] for d in docs)


def test_csta_statement_and_boundary(parsed):
    d = by_code(parsed["csta-2026-pk12-cs-standards"], "MS-ALG-PS-01")
    assert d["grade_band"] == "MS" and d["strand"] == "ALG"
    assert d["statement"] == ("Design an algorithm that includes variables of multiple data types "
                              "to solve a problem or express an idea.")
    assert "Students should design and explain an algorithm" in d["description"]
    assert "Practice(s)" not in d["description"]


def test_utah_612_without_standard_prefix_and_intro_example(parsed):
    docs = parsed["utah-6-12-cs-standards"]
    assert by_code(docs, "8.AP.2")["statement"].startswith("Create procedures with or without parameters")
    # The intro uses 6.NI.1 as an example ("practical measures"); the real standard says "protective".
    assert "protective measures" in by_code(docs, "6.NI.1")["statement"]
    assert "(Practice" not in by_code(docs, "9/10.CS.1")["statement"]


def test_utah_612_last_standard_stops_before_glossary(parsed):
    assert "GLOSSARY" not in by_code(parsed["utah-6-12-cs-standards"], "11/12.IC.3")["description"]


def test_cte_doubled_headings_and_bullets(parsed):
    d = by_code(parsed["utah-cte-computer-programming-1"], "2.1")
    assert d["course"] == "Computer Programming 1" and d["strand"] == "2"
    assert d["statement"] == "Demonstrate the ability to use good programming style."
    assert "Demonstrate proper use of white space" in d["description"]


def test_cte_parser_on_small_sample():
    text = "\n".join([
        "SSTRAND 1TRAND 1",
        "Students will learn things.",
        "StStandarandard 1d 1",
        "Write a program.",
        "• Use variables",
        "  and constants",
        "• Print output.",
        "PPerferformance Skillsormance Skills",
        "Not part of the standard.",
        "Standard 2",
        "• Only bullets here",
        "Industry Test Points by Strand",
        "Standard 9",
    ])
    docs = parse_utah_cte(text, "sample", "Sample Course")
    assert [d["code"] for d in docs] == ["1.1", "1.2"]
    assert docs[0]["description"] == "Write a program. Use variables and constants. Print output."
    assert docs[1]["statement"] == "Only bullets here."


class FakeES:
    def __init__(self):
        self.docs = {}
        self.created = None

    def req(self, method, path, body=None, ndjson=False, ok404=False, timeout=0):
        if method == "HEAD":
            return None if self.created is None else {}
        if method == "PUT":
            self.created = body
            return {}
        if path == "/_bulk":
            lines = body.splitlines()
            for action, source in zip(lines[::2], lines[1::2]):
                self.docs[json.loads(action)["index"]["_id"]] = json.loads(source)
            return {"items": [{"index": {}} for _ in lines[::2]]}
        if path.endswith("/_count"):
            term = (body or {}).get("query", {}).get("term", {}).get("source")
            return {"count": sum(1 for d in self.docs.values() if term in (None, d["source"]))}
        return {}


def test_mapping_puts_semantic_text_on_description():
    props = load_standards.mapping()["mappings"]["properties"]
    assert props["description"]["copy_to"] == "semantic"
    assert props["semantic"] == {"type": "semantic_text", "inference_id": ".elser-2-elasticsearch"}


def test_load_then_counts_match(parsed, capsys):
    es = FakeES()
    load_standards.load(es, parsed, recreate=False, inference_id=".elser-2-elasticsearch")
    assert es.created is not None
    assert len(es.docs) == sum(EXPECTED.values())
    assert load_standards.compare_counts(es, parsed) is True


def test_counts_mismatch_is_reported(parsed, capsys):
    es = FakeES()
    load_standards.load(es, parsed, recreate=False, inference_id=".elser-2-elasticsearch")
    es.docs.pop("utah-cte-web-development-1:5.2")
    assert load_standards.compare_counts(es, parsed) is False
    assert "MISMATCH" in capsys.readouterr().out
