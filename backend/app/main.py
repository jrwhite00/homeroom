from fastapi import FastAPI

from app.courses_api import router as courses_router
from app.search import router as search_router

app = FastAPI(title="Homeroom")
app.include_router(search_router)
app.include_router(courses_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
