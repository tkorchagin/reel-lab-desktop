"""FastAPI entry point for the local full-stack Reel Lab application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reel_lab.api.router import router
from reel_lab.config import FRONTEND_DIST
from reel_lab.db import upgrade_database
from reel_lab.services.music import seed_local_presets


@asynccontextmanager
async def lifespan(_app: FastAPI):
    upgrade_database()
    seed_local_presets()
    yield


app = FastAPI(title="Reel Lab", version="0.2.0", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_relative_to(FRONTEND_DIST) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def frontend_missing() -> None:
        raise HTTPException(status_code=503, detail="Frontend has not been built yet")
