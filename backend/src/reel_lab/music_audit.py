"""Batch checks for local beat grids across the ready music library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from reel_lab.audio import select_highlights, slice_audio_map
from reel_lab.config import MUSIC_DIR
from reel_lab.db import session_scope
from reel_lab.db.models import MusicTrack
from reel_lab.models import AudioMap
from reel_lab.system import write_json

AUDIT_CONFIDENCE_THRESHOLD = 0.5


def audit_music_library(
    output: Path,
    *,
    window_duration: float = 15.0,
    window_step: float = 30.0,
    workers: int = 4,
) -> dict[str, Any]:
    """Check rolling excerpts for every ready track and write a JSON report."""
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(MusicTrack)
                .where(MusicTrack.status == "ready")
                .order_by(MusicTrack.sort_order, MusicTrack.title)
            ).all()
        )
        tracks = [
            {
                "id": track.id,
                "title": track.title,
                "artist": track.metadata_json.get("artist"),
                "analysis": dict(track.analysis_json),
            }
            for track in rows
            if track.analysis_json
        ]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        results = list(
            executor.map(
                lambda track: _audit_track(track, window_duration, window_step),
                tracks,
            )
        )

    review = [result for result in results if result["status"] != "ok"]
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "settings": {
            "window_duration": window_duration,
            "window_step": window_step,
            "confidence_threshold": AUDIT_CONFIDENCE_THRESHOLD,
        },
        "summary": {
            "tracks": len(results),
            "ok": len(results) - len(review),
            "review": len(review),
            "windows": sum(len(result["windows"]) for result in results),
        },
        "tracks": results,
    }
    write_json(output.expanduser().resolve(), payload)
    return payload


def audit_window_starts(
    track_duration: float,
    window_duration: float,
    window_step: float,
    highlight_start: float | None = None,
) -> list[float]:
    """Cover the full track and always include its automatic highlight."""
    duration = min(max(0.0, window_duration), max(0.0, track_duration))
    limit = max(0.0, track_duration - duration)
    starts = [0.0, limit]
    current = window_step
    while current < limit:
        starts.append(current)
        current += max(window_step, 1.0)
    if highlight_start is not None:
        starts.append(min(limit, max(0.0, highlight_start)))
    return sorted({round(start, 3) for start in starts})


def _audit_track(
    track: dict[str, Any],
    window_duration: float,
    window_step: float,
) -> dict[str, Any]:
    audio_map = AudioMap.from_dict(track["analysis"])
    normalized = MUSIC_DIR / str(track["id"]) / "analysis" / "track.wav"
    if normalized.is_file():
        audio_map.normalized_path = str(normalized)
    highlight = next(
        iter(select_highlights(audio_map, durations=(window_duration,)).values()),
        None,
    )
    starts = audit_window_starts(
        audio_map.duration,
        window_duration,
        window_step,
        float(highlight["start"]) if highlight else None,
    )
    windows = []
    for start in starts:
        try:
            local = slice_audio_map(audio_map, start, window_duration)
        except (OSError, RuntimeError, ValueError) as error:
            windows.append({"start": start, "status": "error", "error": str(error)})
            continue
        status = (
            "ok"
            if local.beat_confidence >= AUDIT_CONFIDENCE_THRESHOLD and len(local.beats) >= 2
            else "review"
        )
        windows.append(
            {
                "start": start,
                "duration": local.duration,
                "status": status,
                "bpm": local.bpm,
                "confidence": local.beat_confidence,
                "beats": len(local.beats),
            }
        )
    status = "ok" if windows and all(window["status"] == "ok" for window in windows) else "review"
    return {
        "id": track["id"],
        "title": track["title"],
        "artist": track["artist"],
        "status": status,
        "global_bpm": audio_map.bpm,
        "windows": windows,
    }
