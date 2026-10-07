"""HTTP serving helpers with byte-range support for browser video seeking."""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlsplit

from reel_lab.models import AudioMap, Variant
from reel_lab.render import ProgressCallback, render_variant
from reel_lab.system import read_json

_EXPORT_LOCKS: dict[Path, threading.Lock] = {}
_EXPORT_LOCKS_GUARD = threading.Lock()
_EXPORT_JOBS: dict[str, ExportJob] = {}
_EXPORT_JOBS_BY_OUTPUT: dict[Path, str] = {}
_EXPORT_JOBS_GUARD = threading.Lock()


@dataclass
class ExportJob:
    """Progress state for one background video export."""

    id: str
    url: str
    filename: str
    status: str = "queued"
    completed: int = 0
    total: int = 1
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the browser-facing job state."""
        stages = {
            "queued": "Queued",
            "rendering": "Rendering clips",
            "finalizing": "Assembling MP4",
            "ready": "Ready",
            "error": "Error",
        }
        if self.status == "ready":
            progress = 100
        elif self.status == "finalizing":
            progress = 94
        elif self.status == "rendering":
            progress = min(90, max(4, round(90 * self.completed / max(1, self.total))))
        else:
            progress = 0 if self.status == "error" else 2
        payload: dict[str, object] = {
            "job_id": self.id,
            "status": self.status,
            "stage": stages[self.status],
            "progress": progress,
        }
        if self.status == "ready":
            payload.update({"url": self.url, "filename": self.filename})
        if self.error:
            payload["error"] = self.error
        return payload


class RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve static files and honor a single HTTP bytes range."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/export/"):
            job_id = path.removeprefix("/api/export/")
            payload = export_job_payload(job_id)
            if payload is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Export job not found"})
            else:
                self._send_json(HTTPStatus.OK, payload)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/export":
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return
        try:
            run_name, variant_id = self._read_export_request()
            job = start_export_job(Path(self.directory), run_name, variant_id)
            payload = export_job_payload(job.id)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except FileNotFoundError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        except Exception as error:
            self.log_error("Export failed: %s", error)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
            return
        status = HTTPStatus.OK if payload and payload["status"] == "ready" else HTTPStatus.ACCEPTED
        self._send_json(status, payload or {"error": "Could not create an export job"})

    def send_head(self):
        self._active_range = None
        range_header = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not range_header or os.path.isdir(path):
            return super().send_head()
        try:
            file = open(path, "rb")  # noqa: SIM115 - the HTTP handler closes this stream
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        size = os.fstat(file.fileno()).st_size
        try:
            start, end = _parse_range(range_header, size)
        except ValueError:
            file.close()
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return None

        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(os.fstat(file.fileno()).st_mtime))
        self.end_headers()
        self._active_range = (start, end)
        return file

    def end_headers(self) -> None:
        if "Accept-Ranges" not in self._headers_buffer_text():
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile) -> None:
        active_range = self._active_range
        if active_range is None:
            with suppress(BrokenPipeError, ConnectionResetError):
                super().copyfile(source, outputfile)
            return
        start, end = active_range
        source.seek(start)
        remaining = end - start + 1
        try:
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self._active_range = None

    def _headers_buffer_text(self) -> str:
        return b"".join(getattr(self, "_headers_buffer", [])).decode("latin-1", errors="ignore")

    def _read_export_request(self) -> tuple[str, int]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 4096:
            raise ValueError("Invalid export request")
        payload = json.loads(self.rfile.read(content_length))
        run_name = payload.get("run")
        variant_id = payload.get("variant")
        if not isinstance(run_name, str) or not run_name:
            raise ValueError("Project is required")
        if isinstance(variant_id, bool) or not isinstance(variant_id, int):
            raise ValueError("Variant is required")
        return run_name, variant_id

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class RangeHTTPServer(http.server.ThreadingHTTPServer):
    """Reusable threaded server for responsive local previews."""

    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exception(), (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def start_export_job(root_dir: Path, run_name: str, variant_id: int) -> ExportJob:
    """Start or reuse a background export job."""
    run_dir, audio_map, variant, output = _load_export_context(root_dir, run_name, variant_id)
    root = root_dir.expanduser().resolve()
    output_key = output.resolve()
    relative_output = output.relative_to(root)
    with _EXPORT_JOBS_GUARD:
        existing_id = _EXPORT_JOBS_BY_OUTPUT.get(output_key)
        existing = _EXPORT_JOBS.get(existing_id or "")
        if existing and existing.status != "error":
            return existing
        job = ExportJob(
            id=uuid.uuid4().hex,
            url="/" + quote(relative_output.as_posix(), safe="/"),
            filename=f"{run_dir.name}-variant-{variant.id:02d}.mp4",
            total=max(1, len(variant.items)),
        )
        if output.exists() and output.stat().st_size > 0:
            job.status = "ready"
            job.completed = job.total
        _EXPORT_JOBS[job.id] = job
        _EXPORT_JOBS_BY_OUTPUT[output_key] = job.id
    if job.status != "ready":
        threading.Thread(
            target=_run_export_job,
            args=(job.id, run_dir, audio_map, variant, output),
            daemon=True,
            name=f"reel-export-{variant.id:02d}",
        ).start()
    return job


def export_job_payload(job_id: str) -> dict[str, object] | None:
    """Return a stable snapshot of one export job."""
    with _EXPORT_JOBS_GUARD:
        job = _EXPORT_JOBS.get(job_id)
        return job.to_dict() if job else None


def _load_export_context(
    root_dir: Path,
    run_name: str,
    variant_id: int,
) -> tuple[Path, AudioMap, Variant, Path]:
    run_dir = _resolve_run_directory(root_dir, run_name)
    audio_map = AudioMap.from_dict(read_json(run_dir / "audio-map.json"))
    variants = [Variant.from_dict(item) for item in read_json(run_dir / "variants.json")]
    variant = next((item for item in variants if item.id == variant_id), None)
    if variant is None:
        raise ValueError(f"Variant {variant_id} not found")
    output = run_dir / "outputs" / f"variant-{variant.id:02d}.mp4"
    return run_dir, audio_map, variant, output


def _render_export(
    run_dir: Path,
    audio_map: AudioMap,
    variant: Variant,
    output: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    with _export_lock(output):
        if output.exists() and output.stat().st_size > 0:
            return output
        temporary = output.with_name(f".{output.stem}.partial.mp4")
        render_variant(
            run_dir=run_dir,
            variant=variant,
            audio_map=audio_map,
            output=temporary,
            progress=progress,
        )
        temporary.replace(output)
    return output


def _run_export_job(
    job_id: str,
    run_dir: Path,
    audio_map: AudioMap,
    variant: Variant,
    output: Path,
) -> None:
    def report(completed: int, total: int) -> None:
        with _EXPORT_JOBS_GUARD:
            job = _EXPORT_JOBS[job_id]
            job.completed = completed
            job.total = max(1, total)
            job.status = "finalizing" if completed >= total else "rendering"

    try:
        with _EXPORT_JOBS_GUARD:
            _EXPORT_JOBS[job_id].status = "rendering"
        _render_export(run_dir, audio_map, variant, output, progress=report)
        with _EXPORT_JOBS_GUARD:
            job = _EXPORT_JOBS[job_id]
            job.status = "ready"
            job.completed = job.total
    except Exception as error:
        with _EXPORT_JOBS_GUARD:
            job = _EXPORT_JOBS[job_id]
            job.status = "error"
            job.error = str(error)


def _resolve_run_directory(root_dir: Path, run_name: str) -> Path:
    root = root_dir.expanduser().resolve()
    run_dir = (root / run_name).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("Project is outside the viewer directory")
    if not (run_dir / "audio-map.json").is_file() or not (run_dir / "variants.json").is_file():
        raise FileNotFoundError("Export project not found")
    return run_dir


def _export_lock(output: Path) -> threading.Lock:
    resolved = output.resolve()
    with _EXPORT_LOCKS_GUARD:
        return _EXPORT_LOCKS.setdefault(resolved, threading.Lock())


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported range")
    start_text, end_text = value[6:].split("-", 1)
    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise ValueError("invalid suffix")
        return max(0, size - length), size - 1
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("invalid range")
    return start, min(end, size - 1)
