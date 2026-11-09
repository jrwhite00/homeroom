"""The jargon decoder: mark every technical word in a talk abstract with its concept's definition.

Definitions come only from the concept table (curriculum/concepts, loaded into the concepts index).
Nothing here writes or rewords a definition: a term is decoded only when it matches one of a
concept's `technical words`, and the definition shown is that concept's `definition`, verbatim.

    GET /talks/{id}   the talk's title and its abstract split into plain text and decoded terms

Matching is case-insensitive, on whole words (so "table" doesn't match inside "stable"), with an
optional plural "s". Longer technical words win over shorter ones that overlap them ("container
image" before "image"). If two concepts list the same word, the term shows both definitions.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException

from app.es import ES, ESError
from app.search import get_es

CONCEPTS, SESSIONS = "concepts", "sessions"

router = APIRouter()


def term_index(concepts: list[dict]) -> dict[str, list[dict]]:
    """Lowercased technical word -> the concepts (name and definition, as written) that list it."""
    index: dict[str, list[dict]] = {}
    for c in concepts:
        if not c.get("definition"):
            continue  # never decode a term without a teacher's definition
        for word in c.get("technical_words") or []:
            key = " ".join(word.lower().split())
            entry = {"slug": c.get("slug"), "concept": c.get("concept"), "definition": c["definition"]}
            if key and entry not in index.setdefault(key, []):
                index[key].append(entry)
    return index


def _term_regex(term: str) -> str:
    """One technical word as a regex: any run of spaces between words, and an optional plural."""
    *head, last = term.split()
    stem = r"\s+".join(re.escape(part) for part in head + [last[:-1] if last.endswith("y") else last])
    plural = "(?:y|ies)" if last.endswith("y") else "(?:e?s)?"
    return stem + plural


def term_pattern(terms: list[str]) -> re.Pattern | None:
    """Whole-word, case-insensitive; group t<i> matches terms[i]. Longest terms are tried first."""
    if not terms:
        return None
    body = "|".join(f"(?P<t{i}>{_term_regex(t)})" for i, t in enumerate(terms))
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


def decode(text: str, concepts: list[dict]) -> list[dict]:
    """Split text into segments: {"text"} for plain text, {"text", "concepts"} for a decoded term."""
    index = term_index(concepts)
    terms = sorted(index, key=len, reverse=True)  # longest first, so overlapping words prefer the longer
    pattern = term_pattern(terms)
    if not text or pattern is None:
        return [{"text": text}] if text else []
    segments, pos = [], 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            segments.append({"text": text[pos:m.start()]})
        segments.append({"text": m.group(0), "concepts": index[terms[int(m.lastgroup[1:])]]})
        pos = m.end()
    if pos < len(text):
        segments.append({"text": text[pos:]})
    return segments


def concept_table(es: ES) -> list[dict]:
    res = es.search(CONCEPTS, {"size": 500, "query": {"match_all": {}},
                               "_source": ["slug", "concept", "definition", "technical_words"]})
    return [h["_source"] for h in res.get("hits", {}).get("hits", [])]


@router.get("/talks/{talk_id}")
def talk(talk_id: str, es: ES = Depends(get_es)) -> dict:
    try:
        res = es.search(SESSIONS, {"size": 1, "query": {"term": {"id": talk_id}},
                                   "_source": ["id", "title", "abstract", "starts_at", "room", "level"]})
        hits = res.get("hits", {}).get("hits", [])
        if not hits:
            raise HTTPException(status_code=404, detail="We couldn't find that talk.")
        s = hits[0]["_source"]
        return {"id": s.get("id"), "title": s.get("title"), "starts_at": s.get("starts_at"), "room": s.get("room"),
                "level": s.get("level"), "abstract": decode(s.get("abstract") or "", concept_table(es))}
    except ESError as e:
        raise HTTPException(status_code=502, detail=f"search backend unavailable: {e}") from e
