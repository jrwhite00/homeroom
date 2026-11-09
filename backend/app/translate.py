"""translate(question): turn a student's everyday question into technical search words.

It finds the concepts whose definitions are closest in meaning to the question (semantic
search on the concepts index) and returns their technical words as the search text.
"How do big websites stay up when everyone visits" has none of the words a talk title uses;
a concept defined in plain language bridges the two.

The closest concept is always used. Others are added only if they score at least
RELATIVE_CUTOFF of the closest one, at most MAX_CONCEPTS in all, so a weak second match
does not dilute the search with unrelated words.
"""
from __future__ import annotations

from app.es import ES

INDEX = "concepts"
MAX_CONCEPTS = 3
RELATIVE_CUTOFF = 0.8


def closest_concepts(es: ES, text: str, index: str = INDEX) -> list[dict]:
    """The MAX_CONCEPTS concepts closest in meaning to the text, best first, each with its "_score"."""
    body = {"size": MAX_CONCEPTS, "query": {"match": {"semantic": {"query": text}}},
            "_source": ["slug", "concept", "technical_words"]}
    hits = es.search(index, body).get("hits", {}).get("hits", [])
    return [{**h["_source"], "_score": h.get("_score") or 0} for h in hits]


def translate(es: ES, question: str, index: str = INDEX) -> dict | None:
    """Return {"text", "concepts"} for the closest concepts, or None if no concept matches."""
    hits = closest_concepts(es, question, index)
    if not hits or not hits[0]["_score"]:
        return None
    top = hits[0]["_score"]
    chosen = [h for h in hits if h["_score"] >= RELATIVE_CUTOFF * top]
    words: list[str] = []
    for concept in chosen:
        words += [w for w in concept.get("technical_words", []) if w not in words]
    if not words:
        return None
    return {"text": ", ".join(words), "concepts": [c.get("concept") or c.get("slug") for c in chosen]}
