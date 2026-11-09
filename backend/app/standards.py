"""Parse the curriculum standards text files in data/standards/ into one document per standard.

The text was extracted from the published PDFs, so each parser skips page headers and footers
and works around extraction quirks (for example the Utah CTE headings come out doubled,
"StStandarandard 1d 1" for "Standard 1").

Every document has the same fields:
  id           unique across all sources, safe to use as an Elasticsearch _id
  source       the file stem, for example "utah-cte-web-development-1"
  framework    "CSTA 2026", "Utah 6-12 CS" or "Utah CTE"
  course       the Utah CTE course name, otherwise None
  code         the standard's code as published, for example "MS-ALG-PS-01", "9/10.AP.1", "2.3"
  grade_band   for example "MS", "9/10" or "9-12"
  strand       the strand or concept area code, for example "ALG", "AP" or "2"
  statement    the standard as written
  description  the statement followed by its elaboration (boundary statement, explanation or bullets)
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "standards"

CSTA_FILE = "csta-2026-pk12-cs-standards"
UTAH_612_FILE = "utah-6-12-cs-standards"
CTE_COURSES = {
    "utah-cte-computer-programming-1": "Computer Programming 1",
    "utah-cte-computer-programming-2": "Computer Programming 2",
    "utah-cte-computer-science-principles": "Computer Science Principles",
    "utah-cte-exploring-computer-science": "Exploring Computer Science",
    "utah-cte-web-development-1": "Web Development 1",
}


def _join(lines: list[str]) -> str:
    text = " ".join(line.strip() for line in lines if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def _doc(source: str, framework: str, code: str, grade_band: str, strand: str,
         statement: str, elaboration: str, course: str | None = None) -> dict:
    return {
        "id": f"{source}:{code.replace('/', '-')}",
        "source": source,
        "framework": framework,
        "course": course,
        "code": code,
        "grade_band": grade_band,
        "strand": strand,
        "statement": statement,
        "description": f"{statement} {elaboration}".strip(),
    }


# --- CSTA 2026 -----------------------------------------------------------------------------
# Each standard starts "MS-ALG-PS-01: statement", then "Boundary Statement: ...", then
# "Practice(s): ...". The same IDs appear again later in summary tables without the
# "Boundary Statement", so only blocks that have one count.

CSTA_ID = re.compile(r"^((EK|E[1-5]|MS|HS|S1|S2)-([A-Z]{3})-[A-Z]{2}-\d{2}):\s*(.*)$")
CSTA_NOISE = re.compile(r"^(©|2026 CSTA PK–12 Computer Science Standards|[A-Z]{3}$)")


def parse_csta(text: str, source: str = CSTA_FILE) -> list[dict]:
    docs: dict[str, dict] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = CSTA_ID.match(lines[i].strip())
        if not m:
            i += 1
            continue
        code, band, area, first = m.groups()
        statement, boundary, j = [first], [], i + 1
        while j < len(lines) and not lines[j].startswith("Boundary Statement:"):
            if CSTA_ID.match(lines[j].strip()) or lines[j].startswith("Practice(s):"):
                break
            if not CSTA_NOISE.match(lines[j].strip()):
                statement.append(lines[j])
            j += 1
        if j < len(lines) and lines[j].startswith("Boundary Statement:"):
            boundary.append(lines[j][len("Boundary Statement:"):])
            j += 1
            while j < len(lines) and not lines[j].startswith("Practice(s):") and not CSTA_ID.match(lines[j].strip()):
                if not CSTA_NOISE.match(lines[j].strip()):
                    boundary.append(lines[j])
                j += 1
            if code not in docs:
                docs[code] = _doc(source, "CSTA 2026", code, band, area, _join(statement), _join(boundary))
        i = j
    return list(docs.values())


# --- Utah 6-12 CS --------------------------------------------------------------------------
# "Standard 6.NI.1 statement ... (Practice 4: ...)" followed by an explanation paragraph.
# 8.AP.2 and 8.AP.3 have no "Standard" prefix. 6.NI.1 also appears once in the introduction as
# an example, with slightly different wording, so later definitions win.

UT_ID = re.compile(r"^\s*(?:Standard\s+)?((6|7|8|9/10|11/12)\.(CS|NI|DA|AP|IC)\.\d+)\.?\s+(.*)$")
UT_STOP = re.compile(r"\((CS|NI|DA|AP|IC)\):\s*$|^\s*Grade \d|^\s*GLOSSARY|^\s*Organization of Standards"
                     r"|^\s*Green text demonstrates")
UT_NOISE = re.compile(r"^\s*(\d+ \| P ?age|Utah 6-12 Computer Science Standards)")


def parse_utah_612(text: str, source: str = UTAH_612_FILE) -> list[dict]:
    docs: dict[str, dict] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = UT_ID.match(lines[i])
        if not m:
            i += 1
            continue
        code, band, strand, first = m.groups()
        block, j = [first], i + 1
        while j < len(lines) and not UT_ID.match(lines[j]) and not UT_STOP.search(lines[j]):
            if not UT_NOISE.match(lines[j]):
                block.append(lines[j])
            j += 1
        body = _join(block)
        # The statement ends with the practice list in parentheses; the explanation follows it.
        pm = re.search(r"\s*\(Practice[^)]*\)\.?", body)
        statement = body[:pm.start()].strip() if pm else body
        elaboration = body[pm.end():].strip() if pm else ""
        docs.pop(code, None)
        docs[code] = _doc(source, "Utah 6-12 CS", code, band, strand, statement, elaboration)
        i = j
    return list(docs.values())


# --- Utah CTE strands and standards --------------------------------------------------------
# "STRAND 2" then the strand description, then "Standard 1" and its statement and bullets,
# then optionally "Performance Skills". Bold headings come out doubled in some files.
# The test-points tables and vocabulary at the end are not standards.

CTE_STRAND = re.compile(r"^\s*S?STRAND\s*(\d+)(?!\d)")  # also "SSTRAND 2TRAND 2"
CTE_STANDARD = re.compile(r"^\s*(?:StStandarandard\s*(\d+)d\s*\d+|Standard\s+(\d+))\s*$")
CTE_PERF = re.compile(r"^\s*P?Perf")
CTE_END = re.compile(r"Test Points by Strand|^\s*Indus|^\s*Test Name\b")
CTE_NOISE = re.compile(r"^\s*(\d+\]\s*Page|REVISED|Updated:|CTE®|Learning that works for Utah)", re.I)


def parse_utah_cte(text: str, source: str, course: str) -> list[dict]:
    docs: list[dict] = []
    strand: str | None = None
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current and current["lines"]:
            statement_lines: list[str] = []
            bullets: list[list[str]] = []
            for l in current["lines"]:
                if l.strip().startswith("•"):
                    bullets.append([l.strip().lstrip("•")])
                elif bullets:
                    bullets[-1].append(l)  # a bullet that wrapped onto the next line
                else:
                    statement_lines.append(l)
            statement = _join(statement_lines)
            sentences = [b if b[-1] in ".!?:" else b + "." for b in (_join(b) for b in bullets) if b]
            elaboration = " ".join(sentences)
            if not statement:  # a few standards are only a bullet list (Web Development 1, 5.2)
                statement, elaboration = elaboration, ""
            code = f"{current['strand']}.{current['number']}"
            docs.append(_doc(source, "Utah CTE", code, "9-12", current["strand"], statement,
                             elaboration, course=course))
        current = None

    for line in text.splitlines():
        if CTE_END.search(line):
            break
        if line.strip().upper() == course.upper() or CTE_NOISE.match(line):
            continue
        if m := CTE_STRAND.match(line):
            close()
            strand = m.group(1)
            continue
        if m := CTE_STANDARD.match(line):
            close()
            current = {"strand": strand, "number": m.group(1) or m.group(2), "lines": []}
            continue
        if CTE_PERF.match(line):
            close()
            continue
        if current is not None:
            current["lines"].append(line)
    close()
    return docs


def parse_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    stem = path.stem
    if stem == CSTA_FILE:
        return parse_csta(text)
    if stem == UTAH_612_FILE:
        return parse_utah_612(text)
    if stem in CTE_COURSES:
        return parse_utah_cte(text, stem, CTE_COURSES[stem])
    raise ValueError(f"No parser for {path.name}")


def parse_all(directory: Path = DEFAULT_DIR) -> dict[str, list[dict]]:
    """Return {source: [docs]} for every .txt file in the directory."""
    return {p.stem: parse_file(p) for p in sorted(directory.glob("*.txt"))}
