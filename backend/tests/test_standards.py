"""Standards parser and loader tests.

Every standard below is made up. It copies the layout of the extracted PDFs (codes, headings,
page noise, extraction quirks) but none of their wording: no standards text is kept in this repo.
"""
import hashlib
import json

import pytest

from app import load_standards
from app.load_standards import LoadError, Source, fetch, parse_all
from app.standards import parse_csta, parse_source, parse_utah_612, parse_utah_cte

CSTA_SAMPLE = "\n".join([
    "ALG",
    "MS-ALG-PS-01: Plan a made-up routine for sorting a sock drawer",
    "by color.",
    "Boundary Statement: Invented example text for tests only.",
    "© 2026 Example footer",
    "It continues on a second line.",
    "Practice(s): 3, 4",
    "HS-NET-CY-02: Describe an imaginary network of paper cups.",
    "Boundary Statement: Also invented.",
    "Practice(s): 7",
    # Summary tables repeat IDs without a boundary statement; they are not new standards.
    "MS-ALG-PS-01: Plan a made-up routine for sorting a sock drawer",
    "Practice(s): 3",
])

UTAH_612_SAMPLE = "\n".join([
    "Organization of Standards",
    "Standard 6.NI.1 Practical example wording from the introduction. (Practice 1)",
    "Grade 6",
    "Standard 6.NI.1 Explain an invented rule about protective pretend passwords. (Practice 4: Example)",
    "This is the made-up explanation that follows the practice list.",
    "12 | Page",
    "8.AP.2 Create an imaginary procedure for feeding a robot cat. (Practice 5)",
    "Explanation for the robot cat.",
    "Standard 11/12.IC.3 Discuss a fictional ethics question. (Practice 7)",
    "Last explanation.",
    "GLOSSARY",
    "Algorithm: not part of any standard.",
])

CTE_SAMPLE = "\n".join([
    "SAMPLE COURSE",
    "SSTRAND 1TRAND 1",
    "Students will learn invented things.",
    "StStandarandard 1d 1",
    "Write a pretend program.",
    "• Use made-up variables",
    "  and constants",
    "• Print imaginary output.",
    "PPerferformance Skillsormance Skills",
    "Not part of the standard.",
    "Standard 2",
    "• Only bullets here",
    "STRAND 4",
    "Standard 3",
    "Loop over invented things.",
    "• Use a pretend loop.",
    # The Computer Programming 1 PDF garbles this heading differently (Gitar finding on #2).
    "PP f ererformance Skillsormance Skills",
    "Write a performance-skill sentence that must not leak into 4.3.",
    "Industry Test Points by Strand",
    "Standard 9",
])


def by_code(docs, code):
    return next(d for d in docs if d["code"] == code)


def test_csta_statement_boundary_and_noise():
    docs = parse_csta(CSTA_SAMPLE)
    assert [d["code"] for d in docs] == ["MS-ALG-PS-01", "HS-NET-CY-02"]
    d = docs[0]
    assert d["grade_band"] == "MS" and d["strand"] == "ALG" and d["framework"] == "CSTA 2026"
    assert d["statement"] == "Plan a made-up routine for sorting a sock drawer by color."
    assert d["description"].endswith("Invented example text for tests only. It continues on a second line.")
    assert "Practice(s)" not in d["description"] and "©" not in d["description"]


def test_utah_612_prefixes_intro_example_and_glossary():
    docs = parse_utah_612(UTAH_612_SAMPLE)
    assert [d["code"] for d in docs] == ["6.NI.1", "8.AP.2", "11/12.IC.3"]
    assert "protective" in by_code(docs, "6.NI.1")["statement"]  # the real one beats the intro example
    assert by_code(docs, "8.AP.2")["statement"] == "Create an imaginary procedure for feeding a robot cat."
    assert by_code(docs, "6.NI.1")["description"].endswith("follows the practice list.")
    assert "Page" not in by_code(docs, "6.NI.1")["description"]
    assert "GLOSSARY" not in by_code(docs, "11/12.IC.3")["description"]
    assert by_code(docs, "11/12.IC.3")["id"] == "utah-6-12-cs-standards:11-12.IC.3"


def test_cte_doubled_headings_bullets_and_bullet_only_standard():
    docs = parse_utah_cte(CTE_SAMPLE, "sample", "Sample Course")
    assert [d["code"] for d in docs] == ["1.1", "1.2", "4.3"]
    assert docs[0]["description"] == "Write a pretend program. Use made-up variables and constants. Print imaginary output."
    assert docs[1]["statement"] == "Only bullets here."
    assert docs[0]["course"] == "Sample Course" and docs[0]["grade_band"] == "9-12"


def test_cte_garbled_performance_heading_closes_the_standard():
    d = by_code(parse_utah_cte(CTE_SAMPLE, "sample", "Sample Course"), "4.3")
    assert d["description"] == "Loop over invented things. Use a pretend loop."
    assert "erformance" not in d["description"]


def test_parse_source_dispatches_and_rejects_unknown():
    assert parse_source("utah-cte-web-development-1", CTE_SAMPLE)[0]["course"] == "Web Development 1"
    with pytest.raises(ValueError):
        parse_source("not-a-source", "")


# --- loader -----------------------------------------------------------------------------------

PDF = b"%PDF-1.7 pretend bytes"


def source(name="utah-cte-web-development-1", url="https://example.test/x.pdf", expected=3, data=PDF):
    return Source(name, url, hashlib.sha256(data).hexdigest(), expected)


def test_fetch_downloads_once_and_checks_hash(tmp_path):
    calls = []

    def downloader(url, dest):
        calls.append(url)
        dest.write_bytes(PDF)

    path = fetch(source(), tmp_path, downloader)
    assert path.read_bytes() == PDF and calls == ["https://example.test/x.pdf"]
    fetch(source(), tmp_path, downloader)
    assert len(calls) == 1  # cached


def test_fetch_rejects_a_changed_pdf(tmp_path):
    (tmp_path / "utah-cte-web-development-1.pdf").write_bytes(b"a different document")
    with pytest.raises(LoadError, match="expected"):
        fetch(source(), tmp_path)


def test_missing_csta_explains_how_to_get_it(tmp_path):
    with pytest.raises(LoadError) as e:
        fetch(source(name="csta-2026-pk12-cs-standards", url=None), tmp_path)
    assert load_standards.CSTA_PAGE in str(e.value) and "browser" in str(e.value)


def test_unexpected_count_stops_before_indexing(tmp_path, monkeypatch):
    monkeypatch.setattr(load_standards, "SOURCES", [source(expected=4)])
    (tmp_path / "utah-cte-web-development-1.pdf").write_bytes(PDF)
    with pytest.raises(LoadError, match="parsed 3, expected 4"):
        parse_all(tmp_path, extract=lambda path: CTE_SAMPLE)


def test_sources_cover_every_parser_and_pin_hashes():
    names = [s.name for s in load_standards.SOURCES]
    assert sorted(names) == sorted(["csta-2026-pk12-cs-standards", "utah-6-12-cs-standards",
                                    "utah-cte-computer-programming-1", "utah-cte-computer-programming-2",
                                    "utah-cte-computer-science-principles",
                                    "utah-cte-exploring-computer-science", "utah-cte-web-development-1"])
    assert all(len(s.sha256) == 64 for s in load_standards.SOURCES)
    assert sum(s.expected for s in load_standards.SOURCES) == 480


def test_cache_dir_is_git_ignored():
    gitignore = (load_standards.CACHE_DIR.parents[2] / ".gitignore").read_text()
    assert "data/standards/cache/" in gitignore


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
            for action, doc in zip(lines[::2], lines[1::2]):
                self.docs[json.loads(action)["index"]["_id"]] = json.loads(doc)
            return {"items": [{"index": {}} for _ in lines[::2]]}
        if path.endswith("/_count"):
            term = (body or {}).get("query", {}).get("term", {}).get("source")
            return {"count": sum(1 for d in self.docs.values() if term in (None, d["source"]))}
        return {}


@pytest.fixture
def parsed():
    return {"utah-cte-web-development-1": parse_source("utah-cte-web-development-1", CTE_SAMPLE),
            "csta-2026-pk12-cs-standards": parse_csta(CSTA_SAMPLE)}


def test_mapping_puts_semantic_text_on_description():
    props = load_standards.mapping()["mappings"]["properties"]
    assert props["description"]["copy_to"] == "semantic"
    assert props["semantic"] == {"type": "semantic_text", "inference_id": ".elser-2-elasticsearch"}


def test_load_then_counts_match(parsed):
    es = FakeES()
    load_standards.load(es, parsed, recreate=False, inference_id=".elser-2-elasticsearch")
    assert es.created is not None and len(es.docs) == 5
    assert load_standards.compare_counts(es, parsed) is True


def test_counts_mismatch_is_reported(parsed, capsys):
    es = FakeES()
    load_standards.load(es, parsed, recreate=False, inference_id=".elser-2-elasticsearch")
    es.docs.pop("utah-cte-web-development-1:1.2")
    assert load_standards.compare_counts(es, parsed) is False
    assert "MISMATCH" in capsys.readouterr().out
