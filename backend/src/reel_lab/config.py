"""Local filesystem configuration for the FastAPI application."""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DATA_DIR = Path(os.environ.get("REEL_LAB_DATA_DIR", BACKEND_ROOT / "data")).resolve()
DATABASE_PATH = DATA_DIR / "reel-lab.sqlite3"
OBJECTS_DIR = DATA_DIR / "objects"
UPLOADS_DIR = DATA_DIR / "uploads"
DERIVATIVES_DIR = DATA_DIR / "derivatives"
MUSIC_DIR = DATA_DIR / "music"
AUDIO_LIBRARY_DIR = DATA_DIR / "audio-library"
PROJECTS_DIR = DATA_DIR / "projects"
EXPORTS_DIR = DATA_DIR / "exports"
FRONTEND_DIST = REPOSITORY_ROOT / "frontend" / "dist"


def ensure_data_directories() -> None:
    """Create private local storage directories used by the application."""
    for path in (
        DATA_DIR,
        OBJECTS_DIR,
        UPLOADS_DIR,
        DERIVATIVES_DIR,
        MUSIC_DIR,
        AUDIO_LIBRARY_DIR,
        PROJECTS_DIR,
        EXPORTS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
