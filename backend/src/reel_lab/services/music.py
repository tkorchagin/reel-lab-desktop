"""Music presets, uploads, analysis, and reel-length highlights."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copyfile

from sqlalchemy import or_, select

from reel_lab.audio import analyze_track, select_highlights
from reel_lab.config import AUDIO_LIBRARY_DIR, MUSIC_DIR
from reel_lab.db import session_scope
from reel_lab.db.models import MusicTrack, ProcessingJob
from reel_lab.models import AudioMap
from reel_lab.system import read_json, run_command

SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
MUSIC_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reel-lab-music")
HIGHLIGHT_CACHE_VERSION = "section-lift-v2"
BUNDLED_DEMO_DIR = Path(__file__).resolve().parents[1] / "assets" / "demo-music"


def seed_local_presets() -> None:
    """Synchronize audio-library files with global preset rows."""
    _seed_demo_library()
    pending: list[str] = []
    with session_scope() as session:
        existing_presets = list(
            session.scalars(select(MusicTrack).where(MusicTrack.is_preset.is_(True))).all()
        )
        by_source = {track.source_path: track for track in existing_presets}
        by_filename = {
            str(track.metadata_json.get("filename")): track
            for track in existing_presets
            if track.metadata_json.get("filename")
        }
        by_library_path = {
            str(track.metadata_json.get("library_path")): track
            for track in existing_presets
            if track.metadata_json.get("library_path")
        }
        by_catalog_id = {
            str(track.metadata_json.get("catalog_id")): track
            for track in existing_presets
            if track.metadata_json.get("catalog_id")
        }
        by_legacy_title = {
            track.title: track
            for track in existing_presets
            if not track.metadata_json.get("filename")
            and not track.metadata_json.get("library_path")
        }

        for entry in _library_entries():
            source = Path(entry["path"])
            title = str(entry["title"])
            metadata = dict(entry["metadata"])
            catalog_id = str(metadata.get("catalog_id", ""))
            track = (
                (by_catalog_id.get(catalog_id) if catalog_id else None)
                or by_source.get(str(source))
                or by_library_path.get(str(metadata.get("library_path")))
                or by_filename.get(source.name)
                or by_legacy_title.get(title)
            )
            if track is None:
                track = MusicTrack(
                    title=title,
                    source_path=str(source),
                    source_kind="audio",
                    is_preset=True,
                    status="queued",
                    stage="Queued for analysis",
                )
                session.add(track)
                session.flush()
            track.title = title
            track.source_path = str(source)
            track.source_kind = "audio"
            track.is_preset = True
            track.metadata_json = metadata
            track.sort_order = int(entry["sort_order"])
            if track.analysis_json and track.status == "ready":
                audio_map = AudioMap.from_dict(track.analysis_json)
                audio_map.source_path = str(source)
                normalized = MUSIC_DIR / track.id / "analysis" / "track.wav"
                audio_map.normalized_path = str(normalized if normalized.is_file() else source)
                track.analysis_json = audio_map.to_dict()
                _ensure_highlight_cache(track)
                track.preview_path = str(source)
            else:
                track.status = "queued"
                track.progress = 0
                track.stage = "Queued for analysis"
                pending.append(track.id)

            by_source[str(source)] = track
            if catalog_id:
                by_catalog_id[catalog_id] = track
            by_library_path[str(metadata.get("library_path"))] = track
            by_filename[source.name] = track

    for track_id in pending:
        MUSIC_EXECUTOR.submit(_process_music, None, track_id)


def _seed_demo_library() -> None:
    """Copy the bundled demo into an empty library; preserve existing audio and catalogs."""
    if not BUNDLED_DEMO_DIR.is_dir():
        return
    if any(
        path.is_file()
        and (path.name == "catalog.json" or path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS)
        for path in AUDIO_LIBRARY_DIR.rglob("*")
    ):
        return
    destination_root = AUDIO_LIBRARY_DIR / "demo"
    for source in BUNDLED_DEMO_DIR.rglob("*"):
        if not source.is_file():
            continue
        destination = destination_root / source.relative_to(BUNDLED_DEMO_DIR)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, destination)


def _library_entries() -> list[dict[str, object]]:
    root = AUDIO_LIBRARY_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    configured: dict[str, dict[str, object]] = {}
    for catalog_path in sorted(root.rglob("catalog.json")):
        catalog = read_json(catalog_path)
        catalog_root = catalog_path.parent
        for index, raw in enumerate(catalog.get("tracks", []), start=1):
            filename = str(raw.get("file", "")).strip()
            source = (catalog_root / filename).resolve()
            if not filename or not source.is_relative_to(root) or not source.is_file():
                continue
            metadata = {
                key: raw[key]
                for key in (
                    "artist",
                    "description",
                    "tags",
                    "chart",
                    "chart_rank",
                    "chart_date",
                    "chart_source",
                    "source",
                    "service_url",
                    "match_score",
                    "genres",
                    "genre_sources",
                    "shazam_id",
                    "shazam_url",
                    "release_date",
                    "explicit",
                    "version",
                )
                if raw.get(key) is not None
            }
            metadata["filename"] = source.name
            library_path = source.relative_to(root).as_posix()
            metadata["library_path"] = library_path
            catalog_id = str(raw.get("id", "")).strip()
            if catalog_id:
                metadata["catalog_id"] = catalog_id
            configured[library_path] = {
                "path": source,
                "title": str(raw.get("title") or _title_from_path(source)),
                "metadata": metadata,
                "sort_order": int(raw.get("order", index * 10)),
            }

    entries = list(configured.values())
    next_order = max((int(entry["sort_order"]) for entry in entries), default=0) + 10
    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        library_path = source.relative_to(root).as_posix()
        if library_path in configured:
            continue
        entries.append(
            {
                "path": source.resolve(),
                "title": _title_from_path(source),
                "metadata": {
                    "filename": source.name,
                    "library_path": library_path,
                },
                "sort_order": next_order,
            }
        )
        next_order += 10
    return sorted(entries, key=lambda entry: int(entry["sort_order"]))


def _title_from_path(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip().title()


def list_music(user_id: str) -> list[MusicTrack]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(MusicTrack)
                .where(or_(MusicTrack.user_id == user_id, MusicTrack.user_id.is_(None)))
                .order_by(
                    MusicTrack.is_preset.desc(),
                    MusicTrack.sort_order,
                    MusicTrack.created_at.desc(),
                )
            ).all()
        )


def start_music_processing(user_id: str, track_id: str) -> str:
    with session_scope() as session:
        track = session.scalar(
            select(MusicTrack).where(MusicTrack.id == track_id, MusicTrack.user_id == user_id)
        )
        if not track:
            raise ValueError("Track not found")
        job = ProcessingJob(
            user_id=user_id,
            kind="music",
            stage="Preparing audio",
            result_json={"track_id": track_id},
        )
        session.add(job)
        session.flush()
        job_id = job.id
    MUSIC_EXECUTOR.submit(_process_music, job_id, track_id)
    return job_id


def _process_music(job_id: str | None, track_id: str) -> None:
    try:
        with session_scope() as session:
            track = session.get(MusicTrack, track_id)
            job = session.get(ProcessingJob, job_id) if job_id else None
            if not track or (job_id and not job):
                return
            track.status = "processing"
            track.progress = 8
            track.stage = "Analyzing music"
            if job:
                job.status = "processing"
                job.progress = 8
                job.stage = track.stage
            source = Path(track.source_path)

        work_dir = MUSIC_DIR / track_id
        audio_map = analyze_track(source, work_dir / "analysis", analyzer="auto")
        with session_scope() as session:
            track = session.get(MusicTrack, track_id)
            job = session.get(ProcessingJob, job_id) if job_id else None
            if track:
                track.progress = 82
                track.stage = "Preparing audio preview"
            if job:
                job.progress = 82
                job.stage = "Preparing audio preview"

        preview = work_dir / "preview.m4a"
        preview.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(preview),
            ]
        )
        with session_scope() as session:
            track = session.get(MusicTrack, track_id)
            job = session.get(ProcessingJob, job_id) if job_id else None
            if track:
                track.preview_path = str(preview)
                track.duration = audio_map.duration
                track.bpm = audio_map.bpm
                track.analysis_json = audio_map.to_dict()
                track.highlights_json = _highlight_cache(audio_map)
                track.status = "ready"
                track.progress = 100
                track.stage = "Ready"
                track.error = None
            if job:
                job.status = "ready"
                job.progress = 100
                job.stage = "Music ready"
    except Exception as error:
        with session_scope() as session:
            track = session.get(MusicTrack, track_id)
            job = session.get(ProcessingJob, job_id) if job_id else None
            if track:
                track.status = "error"
                track.error = str(error)
                track.stage = "Analysis failed"
            if job:
                job.status = "error"
                job.error = str(error)
                job.stage = "Analysis failed"


def _ensure_highlight_cache(track: MusicTrack) -> None:
    if (
        track.status != "ready"
        or not track.analysis_json
        or _highlight_cache_is_current(track.highlights_json)
    ):
        return
    track.highlights_json = _highlight_cache(AudioMap.from_dict(track.analysis_json))


def _highlight_cache(audio_map: AudioMap) -> dict[str, object]:
    highlights: dict[str, object] = select_highlights(audio_map)
    highlights["_version"] = HIGHLIGHT_CACHE_VERSION
    return highlights


def _highlight_cache_is_current(highlights: dict[str, object]) -> bool:
    return bool(highlights) and highlights.get("_version") == HIGHLIGHT_CACHE_VERSION
