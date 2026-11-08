from fastapi import FastAPI

from .config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="API base para funcionalidades de IA e automações.",
    version="0.1.0",
    debug=settings.debug,
)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "online"}


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}

