"""Persistent local media-processing jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import and_, select

from reel_lab.db import session_scope
from reel_lab.db.models import (
    JobItem,
    MediaAnalysis,
    MediaAsset,
    ProcessingJob,
    Project,
    ProjectAsset,
    StoredFile,
)
from reel_lab.media import analyze_media
from reel_lab.services.derivatives import create_derivatives, media_metadata

ANALYSIS_VERSION = "media-v2"
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reel-lab")


def start_project_processing(user_id: str, project_id: str, profile: str) -> str:
    """Create a persistent job and start media analysis in the local executor."""
    with session_scope() as session:
        project = session.scalar(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        if not project:
            raise ValueError("Project not found")
        associations = session.scalars(
            select(ProjectAsset)
            .where(ProjectAsset.project_id == project_id, ProjectAsset.selected.is_(True))
            .order_by(ProjectAsset.sort_order)
        ).all()
        if not associations:
            raise ValueError("No media selected for this project")
        job = ProcessingJob(
            user_id=user_id,
            project_id=project_id,
            kind="media",
            stage="Preparing media",
        )
        session.add(job)
        session.flush()
        for association in associations:
            session.add(JobItem(job_id=job.id, asset_id=association.asset_id))
        project.processing_profile = profile
        project.status = "processing"
        job_id = job.id
    EXECUTOR.submit(_process_project, job_id, profile)
    return job_id


def _process_project(job_id: str, profile: str) -> None:
    errors = 0
    with session_scope() as session:
        job = session.get(ProcessingJob, job_id)
        if not job:
            return
        job.status = "processing"
        items_query = select(JobItem).where(JobItem.job_id == job_id)
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
        total = len(items)

    for index, raw_item in enumerate(items):
        try:
            _process_asset(job_id, raw_item.id, raw_item.asset_id, profile)
        except Exception as error:  # the job must continue with the remaining files
            errors += 1
            with session_scope() as session:
                item = session.get(JobItem, raw_item.id)
                if item:
                    item.status = "error"
                    item.error = str(error)
                    item.stage = "Processing failed"
        with session_scope() as session:
            job = session.get(ProcessingJob, job_id)
            if job:
                job.progress = round(((index + 1) / max(1, total)) * 100)
                job.stage = f"Processed {index + 1} of {total}"

    with session_scope() as session:
        job = session.get(ProcessingJob, job_id)
        if not job:
            return
        project = session.get(Project, job.project_id) if job.project_id else None
        job.status = "ready"
        job.progress = 100
        job.stage = "Processing complete"
        job.result_json = {"error_count": errors, "total": total}
        if project:
            project.status = "ready"


def _process_asset(job_id: str, item_id: str, asset_id: str, profile: str) -> None:
    with session_scope() as session:
        asset = session.get(MediaAsset, asset_id)
        item = session.get(JobItem, item_id)
        if not asset or not item:
            return
        analysis = session.scalar(
            select(MediaAnalysis).where(
                MediaAnalysis.asset_id == asset_id,
                MediaAnalysis.profile == profile,
                MediaAnalysis.version == ANALYSIS_VERSION,
            )
        )
        if analysis and analysis.status == "ready" and asset.proxy_path:
            item.status = "ready"
            item.progress = 100
            item.stage = "Already analyzed"
            return
        if not analysis:
            analysis = MediaAnalysis(asset_id=asset_id, profile=profile, version=ANALYSIS_VERSION)
            session.add(analysis)
            session.flush()
        analysis.status = "processing"
        analysis.progress = 4
        analysis.stage = "Reading metadata"
        item.status = "processing"
        item.progress = 4
        item.stage = analysis.stage
        stored = session.get(StoredFile, asset.stored_file_id)
        if not stored:
            raise RuntimeError("Source file not found")
        source = Path(stored.path)
        sha256 = stored.sha256
        media_type = asset.media_type

    def report(stage: str, completed: int, total: int) -> None:
        stage_names = {
            "metadata": "Reading metadata",
            "frames": "Evaluating frames",
            "complete": "Analysis complete",
        }
        if stage == "frames":
            progress = 12 + round(68 * completed / max(1, total))
        elif stage == "complete":
            progress = 82
        else:
            progress = 8
        with session_scope() as callback_session:
            current = callback_session.scalar(
                select(MediaAnalysis).where(
                    MediaAnalysis.asset_id == asset_id,
                    MediaAnalysis.profile == profile,
                    MediaAnalysis.version == ANALYSIS_VERSION,
                )
            )
            current_item = callback_session.get(JobItem, item_id)
            if current:
                current.progress = progress
                current.stage = stage_names.get(stage, stage)
            if current_item:
                current_item.progress = progress
                current_item.stage = stage_names.get(stage, stage)

    candidates = analyze_media([source], profile=profile, progress=report)
    with session_scope() as session:
        asset = session.get(MediaAsset, asset_id)
        analysis = session.scalar(
            select(MediaAnalysis).where(
                MediaAnalysis.asset_id == asset_id,
                MediaAnalysis.profile == profile,
                MediaAnalysis.version == ANALYSIS_VERSION,
            )
        )
        item = session.get(JobItem, item_id)
        if analysis:
            analysis.progress = 86
            analysis.stage = "Preparing preview"
        if item:
            item.progress = 86
            item.stage = "Preparing preview"
        if asset:
            asset.metadata_json = media_metadata(source, media_type)

    proxy, thumbnail = create_derivatives(source, sha256, media_type)
    with session_scope() as session:
        asset = session.get(MediaAsset, asset_id)
        analysis = session.scalar(
            select(MediaAnalysis).where(
                MediaAnalysis.asset_id == asset_id,
                MediaAnalysis.profile == profile,
                MediaAnalysis.version == ANALYSIS_VERSION,
            )
        )
        item = session.get(JobItem, item_id)
        if asset:
            asset.proxy_path = str(proxy)
            asset.thumbnail_path = str(thumbnail)
        if analysis:
            analysis.candidates_json = [candidate.to_dict() for candidate in candidates]
            analysis.status = "ready"
            analysis.progress = 100
            analysis.stage = "Ready"
            analysis.error = None
        if item:
            item.status = "ready"
            item.progress = 100
            item.stage = "Ready"
