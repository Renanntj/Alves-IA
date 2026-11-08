from fastapi import FastAPI

from .auth import router
from .config import get_settings
from .whatsapp.api import router as whatsapp_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="API base para funcionalidades de IA e automações.",
    version="0.1.0",
    debug=settings.debug,
)
app.include_router(router)
app.include_router(whatsapp_router)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "online"}


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
