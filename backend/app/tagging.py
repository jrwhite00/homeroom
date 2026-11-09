"""Tag each session with the curriculum concepts it assumes. Pure logic: no network calls.

A tag is a concept slug from curriculum/concepts plus a one-sentence reason. A talk assumes
zero to four concepts. The model answers through structured output whose `concept` field is an
enum of the real slugs, and every slug is checked again here, so only slugs that exist are kept.

Each result is cached in data/tags.json under the session id, with a key that changes whenever
anything that could change the answer changes: the model, this prompt, the concept table, or
the talk's own text. A rerun with nothing changed sends nothing to the model.

Status per session:
  tagged   the model answered; assumes may be empty (the talk assumes nothing)
  unknown  no usable answer (error, refusal, expired, unparseable); readiness treats it as unknown
"""
from __future__ import annotations

import hashlib
import json
import re

PROMPT_VERSION = "1"
MAX_CONCEPTS = 4
CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

SYSTEM = """You help high school students decide which conference talks they are ready for.
You are given a table of concepts, each with a slug, a plain-language definition and technical words.
For one talk, list the concepts from the table that the talk ASSUMES the audience already knows:
knowledge the speaker will not stop to explain. Do not list concepts the talk teaches or only mentions.
List zero to four concepts, most important first. An introductory talk may assume none.
For each one, give a one-sentence reason that points to what in the talk needs it.
Use only slugs from the table."""


def concept_table(concepts: list[dict]) -> str:
    """Render the concept table for the system prompt, in slug order so the text is stable."""
    lines = []
    for c in sorted(concepts, key=lambda c: c["slug"]):
        words = ", ".join(c.get("technical_words", []))
        lines.append(f"- {c['slug']}: {c.get('concept', '')}. {c.get('definition', '')} (technical words: {words})")
    return "Concepts:\n" + "\n".join(lines)


def table_fingerprint(concepts: list[dict]) -> str:
    return hashlib.sha256(concept_table(concepts).encode()).hexdigest()[:16]


def talk_text(session: dict) -> str:
    parts = [f"Title: {session.get('title', '')}"]
    if session.get("track"):
        parts.append(f"Track: {', '.join(session['track'])}")
    if session.get("level"):
        parts.append(f"Audience level: {session['level']}")
    parts.append(f"Description: {session.get('abstract') or '(none)'}")
    return "\n".join(parts)


def cache_key(session: dict, fingerprint: str, model: str) -> str:
    raw = json.dumps([PROMPT_VERSION, model, fingerprint, talk_text(session)])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def output_schema(slugs: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "assumes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"concept": {"type": "string", "enum": sorted(slugs)},
                                   "reason": {"type": "string"}},
                    "required": ["concept", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["assumes"],
        "additionalProperties": False,
    }


def custom_id(session_id: str) -> str:
    """Batch custom_id for a session; session ids that don't fit the format are hashed."""
    return session_id if CUSTOM_ID.match(session_id) else "h" + hashlib.sha256(session_id.encode()).hexdigest()[:40]


def build_request(session: dict, concepts: list[dict], model: str) -> dict:
    """One Message Batches request for one session."""
    return {
        "custom_id": custom_id(session["id"]),
        "params": {
            "model": model,
            "max_tokens": 4096,
            "output_config": {"effort": "low", "format": {
                "type": "json_schema", "schema": output_schema([c["slug"] for c in concepts])}},
            # The concept table is identical for every request, so it is cached after the first.
            "system": [{"type": "text", "text": SYSTEM},
                       {"type": "text", "text": concept_table(concepts), "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": talk_text(session)}],
        },
    }


def parse_answer(text: str, slugs: set[str]) -> tuple[list[dict], list[str]]:
    """Return (accepted tags, notes). Drops unknown or repeated slugs and anything past four."""
    data = json.loads(text)
    accepted, notes = [], []
    for item in data.get("assumes", []):
        slug, reason = str(item.get("concept", "")).strip(), " ".join(str(item.get("reason", "")).split())
        if slug not in slugs:
            notes.append(f"dropped unknown concept '{slug}'")
        elif any(t["concept"] == slug for t in accepted):
            notes.append(f"dropped repeated concept '{slug}'")
        elif len(accepted) == MAX_CONCEPTS:
            notes.append(f"dropped '{slug}': more than {MAX_CONCEPTS} concepts")
        else:
            accepted.append({"concept": slug, "reason": reason})
    return accepted, notes


def entry_from_result(result_type: str, message: dict | None, error: str | None, key: str, model: str,
                      slugs: set[str]) -> dict:
    """Turn one batch result into a tags.json entry (tagged or unknown)."""
    entry = {"key": key, "model": model, "status": "unknown", "assumes": [], "notes": []}
    if result_type != "succeeded" or message is None:
        entry["notes"].append(f"batch result {result_type}" + (f": {error}" if error else ""))
        return entry
    if message.get("stop_reason") != "end_turn":
        entry["notes"].append(f"stopped early: {message.get('stop_reason')}")
        return entry
    text = next((b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"), "")
    try:
        entry["assumes"], entry["notes"] = parse_answer(text, slugs)
        entry["status"] = "tagged"
    except (ValueError, AttributeError) as e:
        entry["notes"].append(f"could not read the answer: {e}")
    return entry


def session_update(entry: dict | None) -> dict:
    """The fields written onto a session document. Unknown leaves assumes unset, as readiness expects."""
    if entry is None or entry["status"] != "tagged":
        return {"assumes": None, "assumes_reason": None, "assumes_status": "unknown"}
    return {"assumes": [t["concept"] for t in entry["assumes"]],
            "assumes_reason": [t["reason"] for t in entry["assumes"]],
            "assumes_status": "tagged"}
