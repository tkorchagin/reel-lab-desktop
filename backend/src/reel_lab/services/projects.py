"""Project assembly, variant generation, preview payloads, and exports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

from reel_lab.audio import select_highlights, slice_audio_map
from reel_lab.config import EXPORTS_DIR, PROJECTS_DIR, REPOSITORY_ROOT
from reel_lab.db import session_scope
from reel_lab.db.models import (
    Export,
    MediaAnalysis,
    MediaAsset,
    MusicTrack,
    ProcessingJob,
    Project,
    ProjectAsset,
    ReelVariant,
    StoredFile,
)
from reel_lab.models import AudioMap, MediaCandidate, Variant
from reel_lab.render import RENDER_CACHE_VERSION, render_variant
from reel_lab.services.derivatives import media_capture_time
from reel_lab.services.processing import ANALYSIS_VERSION
from reel_lab.timeline import generate_ordered_variants, generate_variants

EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reel-lab-export")


def generate_project_variants(user_id: str, project_id: str) -> dict[str, object]:
    """Build and persist four focused variants from cached analyses."""
    with session_scope() as session:
        project = _owned_project(session, user_id, project_id)
        if not project.music_track_id:
            raise ValueError("Choose a music track")
        track = session.get(MusicTrack, project.music_track_id)
        if not track or track.status != "ready":
            raise ValueError("Music has not been analyzed yet")
        rows = session.execute(
            select(ProjectAsset, MediaAsset, StoredFile)
            .join(MediaAsset, MediaAsset.id == ProjectAsset.asset_id)
            .join(StoredFile, StoredFile.id == MediaAsset.stored_file_id)
            .where(
                ProjectAsset.project_id == project.id,
                ProjectAsset.selected.is_(True),
            )
            .order_by(ProjectAsset.sort_order)
        ).all()
        rows = _filter_media_rows(rows, project.media_filter)
        if not rows:
            raise ValueError("No selected media of this type")
        if project.media_order == "capture_date":
            rows = sorted(rows, key=_capture_sort_key)
        candidates: list[MediaCandidate] = []
        paths: list[Path] = []
        for _association, asset, stored in rows:
            analysis = session.scalar(
                select(MediaAnalysis).where(
                    MediaAnalysis.asset_id == asset.id,
                    MediaAnalysis.profile == project.processing_profile,
                    MediaAnalysis.version == ANALYSIS_VERSION,
                    MediaAnalysis.status == "ready",
                )
            )
            if not analysis:
                raise ValueError(f"File {asset.original_name} has not been analyzed yet")
            candidates.extend(MediaCandidate(**item) for item in analysis.candidates_json)
            paths.append(Path(stored.path))
        if not candidates:
            raise ValueError("No analyzed media available for this reel")

        project.clip_reuse_mode = "multiple"
        maximum_mode = project.duration == 0
        duration_override = (
            _maximum_reel_duration(track, candidates, paths) if maximum_mode else None
        )
        audio_map = _project_audio_map(project, track, duration_override=duration_override)
        if maximum_mode:
            variants = generate_variants(
                audio_map,
                candidates,
                count=4,
                seed=project.random_seed,
                min_clip_duration=project.min_clip_duration,
                clip_reuse_mode="multiple",
            )
        else:
            variants = generate_ordered_variants(
                audio_map,
                candidates,
                paths,
                count=4,
                seed=project.random_seed,
                include_all=project.include_all,
                media_order=project.media_order,
                min_clip_duration=project.min_clip_duration,
                clip_reuse_mode="multiple",
            )
        session.execute(delete(ReelVariant).where(ReelVariant.project_id == project.id))
        for variant in variants:
            session.add(
                ReelVariant(
                    project_id=project.id,
                    variant_index=variant.id,
                    name=variant.name,
                    description=variant.description,
                    score=variant.score,
                    seed=variant.seed,
                    timeline_json=[item.__dict__ for item in variant.items],
                )
            )
        project.status = "preview"
    return project_preview(user_id, project_id)


def project_preview(user_id: str, project_id: str) -> dict[str, object]:
    with session_scope() as session:
        project = _owned_project(session, user_id, project_id)
        if not project.music_track_id:
            raise ValueError("No music selected for this project")
        track = session.get(MusicTrack, project.music_track_id)
        if not track:
            raise ValueError("Music not found")
        path_assets = {
            stored.path: asset
            for asset, stored in session.execute(
                select(MediaAsset, StoredFile)
                .join(ProjectAsset, ProjectAsset.asset_id == MediaAsset.id)
                .join(StoredFile, StoredFile.id == MediaAsset.stored_file_id)
                .where(ProjectAsset.project_id == project.id)
            ).all()
        }
        variants = session.scalars(
            select(ReelVariant)
            .where(ReelVariant.project_id == project.id)
            .order_by(ReelVariant.variant_index)
        ).all()
        duration_override = (
            _variant_duration(variants[0]) if project.duration == 0 and variants else None
        )
        audio_map = _project_audio_map(project, track, duration_override=duration_override)
        variant_payload = []
        for variant in variants:
            items = []
            for raw_item in variant.timeline_json:
                item = dict(raw_item)
                asset = path_assets.get(item["path"])
                if asset:
                    item["asset_id"] = asset.id
                    item["asset_url"] = f"/api/assets/{asset.id}/proxy"
                    item["source_name"] = asset.original_name
                items.append(item)
            variant_payload.append(
                {
                    "id": variant.id,
                    "index": variant.variant_index,
                    "name": variant.name,
                    "description": variant.description,
                    "score": variant.score,
                    "items": items,
                }
            )
        return {
            "project": project_payload(project),
            "duration": audio_map.duration,
            "audio_offset": audio_map.source_start,
            "bpm": audio_map.bpm,
            "beat_confidence": audio_map.beat_confidence,
            "beats": audio_map.beats,
            "downbeats": audio_map.downbeats,
            "analyzer": audio_map.analyzer,
            "track_url": f"/api/music/{track.id}/content",
            "energy": [point.__dict__ for point in audio_map.energy],
            "sections": [section.__dict__ for section in audio_map.sections],
            "variants": variant_payload,
        }


def project_debug_log(user_id: str, project_id: str) -> dict[str, object]:
    """Return a portable snapshot of inputs and generated edit decisions."""
    with session_scope() as session:
        project = _owned_project(session, user_id, project_id)
        if not project.music_track_id:
            raise ValueError("No music selected for this project")
        track = session.get(MusicTrack, project.music_track_id)
        if not track:
            raise ValueError("Music not found")
        rows = session.execute(
            select(ProjectAsset, MediaAsset, StoredFile)
            .join(MediaAsset, MediaAsset.id == ProjectAsset.asset_id)
            .join(StoredFile, StoredFile.id == MediaAsset.stored_file_id)
            .where(ProjectAsset.project_id == project.id)
            .order_by(ProjectAsset.sort_order)
        ).all()
        variants = session.scalars(
            select(ReelVariant)
            .where(ReelVariant.project_id == project.id)
            .order_by(ReelVariant.variant_index)
        ).all()
        duration_override = (
            _variant_duration(variants[0]) if project.duration == 0 and variants else None
        )
        audio_map = _project_audio_map(project, track, duration_override=duration_override)
        assets = []
        for association, asset, stored in rows:
            analysis = session.scalar(
                select(MediaAnalysis)
                .where(
                    MediaAnalysis.asset_id == asset.id,
                    MediaAnalysis.profile == project.processing_profile,
                    MediaAnalysis.version == ANALYSIS_VERSION,
                )
                .order_by(MediaAnalysis.updated_at.desc())
            )
            assets.append(
                {
                    "id": asset.id,
                    "name": asset.original_name,
                    "kind": asset.media_type,
                    "selected": association.selected,
                    "sort_order": association.sort_order,
                    "path": _relative_debug_path(stored.path),
                    "metadata": _sanitize_debug_value(asset.metadata_json),
                    "analysis": None
                    if not analysis
                    else {
                        "profile": analysis.profile,
                        "version": analysis.version,
                        "status": analysis.status,
                        "candidates": _sanitize_debug_value(analysis.candidates_json),
                    },
                }
            )
        return {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "project": project_payload(project),
            "music": {
                "id": track.id,
                "title": track.title,
                "source_path": _relative_debug_path(track.source_path),
                "preview_path": _relative_debug_path(track.preview_path),
                "source_start": audio_map.source_start,
                "duration": audio_map.duration,
                "bpm": audio_map.bpm,
                "highlights": _sanitize_debug_value(track.highlights_json),
                "analysis": _sanitize_debug_value(audio_map.to_dict()),
            },
            "assets": assets,
            "variants": [
                {
                    "id": variant.id,
                    "index": variant.variant_index,
                    "name": variant.name,
                    "description": variant.description,
                    "score": variant.score,
                    "seed": variant.seed,
                    "items": _sanitize_debug_value(variant.timeline_json),
                }
                for variant in variants
            ],
        }


def start_export(user_id: str, variant_id: str) -> str:
    should_render = False
    with session_scope() as session:
        variant = session.scalar(
            select(ReelVariant)
            .join(Project, Project.id == ReelVariant.project_id)
            .where(ReelVariant.id == variant_id, Project.user_id == user_id)
        )
        if not variant:
            raise ValueError("Variant not found")
        output = EXPORTS_DIR / user_id / f"{variant.id}-{RENDER_CACHE_VERSION}.mp4"
        job = ProcessingJob(
            user_id=user_id,
            project_id=variant.project_id,
            kind="export",
            stage="Preparing export",
        )
        session.add(job)
        session.flush()
        export = Export(
            user_id=user_id,
            variant_id=variant.id,
            job_id=job.id,
            output_path=str(output),
        )
        session.add(export)
        session.flush()
        if output.exists() and output.stat().st_size > 0:
            job.status = "ready"
            job.progress = 100
            job.stage = "Ready"
            job.result_json = {"url": f"/api/exports/{export.id}/download"}
        else:
            should_render = True
        job_id = job.id
        export_id = export.id
    if should_render:
        EXPORT_EXECUTOR.submit(_render_export, job_id, export_id)
    return job_id


def _render_export(job_id: str, export_id: str) -> None:
    try:
        with session_scope() as session:
            job = session.get(ProcessingJob, job_id)
            export = session.get(Export, export_id)
            if not job or not export:
                return
            variant_row = session.get(ReelVariant, export.variant_id)
            project = session.get(Project, variant_row.project_id) if variant_row else None
            track = session.get(MusicTrack, project.music_track_id) if project else None
            if not variant_row or not project or not track:
                raise RuntimeError("Could not restore the export project")
            variant = Variant.from_dict(
                {
                    "id": variant_row.variant_index,
                    "name": variant_row.name,
                    "description": variant_row.description,
                    "seed": variant_row.seed,
                    "score": variant_row.score,
                    "items": variant_row.timeline_json,
                }
            )
            duration_override = (
                variant.items[-1].timeline_end if project.duration == 0 and variant.items else None
            )
            audio_map = _project_audio_map(project, track, duration_override=duration_override)
            output = Path(export.output_path)
            job.status = "processing"
            job.progress = 3
            job.stage = "Rendering clips"
            project_id = project.id

        def report(completed: int, total: int) -> None:
            with session_scope() as session:
                current = session.get(ProcessingJob, job_id)
                if current:
                    current.progress = min(92, max(4, round(92 * completed / max(1, total))))
                    current.stage = f"Rendering clips {completed} of {total}"

        output.parent.mkdir(parents=True, exist_ok=True)
        render_variant(
            run_dir=PROJECTS_DIR / project_id,
            variant=variant,
            audio_map=audio_map,
            output=output,
            progress=report,
        )
        with session_scope() as session:
            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = "ready"
                job.progress = 100
                job.stage = "Ready"
                job.result_json = {"url": f"/api/exports/{export_id}/download"}
    except Exception as error:
        with session_scope() as session:
            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = "error"
                job.error = str(error)
                job.stage = "Export failed"


def _project_audio_map(
    project: Project,
    track: MusicTrack,
    *,
    duration_override: float | None = None,
) -> AudioMap:
    audio_map = AudioMap.from_dict(track.analysis_json)
    requested_duration = (
        duration_override if duration_override is not None else float(project.duration or 30)
    )
    duration = min(float(requested_duration), audio_map.duration)
    if project.music_start is not None:
        limit = max(0.0, audio_map.duration - duration)
        start = min(limit, max(0.0, float(project.music_start)))
        return slice_audio_map(audio_map, start, duration)

    selected = next(iter(select_highlights(audio_map, durations=(duration,)).values()), None)
    if not selected:
        return slice_audio_map(audio_map, 0.0, duration)
    return slice_audio_map(audio_map, float(selected["start"]), float(selected["duration"]))


def _maximum_reel_duration(
    track: MusicTrack,
    candidates: list[MediaCandidate],
    paths: list[Path],
) -> float:
    audio_map = AudioMap.from_dict(track.analysis_json)
    ordered_paths = list(dict.fromkeys(str(path.expanduser().resolve()) for path in paths))
    grouped: dict[str, list[MediaCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(str(Path(candidate.path).resolve()), []).append(candidate)

    picks: list[MediaCandidate] = []
    for path in ordered_paths:
        ranked = sorted(
            grouped.get(path, []),
            key=_maximum_candidate_score,
            reverse=True,
        )
        if not ranked:
            continue
        take = 2 if any(candidate.kind == "video" for candidate in ranked) else 1
        picks.extend(ranked[:take])
    if not picks:
        picks = sorted(candidates, key=_maximum_candidate_score, reverse=True)[:8]

    scores = sorted(_maximum_candidate_score(candidate) for candidate in picks)
    threshold = max(0.58, _percentile(scores, 0.45)) if len(scores) >= 8 else min(scores)
    strong_count = sum(score >= threshold for score in scores)
    source_count = max(1, len(ordered_paths))
    shot_count = max(min(source_count, 6), min(44, strong_count))
    if audio_map.bpm >= 132:
        seconds_per_shot = 1.25
    elif audio_map.bpm >= 106:
        seconds_per_shot = 1.5
    else:
        seconds_per_shot = 1.85
    target = 6.0 + shot_count * seconds_per_shot
    minimum = min(audio_map.duration, 12.0)
    maximum = min(audio_map.duration, 90.0)
    return round(max(minimum, min(maximum, target)), 1)


def _maximum_candidate_score(candidate: MediaCandidate) -> float:
    duration_fit = 1.0 if candidate.kind == "photo" else min(1.0, candidate.duration / 2.8)
    action = max(candidate.intensity, candidate.action_score)
    return (
        0.48 * candidate.quality
        + 0.18 * action
        + 0.14 * candidate.stability
        + 0.08 * candidate.face_score
        + 0.12 * duration_fit
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = round((len(values) - 1) * percentile)
    return values[index]


def _variant_duration(variant: ReelVariant) -> float:
    return max(
        (float(item.get("timeline_end", 0.0)) for item in variant.timeline_json),
        default=0.0,
    )


def _owned_project(session, user_id: str, project_id: str) -> Project:
    project = session.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if not project:
        raise ValueError("Project not found")
    return project


def _capture_sort_key(row) -> tuple[int, float, int]:
    association, asset, stored = row
    metadata = dict(asset.metadata_json or {})
    captured_at = metadata.get("captured_at")
    if not captured_at:
        captured_at = media_capture_time(Path(stored.path), asset.media_type)
        if captured_at:
            metadata["captured_at"] = captured_at
            asset.metadata_json = metadata
    if not captured_at:
        return (1, 0.0, association.sort_order)
    try:
        captured = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=UTC)
        return (0, captured.timestamp(), association.sort_order)
    except ValueError:
        return (1, 0.0, association.sort_order)


def _filter_media_rows(rows, media_filter: str):
    if media_filter == "all":
        return rows
    return [row for row in rows if row[1].media_type == media_filter]


def project_payload(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "duration": project.duration,
        "include_all": project.include_all,
        "media_filter": project.media_filter,
        "media_order": project.media_order,
        "random_seed": project.random_seed,
        "min_clip_duration": project.min_clip_duration,
        "processing_profile": project.processing_profile,
        "music_track_id": project.music_track_id,
        "music_start": project.music_start,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _relative_debug_path(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except (OSError, ValueError):
        return path.name


def _sanitize_debug_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sanitize_debug_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_debug_value(item) for item in value]
    if isinstance(value, str) and Path(value).expanduser().is_absolute():
        return _relative_debug_path(value)
    return value
