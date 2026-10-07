"""Generate a self-contained local browser preview for edit variants."""

from __future__ import annotations

import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps

from reel_lab.models import AudioMap, Variant
from reel_lab.render import color_filter_for_source
from reel_lab.system import probe_media, read_json, run_command

BROWSER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_ASSET_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".3gp"}
BROWSER_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}


def create_preview(run_dir: Path, audio_map: AudioMap, variants: list[Variant]) -> Path:
    """Create the local static viewer and return its HTML path."""
    preview_dir = run_dir / "preview"
    media_dir = preview_dir / "media"
    cache_dir = run_dir.parent / ".preview-assets"
    media_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    track_source = Path(audio_map.normalized_path)
    track_target = media_dir / "track.wav"
    _link(track_source, track_target)

    path_values = sorted({item.path for variant in variants for item in variant.items})
    asset_urls: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(path_values)))) as executor:
        futures = {
            executor.submit(
                _prepare_browser_asset, Path(path_value), media_dir, cache_dir
            ): path_value
            for path_value in path_values
        }
        for future in as_completed(futures):
            asset_urls[futures[future]] = future.result()

    variant_data = []
    for variant in variants:
        data = variant.to_dict()
        for item in data["items"]:
            item["asset_url"] = asset_urls[item["path"]]
            item["source_name"] = Path(item["path"]).name
        variant_data.append(data)

    preview_data = {
        "duration": audio_map.duration,
        "bpm": audio_map.bpm,
        "analyzer": audio_map.analyzer,
        "track_url": "media/track.wav",
        "energy": [point.__dict__ for point in audio_map.energy],
        "sections": [section.__dict__ for section in audio_map.sections],
        "beats": audio_map.beats,
        "downbeats": audio_map.downbeats,
        "variants": variant_data,
    }
    template_dir = Path(__file__).parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    html = environment.get_template("preview.html").render(preview_data=preview_data)
    output = preview_dir / "index.html"
    output.write_text(html, encoding="utf-8")
    return output


def create_collection(output_dir: Path, runs: list[Path]) -> Path:
    """Create a track selector that embeds multiple variant previews."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for raw_run in runs:
        run = raw_run.expanduser().resolve()
        audio_map = read_json(run / "audio-map.json")
        variants = read_json(run / "variants.json")
        manifest = read_json(run / "manifest.json")
        preview = run / "preview" / "index.html"
        track_key = Path(manifest["track"]).stem
        entries.append(
            {
                "name": run.name,
                "track_key": track_key,
                "track_title": _track_title(track_key),
                "bpm": audio_map["bpm"],
                "duration": audio_map["duration"],
                "variant_count": len(variants),
                "best_variant": max(variants, key=lambda item: item["score"])["id"],
                "include_all": bool(manifest.get("ordered_all", False)),
                "include_photos": bool(manifest.get("include_photos", True)),
                "preserve_order": bool(manifest.get("preserve_order", True)),
                "preview_url": Path(os.path.relpath(preview.parent, output_dir)).as_posix() + "/",
            }
        )
    tracks = list(
        {
            entry["track_key"]: {
                "key": entry["track_key"],
                "title": entry["track_title"],
                "bpm": entry["bpm"],
            }
            for entry in entries
        }.values()
    )
    template_dir = Path(__file__).parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    html = environment.get_template("collection.html").render(entries=entries, tracks=tracks)
    output = output_dir / "index.html"
    output.write_text(html, encoding="utf-8")
    return output


def _track_title(stem: str) -> str:
    known = {
        "01-short-loop": "Short demo",
        "02-medium-energy": "Second demo",
        "03-whats-up-danger": "What's Up Danger",
    }
    return known.get(stem, stem.replace("-", " ").strip().title())


def _prepare_browser_asset(source: Path, media_dir: Path, cache_dir: Path) -> str:
    media_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(source).encode(), usedforsecurity=False).hexdigest()[:12]
    suffix = source.suffix.lower()
    if suffix in BROWSER_IMAGE_EXTENSIONS:
        target = media_dir / f"{digest}{suffix}"
        _link(source, target)
        return f"media/{target.name}"
    if suffix in {".heic", ".heif", ".tif", ".tiff"}:
        cached = cache_dir / f"{digest}.jpg"
        if not cached.exists():
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail((1600, 1600))
                image.save(cached, quality=88, optimize=True)
        target = media_dir / cached.name
        _link(cached, target)
        return f"media/{target.name}"
    if suffix in VIDEO_ASSET_EXTENSIONS:
        if _is_browser_safe_video(source):
            target = media_dir / f"{digest}{suffix}"
            _link(source, target)
            return f"media/{target.name}"
        cached = cache_dir / f"{digest}.mp4"
        if not cached.exists() or cached.stat().st_size == 0:
            temporary = cache_dir / f"{digest}.partial.mp4"
            color_filter = color_filter_for_source(source)
            filters = [color_filter] if color_filter else []
            filters.append(
                "scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2"
            )
            run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-vf",
                    ",".join(filters),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "24",
                    "-pix_fmt",
                    "yuv420p",
                    "-color_primaries",
                    "bt709",
                    "-color_trc",
                    "bt709",
                    "-colorspace",
                    "bt709",
                    "-color_range",
                    "tv",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ]
            )
            temporary.replace(cached)
        target = media_dir / cached.name
        _link(cached, target)
        return f"media/{target.name}"
    target = media_dir / f"{digest}{suffix}"
    _link(source, target)
    return f"media/{target.name}"


def _is_browser_safe_video(source: Path) -> bool:
    metadata = probe_media(source)
    stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    return bool(stream and stream.get("codec_name") in BROWSER_VIDEO_CODECS)


def _link(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        return
    try:
        target.symlink_to(source.resolve())
    except OSError:
        shutil.copy2(source, target)
