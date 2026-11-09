from fastapi import FastAPI

from app import telemetry
from app.chaos import router as chaos_router
from app.courses_api import router as courses_router
from app.decoder import router as decoder_router
from app.lookup_api import router as lookup_router
from app.placements_api import refresh_gauges
from app.placements_api import router as placements_router
from app.search import get_es
from app.search import router as search_router
from app.week import router as week_router

app = FastAPI(title="Homeroom")
app.include_router(search_router)
app.include_router(courses_router)
app.include_router(lookup_router)
app.include_router(placements_router)
app.include_router(chaos_router)
app.include_router(week_router)
app.include_router(decoder_router)
# Request metrics, simulated=true on load generator spans, and the students-pending gauges.
telemetry.install(app, refresh=lambda: refresh_gauges(get_es()))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
