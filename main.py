from fastapi import FastAPI

from config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="智能电商推荐系统",
    version="0.1.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}
