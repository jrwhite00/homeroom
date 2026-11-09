from fastapi import FastAPI

app = FastAPI(title="Homeroom")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
