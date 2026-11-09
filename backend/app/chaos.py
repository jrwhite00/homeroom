"""The chaos switch: a SIMULATED slowdown that adds 800 ms to /search, for the observability demo.

Off by default. GET /chaos shows its state (the app's banner reads it). POST /chaos flips it and
needs the token from CHAOS_TOKEN (a Kubernetes secret); with no token configured it can't be flipped.
Every span the delay touches carries induced=true, so the slowdown is never mistaken for a real one.
Requests from the load generator carry X-Homeroom-Simulated and get simulated=true on their span.
"""
from __future__ import annotations

import hmac
import os
import threading
import time

from fastapi import APIRouter, Header, HTTPException
from opentelemetry import trace
from pydantic import BaseModel

DELAY_SECONDS = 0.8
LABEL = "Simulated slowdown: the chaos switch adds 800 ms to search for the demo. Nothing is really wrong."

router = APIRouter()
tracer = trace.get_tracer("homeroom.chaos")
_lock = threading.Lock()
_state = {"enabled": False}


def enabled() -> bool:
    return _state["enabled"]


def set_enabled(value: bool) -> None:
    with _lock:
        _state["enabled"] = value


def apply_delay() -> bool:
    """If the switch is on, mark the current span induced and sleep inside an induced child span."""
    if not enabled():
        return False
    trace.get_current_span().set_attribute("induced", True)
    with tracer.start_as_current_span("chaos.delay", attributes={"induced": True, "chaos.delay_ms": 800}):
        time.sleep(DELAY_SECONDS)
    return True


class Switch(BaseModel):
    enabled: bool


def status() -> dict:
    return {"enabled": enabled(), "delay_ms": int(DELAY_SECONDS * 1000), "simulated": True, "label": LABEL}


@router.get("/chaos")
def get_chaos() -> dict:
    return status()


@router.post("/chaos")
def post_chaos(switch: Switch, authorization: str | None = Header(default=None)) -> dict:
    token = os.environ.get("CHAOS_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="The chaos switch has no token configured.")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="A valid token is needed to flip the chaos switch.")
    set_enabled(switch.enabled)
    return status()
