"""Readiness: which group a talk falls in for a student.

The rule (James's): count the concepts the talk assumes that the student doesn't know.
0 missing = ready, 1 = one step away, 2 or more = not yet.
A talk with no tags is unknown, never ready.
"""

READY = "ready"
ONE_STEP = "one_step"
NOT_YET = "not_yet"
UNKNOWN = "unknown"


def readiness(assumes: list[str] | None, knows: set[str]) -> tuple[str, list[str]]:
    """Return (group, missing concept slugs, sorted) for one talk and one student."""
    if assumes is None:
        return UNKNOWN, []
    missing = sorted(set(assumes) - set(knows))
    if not missing:
        return READY, missing
    if len(missing) == 1:
        return ONE_STEP, missing
    return NOT_YET, missing
