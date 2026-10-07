"""Content-addressed local upload storage."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reel_lab.config import OBJECTS_DIR, UPLOADS_DIR
from reel_lab.db.models import MediaAsset, ProjectAsset, StoredFile
from reel_lab.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".aiff"}


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    raise HTTPException(status_code=415, detail=f"Unsupported format: {suffix or 'unknown'}")


async def store_upload(session: Session, upload: UploadFile) -> StoredFile:
    """Stream an upload to disk, hash it, and reuse identical stored content."""
    filename = Path(upload.filename or "file").name
    media_type_for(filename)
    temporary = UPLOADS_DIR / f"{uuid.uuid4().hex}.partial"
    digest = hashlib.sha256()
    size = 0
    with temporary.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            target.write(chunk)
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="File is empty")

    sha256 = digest.hexdigest()
    existing = session.scalar(select(StoredFile).where(StoredFile.sha256 == sha256))
    if existing:
        temporary.unlink(missing_ok=True)
        return existing

    suffix = Path(filename).suffix.lower()
    destination = OBJECTS_DIR / sha256[:2] / f"{sha256}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(destination)
    stored = StoredFile(sha256=sha256, path=str(destination), size=size)
    session.add(stored)
    session.flush()
    return stored


def get_or_create_asset(
    session: Session,
    *,
    user_id: str,
    stored: StoredFile,
    filename: str,
    content_type: str | None,
) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.user_id == user_id,
            MediaAsset.stored_file_id == stored.id,
        )
    )
    if asset:
        return asset
    safe_name = Path(filename).name
    asset = MediaAsset(
        user_id=user_id,
        stored_file_id=stored.id,
        original_name=safe_name,
        mime_type=content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
        media_type=media_type_for(safe_name),
    )
    session.add(asset)
    session.flush()
    return asset


def attach_asset(session: Session, project_id: str, asset_id: str) -> ProjectAsset:
    association = session.get(ProjectAsset, (project_id, asset_id))
    if association:
        association.selected = True
        return association
    highest = session.scalar(
        select(func.max(ProjectAsset.sort_order)).where(ProjectAsset.project_id == project_id)
    )
    next_order = int(highest) + 1 if highest is not None else 0
    association = ProjectAsset(
        project_id=project_id,
        asset_id=asset_id,
        selected=True,
        sort_order=next_order,
    )
    session.add(association)
    return association
