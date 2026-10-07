"""REST endpoints consumed by the React application."""

from __future__ import annotations

import mimetypes
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from reel_lab.api.dependencies import (
    current_user,
    database_session,
    session_token_hash,
    set_session_cookie,
)
from reel_lab.api.schemas import (
    AssetOrder,
    AssetSelection,
    AttachAssets,
    GenerateRequest,
    ProcessRequest,
    ProjectCreate,
    ProjectUpdate,
    SessionTransferCreate,
)
from reel_lab.db.models import (
    Export,
    JobItem,
    MediaAnalysis,
    MediaAsset,
    MusicTrack,
    ProcessingJob,
    Project,
    ProjectAsset,
    SessionTransfer,
    StoredFile,
    User,
    UserSession,
)
from reel_lab.services.derivatives import create_derivatives, media_metadata
from reel_lab.services.music import list_music, start_music_processing
from reel_lab.services.processing import ANALYSIS_VERSION, start_project_processing
from reel_lab.services.projects import (
    generate_project_variants,
    project_debug_log,
    project_payload,
    project_preview,
    start_export,
)
from reel_lab.services.storage import (
    attach_asset,
    get_or_create_asset,
    media_type_for,
    store_upload,
)

router = APIRouter(prefix="/api")
Database = Annotated[Session, Depends(database_session)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("/session")
def session_info(user: CurrentUser) -> dict[str, str]:
    return {"user_id": user.id, "label": f"Local {user.id[:4].upper()}"}


@router.post("/session/transfers", status_code=201)
def create_session_transfer(
    body: SessionTransferCreate,
    session: Database,
    user: CurrentUser,
) -> dict[str, str]:
    parsed = urlsplit(body.next_path)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise HTTPException(status_code=422, detail="Only local paths are allowed")
    code = secrets.token_urlsafe(24)
    session.add(
        SessionTransfer(
            code_hash=session_token_hash(code),
            user_id=user.id,
            next_path=body.next_path,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    session.flush()
    return {"url": f"/api/session/transfers/{code}", "expires_in": "600"}


@router.get("/session/transfers/{code}")
def claim_session_transfer(code: str, session: Database) -> RedirectResponse:
    code_hash = session_token_hash(code)
    transfer = session.get(SessionTransfer, code_hash)
    if not transfer:
        raise HTTPException(status_code=404, detail="Sign-in link not found")
    expires_at = transfer.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        session.delete(transfer)
        session.flush()
        raise HTTPException(status_code=410, detail="Sign-in link has expired")

    token = secrets.token_urlsafe(32)
    session.add(UserSession(token_hash=session_token_hash(token), user_id=transfer.user_id))
    next_path = transfer.next_path
    session.delete(transfer)
    session.flush()
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookie(response, token)
    return response


@router.get("/projects")
def projects(session: Database, user: CurrentUser) -> list[dict[str, object]]:
    rows = session.scalars(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    ).all()
    result = []
    for project in rows:
        payload = project_payload(project)
        payload["asset_count"] = len(
            session.scalars(select(ProjectAsset).where(ProjectAsset.project_id == project.id)).all()
        )
        result.append(payload)
    return result


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, session: Database, user: CurrentUser) -> dict[str, object]:
    project = Project(user_id=user.id, name=body.name.strip())
    session.add(project)
    session.flush()
    return project_payload(project)


@router.get("/projects/{project_id}")
def project_detail(project_id: str, session: Database, user: CurrentUser) -> dict[str, object]:
    project = _owned_project(session, user.id, project_id)
    assets = _project_assets(session, project)
    latest_job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.project_id == project.id)
        .order_by(ProcessingJob.created_at.desc())
    )
    return {
        "project": project_payload(project),
        "assets": assets,
        "job": _job_payload(session, latest_job) if latest_job else None,
    }


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: Database,
    user: CurrentUser,
) -> dict[str, object]:
    project = _owned_project(session, user.id, project_id)
    values = body.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = values["name"].strip()
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Enter a project name")
    if {"duration", "music_track_id"} & values.keys() and "music_start" not in values:
        values["music_start"] = None
    for field, value in values.items():
        if field == "random_seed" and value is None:
            continue
        setattr(project, field, value)
    session.flush()
    return project_payload(project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: Database, user: CurrentUser) -> Response:
    project = _owned_project(session, user.id, project_id)
    session.delete(project)
    session.flush()
    return Response(status_code=204)


@router.post("/projects/{project_id}/media", status_code=201)
async def upload_project_media(
    project_id: str,
    session: Database,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    project = _owned_project(session, user.id, project_id)
    stored = await store_upload(session, file)
    asset = get_or_create_asset(
        session,
        user_id=user.id,
        stored=stored,
        filename=file.filename or "file",
        content_type=file.content_type,
    )
    attach_asset(session, project.id, asset.id)
    project.status = "draft"
    session.flush()
    return _asset_payload(session, asset, project.processing_profile, selected=True)


@router.post("/projects/{project_id}/assets", status_code=201)
def attach_library_assets(
    project_id: str,
    body: AttachAssets,
    session: Database,
    user: CurrentUser,
) -> dict[str, int]:
    project = _owned_project(session, user.id, project_id)
    assets = session.scalars(
        select(MediaAsset).where(MediaAsset.user_id == user.id, MediaAsset.id.in_(body.asset_ids))
    ).all()
    for asset in assets:
        attach_asset(session, project.id, asset.id)
    return {"attached": len(assets)}


@router.put("/projects/{project_id}/assets/order")
def reorder_project_assets(
    project_id: str,
    body: AssetOrder,
    session: Database,
    user: CurrentUser,
) -> dict[str, int]:
    project = _owned_project(session, user.id, project_id)
    associations = session.scalars(
        select(ProjectAsset)
        .where(ProjectAsset.project_id == project.id)
        .order_by(ProjectAsset.sort_order)
    ).all()
    current_ids = {association.asset_id for association in associations}
    requested_ids = set(body.asset_ids)
    if len(requested_ids) != len(body.asset_ids) or requested_ids != current_ids:
        raise HTTPException(
            status_code=400,
            detail="Order must include every project source exactly once",
        )

    by_asset_id = {association.asset_id: association for association in associations}
    for sort_order, asset_id in enumerate(body.asset_ids):
        by_asset_id[asset_id].sort_order = sort_order
    if project.status == "preview":
        project.status = "ready"
    session.flush()
    return {"ordered": len(body.asset_ids)}


@router.patch("/projects/{project_id}/assets/{asset_id}")
def select_project_asset(
    project_id: str,
    asset_id: str,
    body: AssetSelection,
    session: Database,
    user: CurrentUser,
) -> dict[str, bool]:
    _owned_project(session, user.id, project_id)
    association = session.get(ProjectAsset, (project_id, asset_id))
    if not association:
        raise HTTPException(status_code=404, detail="Media not found in this project")
    association.selected = body.selected
    return {"selected": association.selected}


@router.delete("/projects/{project_id}/assets/{asset_id}", status_code=204)
def remove_project_asset(
    project_id: str,
    asset_id: str,
    session: Database,
    user: CurrentUser,
) -> Response:
    _owned_project(session, user.id, project_id)
    session.execute(
        delete(ProjectAsset).where(
            ProjectAsset.project_id == project_id, ProjectAsset.asset_id == asset_id
        )
    )
    return Response(status_code=204)


@router.post("/projects/{project_id}/process", status_code=202)
def process_project(
    project_id: str,
    body: ProcessRequest,
    session: Database,
    user: CurrentUser,
) -> dict[str, object]:
    project = _owned_project(session, user.id, project_id)
    project.processing_profile = body.profile
    session.commit()
    try:
        job_id = start_project_processing(user.id, project_id, body.profile)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"job_id": job_id, "status": "queued"}


@router.get("/library")
def library(session: Database, user: CurrentUser) -> list[dict[str, object]]:
    assets = session.scalars(
        select(MediaAsset)
        .where(MediaAsset.user_id == user.id)
        .order_by(MediaAsset.created_at.desc())
    ).all()
    return [_asset_payload(session, asset, None) for asset in assets]


@router.get("/music")
def music(user: CurrentUser) -> list[dict[str, object]]:
    return [_music_payload(track) for track in list_music(user.id)]


@router.post("/music", status_code=202)
async def upload_music(
    session: Database,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    media_type = media_type_for(file.filename or "file")
    if media_type not in {"audio", "video"}:
        raise HTTPException(status_code=415, detail="Music requires an audio or video file")
    stored = await store_upload(session, file)
    track = MusicTrack(
        user_id=user.id,
        stored_file_id=stored.id,
        title=Path(file.filename or "New track").stem,
        source_path=stored.path,
        source_kind=media_type,
    )
    session.add(track)
    session.flush()
    session.commit()
    job_id = start_music_processing(user.id, track.id)
    return {"track": _music_payload(track), "job_id": job_id}


@router.post("/music/from-media/{asset_id}", status_code=202)
def music_from_media(
    asset_id: str,
    session: Database,
    user: CurrentUser,
) -> dict[str, object]:
    row = session.execute(
        select(MediaAsset, StoredFile)
        .join(StoredFile, StoredFile.id == MediaAsset.stored_file_id)
        .where(MediaAsset.id == asset_id, MediaAsset.user_id == user.id)
    ).first()
    if not row or row[0].media_type != "video":
        raise HTTPException(status_code=404, detail="Video not found")
    asset, stored = row
    track = MusicTrack(
        user_id=user.id,
        stored_file_id=stored.id,
        source_asset_id=asset.id,
        title=asset.original_name.rsplit(".", 1)[0],
        source_path=stored.path,
        source_kind="video",
    )
    session.add(track)
    session.flush()
    session.commit()
    job_id = start_music_processing(user.id, track.id)
    return {"track": _music_payload(track), "job_id": job_id}


@router.post("/projects/{project_id}/variants")
def generate_variants(
    project_id: str,
    body: GenerateRequest,
    session: Database,
    user: CurrentUser,
) -> dict[str, object]:
    project = _owned_project(session, user.id, project_id)
    for field, value in body.model_dump().items():
        if field == "random_seed" and value is None:
            continue
        setattr(project, field, value)
    session.commit()
    try:
        return generate_project_variants(user.id, project_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/projects/{project_id}/preview")
def preview(project_id: str, user: CurrentUser) -> dict[str, object]:
    try:
        return project_preview(user.id, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/projects/{project_id}/debug-log")
def debug_log(project_id: str, user: CurrentUser) -> dict[str, object]:
    try:
        return project_debug_log(user.id, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/variants/{variant_id}/exports", status_code=202)
def export_variant(variant_id: str, user: CurrentUser) -> dict[str, object]:
    try:
        job_id = start_export(user.id, variant_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
def job(job_id: str, session: Database, user: CurrentUser) -> dict[str, object]:
    row = session.scalar(
        select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_payload(session, row)


@router.get("/assets/{asset_id}/proxy")
def asset_proxy(asset_id: str, session: Database, user: CurrentUser) -> FileResponse:
    asset = _owned_asset(session, user.id, asset_id)
    if not asset.proxy_path:
        raise HTTPException(status_code=404, detail="Preview is not ready yet")
    media_type = "image/jpeg" if asset.media_type == "photo" else "video/mp4"
    return _file_response(Path(asset.proxy_path), media_type)


@router.get("/assets/{asset_id}/content")
def asset_content(asset_id: str, session: Database, user: CurrentUser) -> FileResponse:
    asset = _owned_asset(session, user.id, asset_id)
    stored = session.get(StoredFile, asset.stored_file_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Source file not found")
    return _file_response(Path(stored.path), asset.mime_type)


@router.get("/assets/{asset_id}/thumbnail")
def asset_thumbnail(asset_id: str, session: Database, user: CurrentUser) -> FileResponse:
    asset = _owned_asset(session, user.id, asset_id)
    if not asset.thumbnail_path:
        raise HTTPException(status_code=404, detail="Cover is not ready yet")
    return _file_response(Path(asset.thumbnail_path), "image/jpeg")


@router.get("/music/{track_id}/content")
def music_content(track_id: str, session: Database, user: CurrentUser) -> FileResponse:
    track = session.scalar(
        select(MusicTrack).where(
            MusicTrack.id == track_id,
            or_(MusicTrack.user_id == user.id, MusicTrack.user_id.is_(None)),
        )
    )
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    path = Path(track.preview_path or track.source_path)
    media_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    return _file_response(path, media_type)


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, session: Database, user: CurrentUser) -> FileResponse:
    export = session.scalar(select(Export).where(Export.id == export_id, Export.user_id == user.id))
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(
        export.output_path,
        media_type="video/mp4",
        filename=f"reel-{export.variant_id[:8]}.mp4",
    )


def _owned_project(session: Session, user_id: str, project_id: str) -> Project:
    project = session.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _owned_asset(session: Session, user_id: str, asset_id: str) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == user_id)
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Media not found")
    return asset


def _project_assets(session: Session, project: Project) -> list[dict[str, object]]:
    rows = session.execute(
        select(ProjectAsset, MediaAsset)
        .join(MediaAsset, MediaAsset.id == ProjectAsset.asset_id)
        .where(ProjectAsset.project_id == project.id)
        .order_by(ProjectAsset.sort_order)
    ).all()
    return [
        _asset_payload(
            session,
            asset,
            project.processing_profile,
            selected=association.selected,
            sort_order=association.sort_order,
        )
        for association, asset in rows
    ]


def _asset_payload(
    session: Session,
    asset: MediaAsset,
    profile: str | None,
    *,
    selected: bool = False,
    sort_order: int = 0,
) -> dict[str, object]:
    stored = session.get(StoredFile, asset.stored_file_id)
    _ensure_heic_preview(asset, stored)
    query = select(MediaAnalysis).where(
        MediaAnalysis.asset_id == asset.id, MediaAnalysis.version == ANALYSIS_VERSION
    )
    if profile:
        query = query.where(MediaAnalysis.profile == profile)
    else:
        query = query.order_by(MediaAnalysis.updated_at.desc())
    analysis = session.scalar(query)
    return {
        "id": asset.id,
        "name": asset.original_name,
        "media_type": asset.media_type,
        "mime_type": asset.mime_type,
        "size": stored.size if stored else 0,
        "selected": selected,
        "sort_order": sort_order,
        "metadata": asset.metadata_json,
        "source_url": f"/api/assets/{asset.id}/content",
        "thumbnail_url": f"/api/assets/{asset.id}/thumbnail" if asset.thumbnail_path else None,
        "proxy_url": f"/api/assets/{asset.id}/proxy" if asset.proxy_path else None,
        "analysis": {
            "profile": analysis.profile,
            "status": analysis.status,
            "progress": analysis.progress,
            "stage": analysis.stage,
            "error": analysis.error,
        }
        if analysis
        else {"profile": profile, "status": "new", "progress": 0, "stage": "Not analyzed"},
    }


def _music_payload(track: MusicTrack) -> dict[str, object]:
    analysis = track.analysis_json or {}
    highlights = _public_highlights(track.highlights_json)
    return {
        "id": track.id,
        "title": track.title,
        "source_kind": track.source_kind,
        "is_preset": track.is_preset,
        "status": track.status,
        "progress": track.progress,
        "stage": track.stage,
        "error": track.error,
        "duration": track.duration,
        "bpm": track.bpm,
        "metadata": track.metadata_json,
        "sort_order": track.sort_order,
        "highlights": highlights,
        "energy": analysis.get("energy", []),
        "downbeats": analysis.get("downbeats", []),
        "sections": analysis.get("sections", []),
    }


def _public_highlights(highlights: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in (highlights or {}).items() if not key.startswith("_")}


def _job_payload(session: Session, job: ProcessingJob) -> dict[str, object]:
    items_query = select(JobItem).where(JobItem.job_id == job.id)
    if job.project_id:
        items_query = items_query.join(
            ProjectAsset,
            and_(
                ProjectAsset.project_id == job.project_id,
                ProjectAsset.asset_id == JobItem.asset_id,
            ),
        ).order_by(ProjectAsset.sort_order)
    else:
        items_query = items_query.order_by(JobItem.id)
    items = session.scalars(items_query).all()
    progress = job.progress
    stage = job.stage
    if job.status in {"queued", "processing"} and items:
        progress = round(sum(item.progress for item in items) / len(items))
        completed = sum(item.status in {"ready", "error"} for item in items)
        active = next((item for item in items if item.status == "processing"), None)
        if active:
            stage = f"{active.stage} · {min(completed + 1, len(items))} of {len(items)}"
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": progress,
        "stage": stage,
        "error": job.error,
        "result": job.result_json,
        "created_at": _utc_timestamp(job.created_at),
        "updated_at": _utc_timestamp(job.updated_at),
        "items": [
            {
                "asset_id": item.asset_id,
                "status": item.status,
                "progress": item.progress,
                "stage": item.stage,
                "error": item.error,
            }
            for item in items
        ],
    }


def _ensure_heic_preview(asset: MediaAsset, stored: StoredFile | None) -> None:
    if (
        not stored
        or asset.media_type != "photo"
        or Path(asset.original_name).suffix.lower() not in {".heic", ".heif"}
        or (asset.proxy_path and asset.thumbnail_path)
    ):
        return
    source = Path(stored.path)
    proxy, thumbnail = create_derivatives(source, stored.sha256, asset.media_type)
    asset.proxy_path = str(proxy)
    asset.thumbnail_path = str(thumbnail)
    asset.metadata_json = media_metadata(source, asset.media_type)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _file_response(path: Path, media_type: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=media_type)
