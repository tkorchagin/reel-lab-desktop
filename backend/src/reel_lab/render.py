"""Render one selected edit decision list with FFmpeg."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps

from reel_lab.models import AudioMap, TimelineItem, Variant
from reel_lab.system import probe_media, run_command

ProgressCallback = Callable[[int, int], None]
RENDER_CACHE_VERSION = "smart-crop-v6-1080p-crf18"


def render_variant(
    run_dir: Path,
    variant: Variant,
    audio_map: AudioMap,
    output: Path,
    *,
    width: int = 1080,
    height: int = 1920,
    progress: ProgressCallback | None = None,
) -> Path:
    """Render a vertical H.264 reel and return the final path."""
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "render" / f"variant-{variant.id:02d}-{RENDER_CACHE_VERSION}"
    segment_dir = work_dir / "segments"
    asset_dir = work_dir / "assets"
    segment_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    prepared_photos: dict[str, Path] = {}
    jobs = []
    for index, item in enumerate(variant.items):
        segment_path = segment_dir / f"{index:04d}.mp4"
        source = _prepare_source(Path(item.path), asset_dir, prepared_photos)
        jobs.append((index, item, source, segment_path))

    worker_count = max(1, min(4, (os.cpu_count() or 4) // 2))
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _render_item,
                item,
                source,
                segment_path,
                width,
                height,
            ): index
            for index, item, source, segment_path in jobs
        }
        for future in as_completed(futures):
            future.result()
            completed += 1
            if progress:
                progress(completed, len(jobs))

    concat_file = work_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{_concat_escape(path.resolve())}'" for _, _, _, path in jobs) + "\n",
        encoding="utf-8",
    )
    silent_video = work_dir / "silent.mp4"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent_video),
        ]
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(silent_video),
            "-ss",
            f"{audio_map.source_start:.4f}",
            "-i",
            audio_map.source_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_metadata=video_full_range_flag=0:colour_primaries=1:"
            "transfer_characteristics=1:matrix_coefficients=1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-af",
            "loudnorm=I=-14:LRA=11:TP=-1.5",
            "-t",
            f"{audio_map.duration:.4f}",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    metadata = probe_media(output)
    if not metadata.get("streams"):
        raise RuntimeError("FFmpeg produced an empty file")
    return output


def _render_item(
    item: TimelineItem,
    source: Path,
    output: Path,
    width: int,
    height: int,
) -> None:
    if output.exists() and output.stat().st_size > 0:
        return
    duration = max(0.05, item.duration)
    input_args = []
    if item.kind == "photo":
        input_args.extend(["-loop", "1", "-t", f"{duration:.4f}", "-i", str(source)])
    else:
        input_args.extend(
            [
                "-stream_loop",
                "-1",
                "-ss",
                f"{item.source_start:.4f}",
                "-t",
                f"{duration:.4f}",
                "-i",
                str(source),
            ]
        )

    filter_graph = _video_filter(
        item,
        width,
        height,
        duration,
        color_filter=color_filter_for_source(source) if item.kind == "video" else None,
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            *input_args,
            "-an",
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
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
            "-video_track_timescale",
            "90000",
            str(output),
        ]
    )


def _video_filter(
    item: TimelineItem,
    width: int,
    height: int,
    duration: float,
    *,
    color_filter: str | None = None,
) -> str:
    crop_x = f"max(0,min(iw-{width},iw*{item.focus_x:.5f}-{width}/2))"
    crop_y = f"max(0,min(ih-{height},ih*{item.focus_y:.5f}-{height}/2))"
    color_prefix = f"{color_filter}," if color_filter else ""
    graph = (
        f"[0:v]{color_prefix}scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:x='{crop_x}':y='{crop_y}',setsar=1"
    )
    effects = ["fps=30"]
    if item.kind == "photo":
        zoom_step = 0.10 / max(1.0, duration * 30.0)
        if item.motion == "zoom-out":
            zoom = f"if(eq(on,0),1.10,max(1.0,zoom-{zoom_step:.7f}))"
        else:
            zoom = f"min(zoom+{zoom_step:.7f},1.10)"
        effects.append(
            f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
        )
    effects.extend(
        [
            "format=yuv420p",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "setpts=PTS-STARTPTS",
        ]
    )
    return f"{graph},{','.join(effects)}[out]"


def color_filter_for_source(source: Path) -> str | None:
    """Return an explicit conversion to the common SDR BT.709 render space."""
    metadata = probe_media(source)
    stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if not stream:
        return None
    transfer = str(stream.get("color_transfer") or "")
    primaries = str(stream.get("color_primaries") or "")
    matrix = str(stream.get("color_space") or "")
    if transfer in {"arib-std-b67", "smpte2084"}:
        return (
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )
    if (
        primaries
        and transfer
        and matrix
        and (primaries, transfer, matrix)
        != (
            "bt709",
            "bt709",
            "bt709",
        )
    ):
        return "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
    return None


def _prepare_source(source: Path, asset_dir: Path, cache: dict[str, Path]) -> Path:
    if source.suffix.lower() not in {".heic", ".heif", ".tif", ".tiff"}:
        return source
    key = str(source.resolve())
    if key in cache:
        return cache[key]
    target = asset_dir / f"photo-{len(cache):03d}.jpg"
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.save(target, quality=94)
    cache[key] = target
    return target


def _concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def clean_render_cache(run_dir: Path, variant_id: int) -> None:
    """Remove recoverable intermediates for a rendered variant."""
    target = run_dir / "render" / f"variant-{variant_id:02d}"
    if target.exists():
        shutil.rmtree(target)
