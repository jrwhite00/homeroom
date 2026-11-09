"""Push the Dash0 dashboard and targets in observability/dash0/ to Dash0, or export the dashboard back.

    cd backend && python -m app.dash0_sync push     # upsert dashboard + SLOs by their origin ids
    cd backend && python -m app.dash0_sync export   # write the live dashboard back to dashboard.json

Needs DASH0_API_URL (for example https://api.us-west-2.aws.dash0.com) and DASH0_API_TOKEN in the
environment. The token is only sent as a header and never printed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "observability" / "dash0"
DATASET = "default"
DASHBOARD_ID = "homeroom-overview"
SLOS = {"slo-schedule-latency.json": "homeroom-schedule-latency", "slo-students-placed.json": "homeroom-three-talks-a-day"}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    base, token = os.environ["DASH0_API_URL"].rstrip("/"), os.environ["DASH0_API_TOKEN"]
    sep = "&" if "?" in path else "?"
    request = urllib.request.Request(f"{base}{path}{sep}dataset={DATASET}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:600]}


def push() -> int:
    ok = True
    status, body = call("PUT", f"/api/dashboards/{DASHBOARD_ID}", json.loads((DIR / "dashboard.json").read_text()))
    print(f"dashboard {DASHBOARD_ID}: HTTP {status}" + ("" if status < 300 else f" {body.get('error')}"))
    ok &= status < 300
    for file, origin in SLOS.items():
        status, body = call("PUT", f"/api/slos/{origin}", json.loads((DIR / file).read_text()))
        print(f"slo {origin}: HTTP {status}" + ("" if status < 300 else f" {body.get('error')}"))
        ok &= status < 300
    return 0 if ok else 1


def export() -> int:
    status, body = call("GET", f"/api/dashboards/{DASHBOARD_ID}")
    if status >= 300:
        print(f"export failed: HTTP {status} {body.get('error')}", file=sys.stderr)
        return 1
    for volatile in ("createdAt", "updatedAt", "version"):  # keep the file stable in git
        body.get("metadata", {}).pop(volatile, None)
    (DIR / "dashboard.json").write_text(json.dumps(body, indent=2) + "\n")
    print(f"wrote {DIR / 'dashboard.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["push"]:
        return push()
    if args[:1] == ["export"]:
        return export()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
