from readiness import NOT_YET, ONE_STEP, READY, UNKNOWN, readiness


def test_untagged_is_unknown_never_ready():
    assert readiness(None, {"containers"}) == (UNKNOWN, [])


def test_assumes_nothing_is_ready():
    assert readiness([], set()) == (READY, [])


def test_all_known_is_ready():
    assert readiness(["programming-basics"], {"programming-basics", "containers"}) == (READY, [])


def test_one_missing_is_one_step():
    assert readiness(["programming-basics", "containers"], {"programming-basics"}) == (ONE_STEP, ["containers"])


def test_two_missing_is_not_yet():
    assert readiness(["containers", "scaling-and-reliability"], set()) == (NOT_YET, ["containers", "scaling-and-reliability"])


def test_duplicates_count_once():
    assert readiness(["containers", "containers"], set()) == (ONE_STEP, ["containers"])
