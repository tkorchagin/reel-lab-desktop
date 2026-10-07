"""Compare real visual cuts with automatically detected music events."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

from reel_lab.media import detect_scene_boundaries
from reel_lab.models import AudioMap
from reel_lab.system import write_json


def benchmark_reference(video: Path, audio_map: AudioMap) -> dict[str, object]:
    """Measure how closely visual cuts align with audio markers."""
    visual_cuts = detect_scene_boundaries(video, threshold=24.0)
    beat_offsets = _nearest_offsets(visual_cuts, audio_map.beats)
    downbeat_offsets = _nearest_offsets(visual_cuts, audio_map.downbeats)
    section_markers = [section.start for section in audio_map.sections[1:]]
    section_offsets = _nearest_offsets(visual_cuts, section_markers)

    details = []
    for index, cut in enumerate(visual_cuts):
        beat_offset = beat_offsets[index] if index < len(beat_offsets) else None
        downbeat_offset = downbeat_offsets[index] if index < len(downbeat_offsets) else None
        section_offset = section_offsets[index] if index < len(section_offsets) else None
        label = "off_grid"
        if section_offset is not None and abs(section_offset) <= 0.35:
            label = "section"
        elif downbeat_offset is not None and abs(downbeat_offset) <= 0.18:
            label = "downbeat"
        elif beat_offset is not None and abs(beat_offset) <= 0.12:
            label = "beat"
        details.append(
            {
                "time": round(cut, 4),
                "label": label,
                "beat_offset_ms": _milliseconds(beat_offset),
                "downbeat_offset_ms": _milliseconds(downbeat_offset),
                "section_offset_ms": _milliseconds(section_offset),
            }
        )

    summary = {
        "duration": audio_map.duration,
        "visual_cut_count": len(visual_cuts),
        "cuts_per_minute": round(len(visual_cuts) / max(audio_map.duration / 60, 0.01), 2),
        "beat_hit_rate_120ms": _hit_rate(beat_offsets, 0.12),
        "beat_hit_rate_250ms": _hit_rate(beat_offsets, 0.25),
        "downbeat_hit_rate_180ms": _hit_rate(downbeat_offsets, 0.18),
        "section_hit_rate_350ms": _hit_rate(section_offsets, 0.35),
        "median_beat_offset_ms": _median_absolute_ms(beat_offsets),
    }
    return {
        "source_video": str(video.resolve()),
        "analyzer": audio_map.analyzer,
        "bpm": audio_map.bpm,
        "summary": summary,
        "cuts": details,
        "beats": audio_map.beats,
        "downbeats": audio_map.downbeats,
        "sections": [section.__dict__ for section in audio_map.sections],
    }


def create_benchmark_report(
    output_dir: Path,
    video: Path,
    audio_map: AudioMap,
    benchmark: dict[str, object],
) -> Path:
    """Create an interactive local report for the reference edit."""
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = output_dir / "media"
    assets.mkdir(exist_ok=True)
    digest = hashlib.sha1(str(video.resolve()).encode(), usedforsecurity=False).hexdigest()[:12]
    video_target = assets / f"reference-{digest}{video.suffix.lower()}"
    track_target = assets / "track.wav"
    _link(video, video_target)
    _link(Path(audio_map.normalized_path), track_target)

    data = dict(benchmark)
    data["video_url"] = f"media/{video_target.name}"
    data["track_url"] = "media/track.wav"
    write_json(output_dir / "benchmark.json", benchmark)
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = environment.get_template("benchmark.html").render(data=data)
    report = output_dir / "index.html"
    report.write_text(html, encoding="utf-8")
    return report


def _nearest_offsets(events: list[float], markers: list[float]) -> list[float | None]:
    if not markers:
        return [None] * len(events)
    marker_array = np.asarray(sorted(markers), dtype=float)
    offsets: list[float | None] = []
    for event in events:
        index = int(np.searchsorted(marker_array, event))
        choices = marker_array[max(0, index - 1) : min(len(marker_array), index + 1)]
        nearest = min(choices, key=lambda marker: abs(float(marker) - event))
        offsets.append(round(event - float(nearest), 6))
    return offsets


def _hit_rate(offsets: list[float | None], threshold: float) -> float:
    valid = [value for value in offsets if value is not None]
    if not valid:
        return 0.0
    return round(sum(abs(value) <= threshold for value in valid) / len(valid), 4)


def _median_absolute_ms(offsets: list[float | None]) -> float:
    valid = [abs(value) * 1000 for value in offsets if value is not None]
    return round(float(np.median(valid)), 2) if valid else 0.0


def _milliseconds(value: float | None) -> float | None:
    return round(value * 1000, 2) if value is not None else None


def _link(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        return
    try:
        target.symlink_to(source.resolve())
    except OSError:
        shutil.copy2(source, target)
