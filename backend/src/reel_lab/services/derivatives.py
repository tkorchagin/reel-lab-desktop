"""Browser previews, thumbnails, and media metadata."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, ImageOps
from pillow_heif import register_heif_opener

from reel_lab.config import DERIVATIVES_DIR
from reel_lab.render import color_filter_for_source
from reel_lab.system import probe_media, run_command

register_heif_opener()


def media_metadata(source: Path, media_type: str) -> dict[str, Any]:
    """Extract the small metadata set needed by the library UI and color pipeline."""
    if media_type == "photo":
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
        result: dict[str, Any] = {"width": width, "height": height, "duration": 0.0}
        captured_at = media_capture_time(source, media_type)
        if captured_at:
            result["captured_at"] = captured_at
        return result

    metadata = probe_media(source)
    stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if not stream:
        return {"duration": float(metadata.get("format", {}).get("duration", 0.0) or 0.0)}
    result = {
        "width": int(stream.get("width", 0) or 0),
        "height": int(stream.get("height", 0) or 0),
        "duration": float(metadata.get("format", {}).get("duration", 0.0) or 0.0),
        "codec": stream.get("codec_name"),
        "pixel_format": stream.get("pix_fmt"),
        "color_range": stream.get("color_range"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "hdr": stream.get("color_transfer") in {"arib-std-b67", "smpte2084"},
    }
    captured_at = media_capture_time(source, media_type, metadata)
    if captured_at:
        result["captured_at"] = captured_at
    return result


def media_capture_time(
    source: Path,
    media_type: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Return the embedded capture time normalized to UTC when available."""
    if media_type == "photo":
        with Image.open(source) as opened:
            exif = opened.getexif()
            details = exif.get_ifd(ExifTags.IFD.Exif)
            value = details.get(36867) or details.get(36868) or exif.get(306)
            offset = details.get(36881) or details.get(36882) or details.get(36880)
        if not value:
            return None
        try:
            captured = datetime.strptime(str(value)[:19], "%Y:%m:%d %H:%M:%S")
            zone = datetime.strptime(str(offset), "%z").tzinfo if offset else _local_timezone()
            return captured.replace(tzinfo=zone).astimezone(UTC).isoformat()
        except ValueError:
            return None

    metadata = metadata or probe_media(source)
    video_stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "video"),
        {},
    )
    value = metadata.get("format", {}).get("tags", {}).get("creation_time")
    value = value or video_stream.get("tags", {}).get("creation_time")
    if not value:
        return None
    try:
        captured = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=_local_timezone())
        return captured.astimezone(UTC).isoformat()
    except ValueError:
        return None


def _local_timezone():
    return datetime.now().astimezone().tzinfo or UTC


def create_derivatives(source: Path, sha256: str, media_type: str) -> tuple[Path, Path]:
    """Create one reusable browser proxy and thumbnail for a stored object."""
    target_dir = DERIVATIVES_DIR / sha256
    target_dir.mkdir(parents=True, exist_ok=True)
    thumbnail = target_dir / "thumbnail.jpg"
    if media_type == "photo":
        proxy = target_dir / "preview.jpg"
        if not proxy.exists() or not thumbnail.exists():
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                preview_image = image.copy()
                preview_image.thumbnail((1800, 1800))
                preview_image.save(proxy, quality=91, optimize=True)
                image.thumbnail((360, 360))
                image.save(thumbnail, quality=84, optimize=True)
        return proxy, thumbnail

    proxy = target_dir / "proxy.mp4"
    if not proxy.exists() or proxy.stat().st_size == 0:
        temporary = target_dir / "proxy.partial.mp4"
        color_filter = color_filter_for_source(source)
        filters = [color_filter] if color_filter else []
        filters.append("scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2")
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
        temporary.replace(proxy)
    if not thumbnail.exists():
        run_command(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                "0.4",
                "-i",
                str(proxy),
                "-frames:v",
                "1",
                "-vf",
                "scale=360:360:force_original_aspect_ratio=decrease",
                str(thumbnail),
            ]
        )
    return proxy, thumbnail


def copy_existing_derivative(source: Path, destination: Path) -> None:
    """Copy a valid existing local derivative into managed storage."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
