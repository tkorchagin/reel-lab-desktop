"""Command-line interface for Reel Lab."""

from __future__ import annotations

import functools
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from reel_lab.audio import analyze_track
from reel_lab.benchmark import benchmark_reference, create_benchmark_report
from reel_lab.media import analyze_media, discover_media
from reel_lab.models import AudioMap, MediaCandidate, Variant
from reel_lab.music_audit import audit_music_library
from reel_lab.preview import create_collection, create_preview
from reel_lab.render import render_variant
from reel_lab.server import RangeHTTPServer, RangeRequestHandler
from reel_lab.system import read_json, require_media_tools, write_json
from reel_lab.timeline import generate_ordered_variants, generate_variants

app = typer.Typer(
    help="Automatic music analysis, edit variations, and reel export.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


@app.command("audit-music")
def audit_music(
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="JSON report for the music library audit."),
    ] = Path("data/music-beat-audit.json"),
    window_duration: Annotated[
        float,
        typer.Option(min=5.0, max=60.0, help="Length of each audit window in seconds."),
    ] = 15.0,
    window_step: Annotated[
        float,
        typer.Option(min=5.0, max=120.0, help="Step between audit windows in seconds."),
    ] = 30.0,
    workers: Annotated[int, typer.Option(min=1, max=8)] = 4,
) -> None:
    """Audit local beat confidence across every ready catalog track."""
    result = audit_music_library(
        out,
        window_duration=window_duration,
        window_step=window_step,
        workers=workers,
    )
    summary = result["summary"]
    console.print(
        f"[bold green]Audited:[/] {summary['tracks']} tracks, {summary['windows']} windows; "
        f"OK {summary['ok']}, review {summary['review']}"
    )
    console.print(f"Report: [link=file://{out.expanduser().resolve()}]{out.expanduser().resolve()}[/link]")


@app.command()
def build(
    inputs: Annotated[list[Path], typer.Argument(help="Photo and video files or directories.")],
    track: Annotated[
        Path, typer.Option("--track", "-t", help="Audio or video containing the music track.")
    ],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Edit project directory.")] = None,
    variants: Annotated[int, typer.Option(min=1, max=30, help="Number of edit variants.")] = 4,
    analyzer: Annotated[str, typer.Option(help="auto, librosa, allin1, or beat-this.")] = "auto",
    media_cache: Annotated[
        Path | None,
        typer.Option(
            "--media-cache",
            help="Existing media.json to reuse source analysis.",
        ),
    ] = None,
    ordered_all: Annotated[
        bool,
        typer.Option(
            "--ordered-all",
            help="Use each source exactly once, keeping file order.",
        ),
    ] = False,
    ordered_best: Annotated[
        bool,
        typer.Option(
            "--ordered-best",
            help="Choose the best sources across the timeline, keeping their order.",
        ),
    ] = False,
    include_photos: Annotated[
        bool,
        typer.Option("--photos/--no-photos", help="Include photos."),
    ] = True,
    preserve_order: Annotated[
        bool,
        typer.Option(
            "--preserve-order/--free-order",
            help="Keep selected media in chronological order.",
        ),
    ] = True,
) -> None:
    """Build music markup, media candidates, variants, and an HTML preview."""
    require_media_tools()
    if ordered_all and ordered_best:
        raise typer.BadParameter("Choose one mode: --ordered-all or --ordered-best")
    run_dir = _run_directory(out, track)
    run_dir.mkdir(parents=True, exist_ok=True)

    with console.status("[bold]Analyzing music…"):
        audio_map = analyze_track(track, run_dir / "audio", analyzer=analyzer)
    write_json(run_dir / "audio-map.json", audio_map.to_dict())
    console.print(
        f"[green]Music:[/] {audio_map.bpm:.1f} BPM, "
        f"{len(audio_map.beats)} beats, {len(audio_map.sections)} sections"
    )

    media_paths = discover_media(inputs)
    if media_cache:
        cached = read_json(media_cache.expanduser().resolve())
        all_candidates = [MediaCandidate(**item) for item in cached]
        console.print(f"[green]Sources:[/] cached analysis, {len(all_candidates)} candidates")
    else:
        with console.status(f"[bold]Analyzing {len(media_paths)} media files…"):
            all_candidates = analyze_media(media_paths)
        console.print(
            f"[green]Sources:[/] {len(media_paths)} files, {len(all_candidates)} candidates"
        )
    write_json(run_dir / "media.json", [candidate.to_dict() for candidate in all_candidates])

    selected_paths = [
        path
        for path in media_paths
        if include_photos
        or path.suffix.lower()
        not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
    ]
    selected_path_values = {str(path.resolve()) for path in selected_paths}
    candidates = [
        candidate
        for candidate in all_candidates
        if str(Path(candidate.path).resolve()) in selected_path_values
    ]

    if ordered_all or ordered_best:
        edit_variants = generate_ordered_variants(
            audio_map,
            candidates,
            selected_paths,
            count=variants,
            include_all=ordered_all,
            media_order="manual" if preserve_order else "random",
        )
    else:
        edit_variants = generate_variants(audio_map, candidates, count=variants)
    write_json(run_dir / "variants.json", [variant.to_dict() for variant in edit_variants])
    preview = create_preview(run_dir, audio_map, edit_variants)
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "track": str(track.expanduser().resolve()),
        "inputs": [str(path) for path in media_paths],
        "audio_map": "audio-map.json",
        "media_map": "media.json",
        "variants": "variants.json",
        "preview": "preview/index.html",
        "ordered_all": ordered_all,
        "ordered_best": ordered_best,
        "include_photos": include_photos,
        "preserve_order": preserve_order,
    }
    write_json(run_dir / "manifest.json", manifest)

    console.print(f"[bold green]Ready:[/] {len(edit_variants)} variants")
    console.print(f"Preview: [link=file://{preview}]{preview}[/link]")
    console.print(f"Project directory: {run_dir}")


@app.command()
def collect(
    runs: Annotated[
        list[Path], typer.Argument(help="Directories containing completed edit projects.")
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Collection viewer directory.")],
) -> None:
    """Create one browser screen for several music runs."""
    result = create_collection(out.expanduser().resolve(), runs)
    console.print(f"[bold green]Collection viewer:[/] {result}")


@app.command("export")
def export_variant(
    run: Annotated[Path, typer.Argument(help="Directory of an existing edit project.")],
    variant: Annotated[int, typer.Option("--variant", "-v", min=1, help="Variant number.")] = 1,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output MP4 path.")
    ] = None,
) -> None:
    """Render one selected variant to a vertical MP4."""
    require_media_tools()
    run_dir = run.expanduser().resolve()
    audio_map = AudioMap.from_dict(read_json(run_dir / "audio-map.json"))
    variants = [Variant.from_dict(item) for item in read_json(run_dir / "variants.json")]
    selected = next((item for item in variants if item.id == variant), None)
    if selected is None:
        raise typer.BadParameter(f"Variant {variant} not found")
    target = output or run_dir / "outputs" / f"variant-{variant:02d}.mp4"

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task = progress.add_task("Rendering clips", total=len(selected.items))

        def advance(completed: int, total: int) -> None:
            progress.update(task, completed=completed, total=total)

        result = render_variant(
            run_dir,
            selected,
            audio_map,
            target,
            progress=advance,
        )
    console.print(f"[bold green]Export ready:[/] {result}")


@app.command()
def benchmark(
    video: Annotated[Path, typer.Argument(help="Edited reference video.")],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Report directory.")] = None,
    analyzer: Annotated[str, typer.Option(help="auto, librosa, allin1, or beat-this.")] = "auto",
    audio_map_path: Annotated[
        Path | None,
        typer.Option(
            "--audio-map",
            help="Existing audio-map.json to reuse music analysis.",
        ),
    ] = None,
) -> None:
    """Compare reference visual cuts with music events from the same video."""
    require_media_tools()
    video = video.expanduser().resolve()
    output_dir = out or Path.cwd() / "runs" / f"benchmark-{_slug(video.stem)}"
    output_dir = output_dir.expanduser().resolve()
    if audio_map_path:
        audio_map = AudioMap.from_dict(read_json(audio_map_path.expanduser().resolve()))
    else:
        with console.status("[bold]Analyzing reference audio…"):
            audio_map = analyze_track(video, output_dir / "audio", analyzer=analyzer)
    write_json(output_dir / "audio-map.json", audio_map.to_dict())
    with console.status("[bold]Detecting visual cuts…"):
        result = benchmark_reference(video, audio_map)
    report = create_benchmark_report(output_dir, video, audio_map, result)
    summary = result["summary"]
    console.print(
        f"[green]Reference:[/] {summary['visual_cut_count']} cuts, "
        f"{summary['beat_hit_rate_120ms'] * 100:.0f}% aligned to beats within ±120 ms"
    )
    console.print(f"Report: [link=file://{report}]{report}[/link]")


@app.command()
def serve(
    run: Annotated[Path, typer.Argument(help="Project or report directory.")],
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the browser.")
    ] = True,
) -> None:
    """Serve a generated viewer over local HTTP."""
    directory = run.expanduser().resolve()
    preview = directory / "preview"
    if preview.is_dir():
        relative_url = "/preview/"
    elif (directory / "index.html").exists():
        relative_url = "/"
    else:
        raise typer.BadParameter("No preview/index.html or index.html found in this directory")
    handler = functools.partial(RangeRequestHandler, directory=str(directory))
    with RangeHTTPServer(("127.0.0.1", port), handler) as server:
        url = f"http://127.0.0.1:{port}{relative_url}"
        console.print(f"[bold green]Reel Lab:[/] {url}")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            console.print("\nServer stopped")


def _run_directory(out: Path | None, track: Path) -> Path:
    if out:
        return out.expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (Path.cwd() / "runs" / f"{stamp}-{_slug(track.stem)}").resolve()


def _slug(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in normalized.split("-") if part)[:48] or "track"
