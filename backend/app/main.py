from fastapi import FastAPI, Request
from opentelemetry import trace

from app.chaos import router as chaos_router
from app.placements_api import router as placements_router
from app.search import router as search_router

app = FastAPI(title="Homeroom")
app.include_router(search_router)
app.include_router(placements_router)
app.include_router(chaos_router)


@app.middleware("http")
async def label_simulated(request: Request, call_next):
    """Requests from the load generator are simulated; say so on their span."""
    if request.headers.get("x-homeroom-simulated"):
        trace.get_current_span().set_attribute("simulated", True)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
