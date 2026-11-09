"""Read the teacher-owned curriculum in curriculum/ (read only; never written from code).

Each file is simple `name: value` lines so a teacher can edit it on GitHub's website.
They are read line by line rather than as YAML, so a value that contains a colon
("definition: How X works: step one") still reads the way the teacher meant it.

    curriculum/concepts/<slug>.yaml   concept, definition, technical words
    curriculum/courses/<slug>.yaml    course, state, standard, teaches, added by

A concept's slug is its file name without .yaml. A course's `teaches` lists concept slugs,
separated by commas.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "curriculum"


class CurriculumError(ValueError):
    """A curriculum file could not be read."""


def parse_lines(text: str, name: str = "file") -> dict[str, str]:
    """Parse `name: value` lines into a dict with snake_case keys ("technical words" -> technical_words)."""
    out: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise CurriculumError(f"{name} line {number} has no ':' between a name and a value: {line.strip()}")
        key, value = line.split(":", 1)
        out["_".join(key.strip().lower().split())] = value.strip().strip('"').strip("'").strip()
    return out


def split_list(value: str | None) -> list[str]:
    """Split a comma-separated value into trimmed, non-empty items."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def read_concepts(directory: Path = DEFAULT_DIR) -> list[dict]:
    """Read every concepts/*.yaml into {slug, concept, definition, technical_words}."""
    concepts = []
    for path in sorted((directory / "concepts").glob("*.yaml")):
        f = parse_lines(path.read_text(encoding="utf-8"), path.name)
        concepts.append({
            "slug": path.stem,
            "concept": f.get("concept", ""),
            "definition": f.get("definition", ""),
            "technical_words": split_list(f.get("technical_words")),
        })
    return concepts


def read_courses(directory: Path = DEFAULT_DIR) -> list[dict]:
    """Read every courses/*.yaml into {slug, course, state, standard, teaches, added_by}."""
    courses = []
    for path in sorted((directory / "courses").glob("*.yaml")):
        f = parse_lines(path.read_text(encoding="utf-8"), path.name)
        courses.append({
            "slug": path.stem,
            "course": f.get("course", ""),
            "state": f.get("state", ""),
            "standard": f.get("standard", ""),
            "teaches": split_list(f.get("teaches")),
            "added_by": f.get("added_by", ""),
        })
    return courses


def missing_concepts(courses: list[dict], concepts: list[dict]) -> list[tuple[str, str]]:
    """Return (course slug, concept slug) for every taught concept that has no concept file."""
    known = {c["slug"] for c in concepts}
    return [(course["slug"], slug) for course in courses for slug in course["teaches"] if slug not in known]
