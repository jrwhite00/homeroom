from fastapi import FastAPI

from app.search import router as search_router

app = FastAPI(title="Homeroom")
app.include_router(search_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
