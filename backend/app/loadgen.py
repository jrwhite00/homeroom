"""SIMULATED students: a load generator for the demo. Runs as its own Deployment, scaled to 0 until needed.

    kubectl -n homeroom scale deploy/loadgen --replicas=1   # start
    kubectl -n homeroom scale deploy/loadgen --replicas=0   # stop

Each simulated student is a fictional persona (a pseudonym, a state and a few courses) in the demo class. Personas only
pick what to ask; no pseudonym or other student identifier is ever sent, logged or put on a span.
Every request carries X-Homeroom-Simulated: loadgen, so the backend marks its span simulated=true.

The mix is roughly what a class exploring the conference would do: mostly searches (some translated
from everyday words), then checking their week, then looking at where Kubernetes placed them.
"""
from __future__ import annotations

import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PERSONAS = [  # fictional
    {"name": "maya", "state": "utah", "courses": ["utah-exploring-computer-science", "utah-computer-science-principles",
                                                  "utah-computer-programming-1"]},
    {"name": "theo", "state": "utah", "courses": ["utah-computer-programming-1", "utah-computer-programming-2"]},
    {"name": "ines", "state": "utah", "courses": ["utah-web-development-1"]},
    {"name": "kofi", "state": "utah", "courses": ["utah-exploring-computer-science"]},
    {"name": "rhea", "state": "utah", "courses": ["utah-computer-science-principles", "utah-web-development-1"]},
]
QUESTIONS = [
    ("autoscaling load balancing high availability", False),
    ("how do big websites stay up when everyone visits", True),
    ("getting started with kubernetes", False),
    ("how do apps keep secrets safe", True),
    ("observability opentelemetry traces", False),
    ("what is a service mesh", True),
    ("gpu scheduling for ai inference", False),
    ("how do I find out why my app is slow", True),
    ("platform engineering developer portal", False),
    ("ebpf networking security", False),
]
CLASS_CODE = "demo"  # the class the simulated students belong to; feeds the teacher's view
MIX = [("search", 0.6), ("week", 0.25), ("kubernetes", 0.15)]


def pick_request(rng: random.Random) -> tuple[str, str]:
    """Return (kind, path) for one simulated request. Paths never contain a pseudonym."""
    persona = rng.choice(PERSONAS)
    kind = rng.choices([k for k, _ in MIX], weights=[w for _, w in MIX])[0]
    if kind == "search":
        q, translate = rng.choice(QUESTIONS)
        params = {"q": q, "mode": "hybrid" if rng.random() < 0.8 else "keyword"}
        if translate:
            params["translate"] = "true"
        return kind, "/search?" + urllib.parse.urlencode(params)
    if kind == "week":
        courses = rng.sample(persona["courses"], k=rng.randint(1, len(persona["courses"])))
        return kind, "/week?" + urllib.parse.urlencode({"state": persona["state"], "courses": ",".join(courses),
                                                        "class_code": CLASS_CODE})
    return kind, "/placements?" + urllib.parse.urlencode({"class": "demo"})


def send(base_url: str, path: str, timeout: float = 10) -> int:
    request = urllib.request.Request(base_url + path, headers={"X-Homeroom-Simulated": "loadgen",
                                                               "User-Agent": "homeroom-loadgen"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def main() -> int:
    base_url = os.environ.get("LOADGEN_BASE_URL", "http://backend.homeroom.svc.cluster.local").rstrip("/")
    rps = float(os.environ.get("LOADGEN_RPS", "2"))
    rng = random.Random()
    counts: dict[str, int] = {}
    last = time.monotonic()
    print(f"simulated students: {len(PERSONAS)} personas, about {rps} requests a second to {base_url}", flush=True)
    while True:
        kind, path = pick_request(rng)
        status = send(base_url, path)
        key = f"{kind}:{status}"
        counts[key] = counts.get(key, 0) + 1
        if time.monotonic() - last > 30:  # counts only; never which persona
            print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)
            counts, last = {}, time.monotonic()
        time.sleep(rng.expovariate(rps))


if __name__ == "__main__":
    sys.exit(main())
